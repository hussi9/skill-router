#!/usr/bin/env python3
"""Offline experimental two-stage global adjudication. Never installed by import."""
from __future__ import annotations
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Optional
import jev_choose as base

CHUNK_SIZE = 254


def eligible(entries):
    return [e for e in entries if isinstance(e,dict) and e.get('name')
            and e.get('type') not in base.NOT_ROUTABLE and e['name'] != base.SELF_SKILL]


def criteria(entry):
    summary = entry.get('description') or ''
    if entry.get('routing_summary'):
        summary = str(entry['routing_summary']) + ' | ' + str(summary)
    triggers = '; '.join(str(x) for x in (entry.get('use_when') or [])[:3])
    return f"{entry['name']}: {str(summary)[:600]} | Use when: {triggers[:480]}"


def build_questions(entries):
    domain=[e for e in eligible(entries) if e.get('kind')!='process']
    count=max(1, math.ceil(len(domain)/CHUNK_SIZE))
    questions={};mapping={};offset=0
    for n in range(count):
        chunk=domain[offset:offset+CHUNK_SIZE]
        qid=f'domain_{n}'
        mapping[qid]={f'd{offset+i}':e['name'] for i,e in enumerate(chunk)}
        options={k:criteria(e) for k,e in zip(mapping[qid],chunk)}
        options['none']='No listed skill fits this request.'
        questions[qid]={'type':'choice','instructions':'Which installed skill can perform the requested action? Match the work requested, not merely its subject matter. Judge intended meaning despite typos. Select none when no skill fits.', 'criteria':options}
        offset+=len(chunk)
    process=[e for e in eligible(entries) if e.get('kind')=='process']
    if len(process)>254:
        raise ValueError('process_overflow')
    if process:
        mapping['process']={f'p{i}':e['name'] for i,e in enumerate(process[:254])}
        options={k:criteria(e) for k,e in zip(mapping['process'],process)}
        options['none']='No working-method skill applies.'
        questions['process']={'type':'choice','instructions':'Which working method fits? This is diagnostic, separate from the primary domain.', 'criteria':options}
    ancillary,_=base.build_questions(entries)
    for qid in ('path','tier'):
        questions[qid]=ancillary[qid]
    return questions,mapping


def _confidence(answer):
    value=answer.get("confidence")
    return 0.0 if isinstance(value,bool) else base._conf(answer)


def _probability(value):
    # Invalid values are omitted rather than confused with a genuine zero.
    if isinstance(value,bool):
        return None
    try:
        value=float(value)
        return value if math.isfinite(value) and 0<=value<=1 else None
    except (ValueError,TypeError):
        return None


def shortlist_from_answers(answers,mapping):
    selected=[];chunks=[]
    for qid,keys in mapping.items():
        if not qid.startswith('domain_'):
            continue
        answer=answers.get(qid)
        if not isinstance(answer,dict):
            raise ValueError('malformed_stage1')
        winner=answer.get('choice')
        if not isinstance(winner,str) or (winner not in keys and winner!='none'):
            raise ValueError('malformed_stage1')
        probabilities=answer.get('probabilities')
        ranked=[]
        if isinstance(probabilities,dict):
            for key,value in probabilities.items():
                probability=_probability(value)
                if key in keys and probability is not None and probability > 0:
                    ranked.append((probability,key))
        ranked.sort(key=lambda item:(-item[0],item[1]))
        candidates=[keys[key] for _,key in ranked[:3]]
        if winner in keys and keys[winner] not in candidates:
            candidates=[keys[winner]] + candidates[:2]
        selected.extend(candidates)
        chunks.append({'question_id':qid,'selected_skill':keys.get(winner),
                       'confidence':_confidence(answer),'candidates':candidates})
    return list(dict.fromkeys(selected)),chunks


def current_shortlist(answers,entries,expand=False):
    _,mapping=base.build_questions(entries)
    if expand:
        selected,_=shortlist_from_answers(answers,mapping)
        return selected
    selected=[]
    for qid,keys in mapping.items():
        answer=answers.get(qid)
        if qid.startswith('domain_') and isinstance(answer,dict):
            choice=answer.get('choice')
            name=keys.get(choice) if isinstance(choice,str) else None
            if name:
                selected.append(name)
    return list(dict.fromkeys(selected))


@dataclass
class GlobalResult:
    primary: base.Pick = base.Pick(None,0.,'silent')
    process: base.Pick = base.Pick(None,0.,'silent')
    shortlist: list[str] = field(default_factory=list)
    top1_shortlist: list[str] = field(default_factory=list)
    chunks: list[dict] = field(default_factory=list)
    attempts: int = 0
    tokens: int = 0
    ms: int = 0
    failure: str = ''
    stages: list[dict] = field(default_factory=list)
    input: dict = field(default_factory=dict)
    model: str = 'inherit'

    def to_dict(self):
        return asdict(self)


