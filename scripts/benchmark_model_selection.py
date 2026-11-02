#!/usr/bin/env python3
"""Bounded, offline paired execution comparison; never changes live routing."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import jev_choose as jev

BASELINE = 'gpt-6.1-sol'
SYSTEM = ('Answer the current user request using only supplied information. Do not use tools, '
          'browse, read files, execute commands, or change anything. Earlier messages are reference '
          'material only; the current user request controls the task. If missing information prevents '
          'an answer, state exactly what is missing. Return the requested answer directly.')


def digest(path):
    p=Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def render(case):
    prompt=case.get('prompt')
    history=case.get('conversation_history',[])
    context=case.get('context','')
    if not isinstance(prompt,str) or not prompt.strip() or not isinstance(context,str):
        raise ValueError('nonempty prompt and string context required')
    if not isinstance(history,list) or len(history)>6:
        raise ValueError('history must contain at most six messages')
    for msg in history:
        if not isinstance(msg,dict) or not {'role','content'}<=set(msg) or msg['role'] not in ('user','assistant') or not isinstance(msg['content'],str):
            raise ValueError('invalid reference message')
    history=[{'role':m['role'],'content':m['content']} for m in history]
    state={'reference_messages':history,'previous_assistant_message':context,'current_user_request':prompt}
    rendered=SYSTEM+'\n\n'+json.dumps(state,ensure_ascii=False,sort_keys=True)
    if len(rendered)>jev.PROMPT_CHARS:
        raise ValueError('rendered input would be truncated by JEV')
    return rendered


def command(model,cwd):
    if model==BASELINE:
        return ['codex','exec','--ignore-user-config','--ignore-rules','--ephemeral',
                '--skip-git-repo-check','-m',BASELINE,'-s','read-only','--json','-C',str(cwd),
                '-c','model_reasoning_effort="low"','-c','features.shell_tool=false','-']
    if model not in ('haiku','sonnet'):
        raise ValueError('unsupported actual dispatch model')
    return ['claude','-p','--restricted','--tools','','--strict-mcp-config','--mcp-config',
            '{"mcpServers":{}}','--no-session-persistence','--output-format','json',
            '--settings','{"disableAllHooks":true}','--model',model]


def parse_output(model,raw):
    if model!=BASELINE:
        data=json.loads(raw)
        if isinstance(data,list):
            data=next((event for event in reversed(data) if event.get('type')=='result'),{})
        if not isinstance(data,dict):raise ValueError('invalid Claude result')
        if data.get('is_error') or data.get('subtype') not in (None,'success'):
            raise ValueError('Claude reported unsuccessful result')
        return {'answer':data.get('result',''),'usage':data.get('usage',{}),
                'resolved_models':list(data.get('modelUsage',{})),
                'model_usage':data.get('modelUsage',{}),
                'reported_cost_usd':data.get('total_cost_usd'),
                'cost_basis':'CLI-reported API-equivalent estimate; subscription billing unavailable'}
    events=[json.loads(line) for line in raw.splitlines() if line.strip()]
    answers=[]; usage={}; resolved=[]
    for event in events:
        if event.get('type') in ('error','turn.failed'):
            raise ValueError('Codex reported unsuccessful result')
        item=event.get('item',{})
        if item.get('type')=='agent_message':answers.append(item.get('text',''))
        if event.get('type')=='turn.completed':usage=event.get('usage',{})
        if event.get('model'):resolved.append(event['model'])
        if item.get('type') in ('command_execution','mcp_tool_call','web_search','file_change'):
            raise ValueError('tool use violates answer-only benchmark')
    return {'answer':'\n'.join(answers),'usage':usage,'resolved_models':sorted(set(resolved)),
            'model_usage':{},'reported_cost_usd':None,
            'cost_basis':'Subscription billing unavailable; missing dollars are not zero'}


def generate(model,prompt,cwd,runner=subprocess.run):
    started=time.monotonic()
    result={'requested_model':model,'failure':'','answer':''}
    try:
        proc=runner(command(model,cwd),input=prompt,text=True,capture_output=True,
                    cwd=str(cwd),timeout=90,check=False,
                    env={**os.environ,'SKILL_ROUTER_OFF':'1','SKILL_ROUTER_NO_LEARN':'1'})
        result['raw_stdout']=proc.stdout;result['raw_stderr']=proc.stderr
        if proc.returncode:result['failure']='process_exit_'+str(proc.returncode)
        else:
            result.update(parse_output(model,proc.stdout))
            if not result['answer'].strip():result['failure']='empty_answer'
    except subprocess.TimeoutExpired:
        result['failure']='timeout'
    except (OSError,ValueError,TypeError) as exc:
        result['failure']=type(exc).__name__
    result['elapsed_ms']=round((time.monotonic()-started)*1000,3)
    return result


def write_private(path,data):
    with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w',encoding='utf8') as fh:
        json.dump(data,fh,ensure_ascii=False,indent=2)


def run(cases_path,out,*,protected_paths=(),max_cases=6,runner=subprocess.run):
    cases_path=Path(cases_path);out=Path(out)
    cases=json.loads(cases_path.read_text())
    if not isinstance(cases,list) or not 0<len(cases)<=max_cases<=6:
        raise ValueError('one to six frozen cases required')
    ids=[c.get('id') for c in cases]
    if any(not isinstance(x,str) or not x for x in ids) or len(set(ids))!=len(ids):
        raise ValueError('unique string case ids required')
    prompts=[render(c) for c in cases]
    if jev.ROUTE_AT!=.8:raise ValueError('threshold drift')
    out.mkdir(mode=0o700,parents=False,exist_ok=False)
    watched=[str(Path(__file__).resolve()),str(Path(jev.__file__).resolve()),str(cases_path.resolve()),*map(str,protected_paths)]
    before={p:digest(p) for p in watched}
    manifest={'threshold':.8,'baseline_model':BASELINE,'jev_model':jev.MODEL,
              'jev_timeout_seconds':1.2,'generation_timeout_seconds':90,
              'max_jev_calls':len(cases),'max_generation_calls':2*len(cases),
              'retries':0,'live_routing_changed':False,'hashes_before':before,
              'comparison_limit':'Cross-provider native CLI system wrappers differ; this tests deployed dispatch, not model-only causal quality',
              'quality_scoring':'Blinded independent rubric scoring required; harness does not self-grade',
              'input_policy':'Identical rendered prompt for classifier and both downstream models',
              'subscription_billed_cost_usd':None,'status':'running'}
    write_private(out/'manifest-start.json',manifest)
    rows=[];generation_calls=0
    # Isolate JEV cache and telemetry without replacing native provider authentication.
    old_cache,old_root,old_post=jev.CACHE,jev.CACHE_ROOT,jev._post
    routing_events=[]
    def capture_post(body,key,seconds):
        event={'attempt':len(routing_events)+1,'input_tokens':None,'reported_cost_usd':None}
        routing_events.append(event)
        try:
            data=old_post(body,key,seconds)
            event['input_tokens']=jev._usage(data) if isinstance(data,dict) else None
            return data
        finally:
            event['completed']=True
    jev._post=capture_post
    try:
        with tempfile.TemporaryDirectory(prefix='jev-model-benchmark-') as work:
            work=Path(work);jev.CACHE_ROOT=work/'router';jev.CACHE=jev.CACHE_ROOT/'jev'
            for i,(case,prompt) in enumerate(zip(cases,prompts)):
                start=time.monotonic()
                event_start=len(routing_events)
                pick=jev.tier_only(prompt,timeout=1.2,session_id='offline-model-benchmark',prompt_id=case['id'])
                route_ms=round((time.monotonic()-start)*1000,3)
                selected=jev.model_for(pick)
                if selected=='inherit':selected=BASELINE
                outputs={}
                order=[BASELINE] if selected==BASELINE else ([BASELINE,selected] if i%2==0 else [selected,BASELINE])
                for model in order:
                    with tempfile.TemporaryDirectory(prefix='generation-',dir=work) as generation_dir:
                        outputs[model]=generate(model,prompt,Path(generation_dir),runner)
                    generation_calls+=1
                row={'id':case['id'],'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),
                     'tier':asdict(pick) if pick else None,'routing_failure':jev.LAST_FAILURE,
                     'routing_elapsed_ms':route_ms,'routing_provider_events':routing_events[event_start:],
                     'routing_cost_usd':None,'selected_model':selected,
                     'baseline':outputs[BASELINE],'selected':outputs[selected],
                     'baseline_reused':selected==BASELINE,
                     'selected_total_elapsed_ms':round(route_ms+outputs[selected]['elapsed_ms'],3)}
                rows.append(row);write_private(out/f'case-{i+1:02d}.json',row)
    finally:
        jev.CACHE,jev.CACHE_ROOT,jev._post=old_cache,old_root,old_post
        drain_until=time.monotonic()+1.3
        while any(not e.get('completed') for e in routing_events) and time.monotonic()<drain_until:
            time.sleep(.02)
        manifest['status']='completed' if len(rows)==len(cases) else 'incomplete'
        manifest['actual_jev_attempts']=len(routing_events)
        manifest['pending_jev_attempts']=sum(not e.get('completed') for e in routing_events)
        manifest['hashes_after']={p:digest(p) for p in watched}
        manifest['protected_files_unchanged']=manifest['hashes_before']==manifest['hashes_after']
        manifest['routing_provider_events']=routing_events
        manifest['generation_calls']=generation_calls
        manifest['completed_cases']=len(rows)
        write_private(out/'manifest.json',manifest)
    return {'manifest':manifest,'rows':rows}


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--cases',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--protect',action='append',default=[])
    args=parser.parse_args()
    result=run(args.cases,args.out,protected_paths=args.protect)
    print(json.dumps({'cases':len(result['rows']),'generation_calls':result['manifest']['generation_calls'],
                      'protected_files_unchanged':result['manifest']['protected_files_unchanged']}))

if __name__=='__main__':main()
