"""Frozen four-classifier/three-generation offline benchmark; never installs routing."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import benchmark_model_selection as pilot
import jev_choose as jev
import jev_model_repair as repair
import jev_scope as scope


def run(cases_path,out,*,protected_paths=(),runner=subprocess.run,transport=None):
    cases_path=Path(cases_path); out=Path(out)
    cases=json.loads(cases_path.read_text())
    if not isinstance(cases,list) or not 1<=len(cases)<=6: raise ValueError('one to six cases required')
    ids=[c.get('id') for c in cases]
    if any(not isinstance(i,str) or not i for i in ids) or len(set(ids))!=len(ids): raise ValueError('unique ids required')
    old=[pilot.render(c) for c in cases]; fixed=[repair.render_fixed(c) for c in cases]; scope_fixed=[scope.render_fixed(c) for c in cases]
    if jev.ROUTE_AT!=.8: raise ValueError('threshold drift')
    watched=[str(Path(p).resolve()) for p in [__file__,pilot.__file__,repair.__file__,scope.__file__,jev.__file__,cases_path,*protected_paths]]
    hashes={p:pilot.digest(p) for p in watched}
    out.mkdir(mode=0o700,parents=False,exist_ok=False)
    manifest={'status':'running','threshold':.8,'jev_model':jev.MODEL,'jev_timeout_seconds':1.2,
      'max_jev_calls':4*len(cases),'max_generation_calls':3*len(cases),'retries':0,
      'hashes_before':hashes,'live_routing_changed':False,'candidate_policy':'Haiku on every task is diagnostic counterfactual only, even heavy; Sonnet selections remain unmeasured',
      'classifier_arms':['legacy_wrapped','prior_structured','scope_criteria','task_focus'],
      'generation_arms':['baseline_legacy','haiku_prior_fixed','haiku_scope_fixed'],
      'causal_limits':'Compare scope_criteria and task_focus separately with prior_structured: one changes criteria only, the other instructions only. Haiku generation changes only one correction rule. Current GPT retains legacy framing. Native CLI wrappers differ.',
      'billing':'API-equivalent estimates, not actual subscription charges; JEV dollars unavailable'}
    pilot.write_private(out/'manifest-start.json',manifest)
    rows=[]; events=[]; generation_calls=0; lock=threading.Lock()
    active={'case_id':None,'arm':None}
    old_post,old_cache,old_root=jev._post,jev.CACHE,jev.CACHE_ROOT
    def capture(body,key,seconds):
        with lock:
            if len(events)>=manifest['max_jev_calls']: raise ValueError('provider call cap')
            event={**active,'attempt':len(events)+1,'completed':False,'input_tokens':None,
              'request_sha256':__import__('hashlib').sha256(json.dumps(body,sort_keys=True).encode()).hexdigest(),
              'request_bytes':len(json.dumps(body).encode())}; events.append(event)
        try:
            data=(transport or old_post)(body,key or ('' if transport else jev._key()),seconds)
            event['input_tokens']=jev._usage(data) if isinstance(data,dict) else None
            return data
        finally: event['completed']=True
    jev._post=capture
    try:
        with tempfile.TemporaryDirectory(prefix='scope-benchmark-') as work:
            work=Path(work); jev.CACHE_ROOT=work/'router'; jev.CACHE=work/'router'/'jev'
            for n,case in enumerate(cases):
                classifiers={}
                order=['legacy_wrapped','prior_structured','scope_criteria','task_focus']
                order=order[n%4:]+order[:n%4]
                for arm in order:
                    active.update(case_id=case['id'],arm=arm)
                    if arm=='legacy_wrapped':
                        start=time.monotonic()
                        pick=jev.tier_only(old[n],timeout=1.2,session_id='offline-scope-benchmark',prompt_id=case['id'])
                        model=jev.model_for(pick)
                        classifiers[arm]={'pick':asdict(pick) if pick else None,'selected_model':pilot.BASELINE if model=='inherit' else model,'failure':jev.LAST_FAILURE,'elapsed_ms':round((time.monotonic()-start)*1000,3)}
                    else: classifiers[arm]=scope.classify(case,arm=arm,transport=capture)
                candidate='haiku'
                specs={'baseline_legacy':(pilot.BASELINE,old[n]),'haiku_prior_fixed':(candidate,fixed[n]),'haiku_scope_fixed':(candidate,scope_fixed[n])}
                generation={}; order=list(specs); order=order[n%3:]+order[:n%3]
                for arm in order:
                    if generation_calls>=manifest['max_generation_calls']: raise ValueError('generation cap')
                    model,prompt=specs[arm]
                    with tempfile.TemporaryDirectory(prefix='generation-',dir=work) as cwd:
                        generation[arm]=pilot.generate(model,prompt,Path(cwd),runner)
                    generation_calls+=1
                policies={}
                for arm,cl in classifiers.items():
                    selected=cl['selected_model']
                    if selected==pilot.BASELINE: output='baseline_legacy'
                    elif selected==candidate and arm!='legacy_wrapped': output='haiku_prior_fixed'
                    else: output=None
                    policies[arm]={'selected_model':selected,'generation_arm':output,
                      'quality_evaluable':output is not None and not generation[output].get('failure'),'total_elapsed_ms':round(cl['elapsed_ms']+generation[output]['elapsed_ms'],3) if output else None,
                      'limitation':'' if output else 'Selected model and prompt combination not generated; no quality/cost imputation'}
                row={'id':case['id'],'candidate_model':candidate,'classifiers':classifiers,'generation':generation,'policies':policies}
                rows.append(row); pilot.write_private(out/f'case-{n+1:02d}.json',row)
    finally:
        jev._post,jev.CACHE,jev.CACHE_ROOT=old_post,old_cache,old_root
        until=time.monotonic()+1.3
        while any(not e['completed'] for e in events) and time.monotonic()<until: time.sleep(.02)
        manifest.update(status='completed' if len(rows)==len(cases) else 'incomplete',actual_jev_calls=len(events),generation_calls=generation_calls,pending_jev_calls=sum(not e['completed'] for e in events),routing_provider_events=events,hashes_after={p:pilot.digest(p) for p in watched})
        manifest['protected_files_unchanged']=hashes==manifest['hashes_after']
        pilot.write_private(out/'manifest.json',manifest)
    return {'manifest':manifest,'rows':rows}

if __name__=='__main__':
    p=argparse.ArgumentParser(__doc__);p.add_argument('--cases',required=True,type=Path);p.add_argument('--out',required=True,type=Path);p.add_argument('--protect',action='append',default=[])
    a=p.parse_args();r=run(a.cases,a.out,protected_paths=a.protect)
    print(json.dumps({'completed_cases':len(r['rows']),'jev_calls':r['manifest']['actual_jev_calls'],'generation_calls':r['manifest']['generation_calls']}))