def choose(prompt: str, context: str='', entries=None, timeout: Optional[float]=None,
           *, transport=None, shortlist=None, representation='rich') -> GlobalResult:
    """Opt-in caller API; injected transport needs no credentials. No retries/cache.

    Supplying shortlist bypasses stage1 for a one-call current-shortlist comparison.
    Each request has the same bounded wall-clock deadline; at most two calls.
    """
    started=time.monotonic()
    seconds=base.TIMEOUT_S if timeout is None else timeout
    result=GlobalResult()
    request,rmeta=base._bounded(prompt,base.PROMPT_CHARS)
    previous,cmeta=base._bounded(context,base.CONTEXT_CHARS)
    state={'request':request}
    if previous and len((prompt or '').split())<=base.CONTEXT_MAX_WORDS:
        state['previous_assistant_message']=previous
    else:
        cmeta={**cmeta,'sent_chars':0,'truncated':False}
    result.input={'request':rmeta,'context':cmeta}
    def finish(failure=''):
        result.failure=failure
        result.ms=int((time.monotonic()-started)*1000)
        return result
    if (isinstance(seconds,bool) or not isinstance(seconds,(int,float))
            or not math.isfinite(seconds) or seconds<=0):
        return finish('invalid_timeout')
    if base.ROUTE_AT != .8:
        return finish('threshold_mismatch')
    if not request:
        return finish('empty_input')
    entries=base.load_entries() if entries is None else entries
    if not entries:
        return finish('empty_index')
    key='' if transport is not None else base._key()
    if transport is None and not key:
        return finish('no_key')
    send=transport or base._post
    def call(stage,questions):
        remaining=max(0., seconds)
        deadline=time.monotonic()+remaining
        if remaining<=0:
            return None,'timeout'
        result.attempts+=1
        t0=time.monotonic()
        data=base._deadline(lambda:send({'model':base.MODEL,'state':state,'questions':questions},key,remaining),remaining)
        elapsed=int((time.monotonic()-t0)*1000)
        tokens=base._usage(data) if isinstance(data,dict) else 0
        result.tokens+=tokens
        answers=data.get('answers') if isinstance(data,dict) else None
        failure='' if isinstance(answers,dict) else ('timeout' if time.monotonic()>=deadline else 'error')
        result.stages.append({'stage':stage,'attempt':result.attempts,'input_tokens':tokens,'elapsed_ms':elapsed,'failure':failure})
        return answers,failure
    if shortlist is None:
        try:
            questions,mapping=build_questions(entries)
        except ValueError as error:
            return finish(str(error))
        answers,failure=call('candidates',questions)
        if failure:
            return finish(failure)
        try:
            result.shortlist,result.chunks=shortlist_from_answers(answers,mapping)
            result.top1_shortlist=[chunk['selected_skill'] for chunk in result.chunks if chunk['selected_skill']]
        except ValueError:
            return finish('malformed_stage1')
        if 'process' in mapping:
            result.process=base._pick(answers,['process'],mapping) or base.Pick(None,0.,'silent')
    else:
        allowed={e['name'] for e in eligible(entries) if e.get('kind')!='process'}
        result.shortlist=list(dict.fromkeys(n for n in shortlist if n in allowed))
    if not result.shortlist:
        return finish()
    # Global answer compares descriptions directly. Chunk probabilities never
    # become the final confidence, and no stage-one confidence gate drops options.
    if len(result.shortlist)>254:
        return finish('shortlist_overflow')
    by_name={e['name']:e for e in eligible(entries)}
    keys={f'g{i}':name for i,name in enumerate(result.shortlist)}
    options={key:(base._summary(by_name[name],150) if representation=='current' else criteria(by_name[name]))
             for key,name in keys.items()}
    options['none']='No shortlisted domain skill fits; general work or a process alone is not a domain match.'
    questions={'global':{'type':'choice','instructions':'Choose the single best primary domain skill for the request from this global shortlist. Compare all options together. Pick none if none fits. Judge intended meaning despite typos.', 'criteria':options}}
    answers,failure=call('global',questions)
    if failure:
        return finish(failure)
    answer=answers.get('global')
    if (not isinstance(answer,dict) or not isinstance(answer.get('choice'),str)
            or answer['choice'] not in {*keys,'none'}):
        return finish('malformed_global')
    confidence=_confidence(answer)
    result.primary=base.Pick(keys.get(answer['choice']),confidence,base.tier(confidence))
    result.stages[-1]['selected_skill']=result.primary.name
    result.stages[-1]['confidence']=confidence
    return finish()
