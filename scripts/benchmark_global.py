#!/usr/bin/env python3
"""Frozen current/chunk-finalist/richer-global routing experiment; no live state.

At most five requests/case, 24 cases, 120 requests. No retries or repeat probes.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import threading
import time
import jev_choose as jev
from benchmark_jev import digest, metrics as historical_metrics, primary
from benchmark_context import secure_json

ARMS = ('current', 'global_current_winners', 'global_current_top3', 'global_richer_top3')


def gated(pick):
    return pick.name if pick and pick.name and math.isfinite(pick.confidence) and pick.confidence >= .8 else None


def prediction(result):
    return gated(result.primary) or gated(result.process) if result else None


def is_correct(row):
    if row.get('failure'):
        return False
    return row['prediction'] in row['expected'] if row['expected'] else row['prediction'] is None


def metrics(rows):
    """Failure-exclusive scores retain every attempted case in denominators."""
    result = historical_metrics(rows)
    routed = [r for r in rows if r['prediction'] and not r.get('failure')]
    correct = sum(is_correct(r) for r in rows)
    result.update(n_correct=correct, correct_including_abstentions=correct,
        positive_hits=sum(is_correct(r) for r in rows if r['expected']),
        routed=len(routed), route_precision=sum(is_correct(r) for r in routed)/len(routed) if routed else None,
        coverage=len(routed)/len(rows) if rows else 0,
        false_routes_on_no_skill=sum(bool(r['prediction']) and not r.get('failure') for r in rows if not r['expected']),
        valid_abstentions=sum(r['prediction'] is None and not r.get('failure') for r in rows),
        valid_control_abstentions=sum(r['prediction'] is None and not r.get('failure') and not r['expected'] for r in rows),
        failures=sum(bool(r.get('failure')) for r in rows))
    return result


def sanitize_answers(answers, questions):
    """Retain known option IDs and bounded numbers; reject arbitrary provider echoes."""
    if not isinstance(answers, dict):
        return {}
    def number(value):
        return value if isinstance(value, (int,float)) and not isinstance(value,bool) and math.isfinite(value) and 0 <= value <= 1 else None
    cleaned = {}
    for qid, question in questions.items():
        answer = answers.get(qid)
        if not isinstance(answer,dict):
            continue
        allowed = question.get('criteria', {})
        choice = answer.get('choice')
        probabilities = answer.get('probabilities')
        cleaned[qid] = {'type':'choice' if answer.get('type') == 'choice' else None,
                        'choice':choice if isinstance(choice,str) and choice in allowed else None,
                        'confidence':number(answer.get('confidence')),
                        'probabilities':{key:value for key,value in probabilities.items()
                            if key in allowed and number(value) is not None} if isinstance(probabilities,dict) else {}}
    return cleaned


def run(cases_path, index_path, out, max_calls=120, timeout=1.2):
    import jev_global as experimental
    cases_path, index_path, out = map(Path, (cases_path, index_path, out))
    if out.exists():
        raise ValueError('output exists; refusing replay')
    if not 0 < max_calls <= 120 or timeout != 1.2:
        raise ValueError('max calls must be 1..120; per-stage timeout must remain 1.2')
    frozen_baseline = cases_path.parent / 'baseline_chooser.py'
    if not frozen_baseline.is_file() or digest(frozen_baseline) != digest(jev.__file__):
        raise ValueError('frozen baseline_chooser.py must match current chooser before predictions')
    cases = json.loads(cases_path.read_text())
    entries = json.loads(index_path.read_text())['entries']
    if not cases or len(cases) > 24 or len(cases)*5 > max_calls:
        raise ValueError('requires 1..24 cases within five-call-per-case budget')
    if len({c['id'] for c in cases}) != len(cases) or len({(c['prompt'], c['context']) for c in cases}) != len(cases):
        raise ValueError('duplicate IDs or prompt/context pairs')
    domain_names = {e['name'] for e in entries if e.get('kind') != 'process'}
    allowed = {e['name'] for e in entries if e.get('type') not in jev.NOT_ROUTABLE and e['name'] != jev.SELF_SKILL}
    if len({e['name'] for e in entries}) != len(entries):
        raise ValueError('duplicate catalog names')
    if any(not isinstance(c['prompt'], str) or not isinstance(c['context'], str) or not set(c['expected_skills']) <= allowed for c in cases):
        raise ValueError('invalid input or labels')
    if jev.ROUTE_AT != .8 or jev.CONTEXT_MAX_WORDS != 15:
        raise ValueError('production gate must remain .8 and context cutoff 15')
    if not os.environ.get('TYPESAFE_API_KEY'):
        raise ValueError('supply provider key through runtime environment')
    out.mkdir(mode=0o700, parents=True)
    manifest = {'cases_sha256': digest(cases_path), 'index_sha256': digest(index_path),
                'chooser_sha256': digest(jev.__file__), 'frozen_baseline_sha256':digest(frozen_baseline), 'experimental_sha256': digest(experimental.__file__),
                'harness_sha256': digest(__file__), 'model': jev.MODEL, 'threshold': .8,
                'context_max_words': 15, 'per_stage_timeout': timeout,
                'n': len(cases), 'planned_max_requests': len(cases)*5, 'max_calls': max_calls,
                'retries': 0, 'repeat_probes': 0, 'cache_policy': 'fresh current-call directory per case; experimental no cache',
                'label_policy': 'frozen before predictions', 'started_at': time.time(), 'status': 'running',
                'counterbalance': 'alternate current+two counterfactual global calls bundle vs richer+global-richer bundle',
                'stochastic_limit': 'one pass per state; no same-state reproducibility estimate',
                'token_accounting': 'per-arm pipeline includes reused baseline cost; do not sum arm costs as actual requests',
                'scope': 'chooser primary/domain and candidate recall; no downstream execution or savings proof'}
    secure_json(out / 'manifest.json', manifest)
    original = (jev.CACHE, jev._post)
    provider_key = jev._key()
    env_original = {k: os.environ.get(k) for k in ('SKILL_ROUTER_JEV', 'SKILL_ROUTER_NO_LEARN')}
    attempts = [0]
    lock = threading.Lock()
    rows = []
    def capture(case_id, arm, raw_box=None):
        def post(body, key, seconds):
            with lock:
                if attempts[0] >= max_calls:
                    raise RuntimeError('provider budget exhausted')
                attempts[0] += 1
                attempt = attempts[0]
            started = time.monotonic()
            data = original[1](body, key or provider_key, seconds)
            answers = data.get('answers') if isinstance(data, dict) else None
            sanitized = sanitize_answers(answers, body['questions'])
            if raw_box is not None:
                raw_box.append(sanitized)
            with lock:
                secure_json(out / 'stages.jsonl', {'id':case_id, 'strategy':arm, 'attempt':attempt,
                            'question_ids':list(body['questions']), 'answers':sanitized,
                            'input_tokens':jev._usage(data) if isinstance(data,dict) else 0,
                            'elapsed_ms':int((time.monotonic()-started)*1000)}, append=True)
            return data
        return post
    try:
        os.environ.update(SKILL_ROUTER_JEV='1', SKILL_ROUTER_NO_LEARN='1')
        for i, case in enumerate(cases):
            common = {'id':case['id'], 'bucket':case['bucket'], 'expected':case['expected_skills'],
                      'expected_tier':case.get('expected_tier','unknown'),
                      'domain_expected':case.get('expected_domain_skills', [n for n in case['expected_skills'] if n in domain_names])}
            per_case = {}
            def baseline_bundle():
                raw = []
                jev.CACHE = out / ('current-'+str(i)) / 'cache'
                jev._post = capture(case['id'], 'current', raw)
                start = time.monotonic()
                choice = jev.choose(case['prompt'], context=case['context'], entries=entries, timeout=timeout,
                                    session_id='global-benchmark', prompt_id=case['id'])
                elapsed = int((time.monotonic()-start)*1000)
                shortlist, expanded = [], []
                baseline_failure = (jev.LAST_FAILURE or 'baseline_unavailable') if choice is None else ''
                # A response arriving after the chooser deadline is never eligible.
                # Freeze the usable snapshot only after a successful current choice.
                usable_raw = dict(raw[0]) if choice is not None and raw else None
                if choice is not None:
                    if usable_raw is None:
                        baseline_failure = 'baseline_raw_unavailable'
                    else:
                        try:
                            shortlist = experimental.current_shortlist(usable_raw, entries)
                            expanded = experimental.current_shortlist(usable_raw, entries, expand=True)
                        except ValueError:
                            shortlist, expanded = [], []
                            baseline_failure = 'baseline_shortlist_error'
                base = {**common, 'strategy':'current', 'prediction':primary(choice),
                        'domain_prediction':gated(choice.domain) if choice else None,
                        'shortlist':shortlist, 'choice':asdict(choice) if choice else None,
                        'failure':(jev.LAST_FAILURE or 'baseline_unavailable') if choice is None else '', 'elapsed_ms':elapsed,
                        'input_tokens':choice.tokens if choice else 0}
                per_case['current'] = base
                for arm, candidates in [('global_current_winners', shortlist), ('global_current_top3', expanded)]:
                    if baseline_failure:
                        per_case[arm] = {**common, 'strategy':arm, 'prediction':None,
                            'domain_prediction':None, 'shortlist':[], 'top1_shortlist':[], 'choice':None,
                            'failure':baseline_failure, 'skipped_due_to_baseline':True,
                            'elapsed_ms':elapsed, 'input_tokens':base['input_tokens']}
                        continue
                    result = experimental.choose(case['prompt'], context=case['context'], entries=entries, timeout=timeout,
                                transport=capture(case['id'],arm), shortlist=candidates, representation='current')
                    per_case[arm] = {**common, 'strategy':arm,
                            'prediction':gated(result.primary) or (gated(choice.process) if choice else None),
                            'domain_prediction':gated(result.primary), 'shortlist':candidates,
                            'top1_shortlist':shortlist, 'choice':result.to_dict(),
                            'failure':result.failure, 'elapsed_ms':elapsed+result.ms,
                            'input_tokens':base['input_tokens']+result.tokens}
            def challenger_bundle():
                result = experimental.choose(case['prompt'], context=case['context'], entries=entries, timeout=timeout,
                            transport=capture(case['id'],'global_richer_top3'))
                per_case['global_richer_top3'] = {**common, 'strategy':'global_richer_top3', 'prediction':prediction(result),
                        'domain_prediction':gated(result.primary), 'shortlist':result.shortlist,
                        'top1_shortlist':list(dict.fromkeys(c['selected_skill'] for c in result.chunks if c.get('selected_skill'))), 'choice':result.to_dict(),
                        'failure':result.failure, 'elapsed_ms':result.ms, 'input_tokens':result.tokens}
            for bundle in ((baseline_bundle,challenger_bundle) if i%2==0 else (challenger_bundle,baseline_bundle)):
                bundle()
            for arm in ARMS:
                row = per_case[arm]
                row['top1_shortlist'] = row.get('top1_shortlist', row['shortlist'])
                row['top1_shortlist_hit'] = bool(set(row['top1_shortlist']) & set(row['domain_expected'])) if row['domain_expected'] else None
                row['shortlist_hit'] = bool(set(row['shortlist']) & set(row['domain_expected'])) if row['domain_expected'] else None
                rows.append(row)
                secure_json(out / 'rows.jsonl',row,append=True)
        summary = {}
        for arm in ARMS:
            rr = [r for r in rows if r['strategy']==arm]
            positives = [r for r in rr if r['domain_expected']]
            summary[arm] = {'primary':metrics(rr), 'domain':metrics([{**r,'prediction':r['domain_prediction'],'expected':r['domain_expected']} for r in rr]),
                            'shortlist_hits':sum(r['shortlist_hit'] for r in positives), 'shortlist_positive_n':len(positives),
                            'top1_shortlist_hits':sum(r['top1_shortlist_hit'] for r in positives),
                            'no_skill_controls':metrics([r for r in rr if not r['expected']]),
                            'buckets':{b:metrics([r for r in rr if r['bucket']==b]) for b in sorted({r['bucket'] for r in rr})}}
        by = {a:{r['id']:r for r in rows if r['strategy']==a} for a in ARMS}
        summary['paired_vs_current'] = {}
        for arm in ARMS[1:]:
            pairs = [(is_correct(by[arm][k]),is_correct(v)) for k,v in by['current'].items()]
            summary['paired_vs_current'][arm] = {'treatment_only_correct':sum(a and not b for a,b in pairs),
                'current_only_correct':sum(b and not a for a,b in pairs),'both_correct':sum(a and b for a,b in pairs),'both_wrong':sum(not a and not b for a,b in pairs)}
        secure_json(out / 'summary.json',summary)
        manifest['status']='completed'
        return summary
    finally:
        jev.CACHE, jev._post = original
        for key,value in env_original.items():
            if value is None: os.environ.pop(key,None)
            else: os.environ[key]=value
        manifest.update(completed_at=time.time(), provider_attempts=attempts[0], score_rows=len(rows))
        if manifest['status']!='completed': manifest['status']='failed'
        secure_json(out / 'manifest.json',manifest)


def main():
    parser=argparse.ArgumentParser(__doc__)
    for name in ('cases','index','out'): parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--max-calls',type=int,default=120)
    args=parser.parse_args()
    try: summary=run(args.cases,args.index,args.out,args.max_calls)
    except ValueError as exc: parser.error(str(exc))
    print(json.dumps({a:summary[a]['primary'] for a in ARMS},indent=2))

if __name__=='__main__': main()
