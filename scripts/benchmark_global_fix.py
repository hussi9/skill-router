#!/usr/bin/env python3
"""Frozen current/global-choice/independent-applicability experiment; no live state.

At most three requests/case, 24 cases, 72 requests. No retries or repeat probes.
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
from benchmark_jev import digest, primary
from benchmark_context import secure_json

ARMS = ('current', 'global_current_top3', 'global_applicability')


from benchmark_global import gated, is_correct, metrics, sanitize_answers as sanitize_choices


def sanitize_answers(answers, questions):
    """Allowlist known choice IDs or finite noul probabilities, never provider text."""
    cleaned = sanitize_choices(answers, questions)
    if isinstance(answers, dict):
        for qid in questions:
            answer = answers.get(qid)
            if isinstance(answer, dict) and answer.get('type') == 'noul':
                value = answer.get('noul')
                valid = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1
                cleaned[qid] = {'type':'noul', 'noul':value if valid else None}
    return cleaned


def selector_prediction(result, baseline_process, arm, gated_arms=('global_applicability',), no_fallback_arms=()):
    fallback = gated(baseline_process)
    if arm in gated_arms:
        gate = result.stages[-1].get('needs_specialist') if result.stages else None
        valid = isinstance(gate, (int, float)) and not isinstance(gate, bool) and math.isfinite(gate) and gate >= .8
        if result.failure or not valid:
            return None
    return gated(result.primary) or (None if arm in no_fallback_arms else fallback)


def run(cases_path, index_path, out, max_calls=72, timeout=1.2, *, selectors=None,
        gated_arms=('global_applicability',), dependency_paths=(),
        process_candidate_arms=(), no_fallback_arms=(), shortlist_builders=None):
    import jev_global as experimental
    import jev_applicability as challenger
    default_design = selectors is None
    if selectors is None:
        selectors = {'global_current_top3': (experimental, {'representation': 'current'}),
                     'global_applicability': (challenger, {})}
    if (len(selectors) != 2 or 'current' in selectors
            or not set(gated_arms) <= set(selectors)
            or not set(process_candidate_arms) <= set(selectors)
            or not set(no_fallback_arms) <= set(selectors)):
        raise ValueError('requires two trusted selectors and known gated arms')
    shortlist_builders = shortlist_builders or {}
    if not set(shortlist_builders) <= set(selectors):
        raise ValueError('unknown shortlist builder arm')
    arms = ('current', *selectors)
    cases_path, index_path, out = map(Path, (cases_path, index_path, out))
    if out.exists():
        raise ValueError('output exists; refusing replay')
    if not 0 < max_calls <= 72 or timeout != 1.2:
        raise ValueError('max calls must be 1..72; per-stage timeout must remain 1.2')
    frozen_baseline = cases_path.parent / 'baseline_chooser.py'
    if not frozen_baseline.is_file() or digest(frozen_baseline) != digest(jev.__file__):
        raise ValueError('frozen baseline_chooser.py must match current chooser before predictions')
    cases = json.loads(cases_path.read_text())
    entries = json.loads(index_path.read_text())['entries']
    if not cases or len(cases) > 24 or len(cases)*3 > max_calls:
        raise ValueError('requires 1..24 cases within three-call-per-case budget')
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
                'chooser_sha256': digest(jev.__file__), 'frozen_baseline_sha256':digest(frozen_baseline), 'experimental_sha256': digest(experimental.__file__), 'applicability_sha256':digest(challenger.__file__),
                'harness_sha256': digest(__file__), 'model': jev.MODEL, 'threshold': .8,
                'context_max_words': 15, 'per_stage_timeout': timeout,
                'n': len(cases), 'planned_max_requests': len(cases)*3, 'max_calls': max_calls,
                'retries': 0, 'repeat_probes': 0, 'cache_policy': 'fresh current-call directory per case; experimental no cache',
                'label_policy': 'frozen before predictions', 'started_at': time.time(), 'status': 'running',
                'counterbalance': 'baseline first; alternate old global selector vs independent applicability selector',
                'stochastic_limit': 'one pass per state; no same-state reproducibility estimate',
                'token_accounting': 'per-arm pipeline includes reused baseline cost; do not sum arm costs as actual requests',
                'scope': 'chooser primary/domain and candidate recall; no downstream execution or savings proof',
                'primary_policy':'old selector retains baseline process fallback; new selector permits it only after valid needs_specialist >= .8 and no failure'}
    if not default_design:
        manifest['counterbalance'] = 'baseline first; alternate trusted selector evaluation order'
        manifest['primary_policy'] = 'domain or baseline process fallback; gated arms require needs_specialist >= .8 and no failure'
    manifest['arms'] = list(arms)
    manifest['shortlist_builders'] = {arm: {'module': builder.__module__, 'name': builder.__name__} for arm, builder in shortlist_builders.items()}
    manifest['process_candidate_arms'] = list(process_candidate_arms)
    manifest['no_fallback_arms'] = list(no_fallback_arms)
    if no_fallback_arms:
        manifest['primary_policy'] += '; no_fallback_arms use only their independently selected primary, including validated process candidates'
    manifest['selector_sources'] = {arm: {'path':str(Path(module.__file__).resolve()),
        'sha256':digest(module.__file__), 'kwargs':kwargs, 'requires_gate':arm in gated_arms}
        for arm,(module,kwargs) in selectors.items()}
    shared_paths = [__file__, jev.__file__, experimental.__file__, challenger.__file__,
                    Path(__file__).with_name('benchmark_global.py'),
                    Path(__file__).with_name('benchmark_context.py'),
                    Path(__file__).with_name('benchmark_jev.py'), *dependency_paths]
    manifest['dependency_sources'] = {str(Path(path).resolve()):digest(path) for path in shared_paths}
    secure_json(out / 'manifest.json', manifest)
    original = (jev.CACHE, jev._post)
    provider_key = jev._key()
    env_original = {k: os.environ.get(k) for k in ('SKILL_ROUTER_JEV', 'SKILL_ROUTER_NO_LEARN')}
    attempts = [0]
    lock = threading.Lock()
    rows = []
    inflight = []
    def capture(case_id, arm, raw_box=None):
        def record_post(body, key, seconds):
            with lock:
                if attempts[0] >= max_calls:
                    raise RuntimeError('provider budget exhausted')
                attempts[0] += 1
                attempt = attempts[0]
            started = time.monotonic()
            try:
                data = original[1](body, key or provider_key, seconds)
            except Exception as exc:
                with lock:
                    secure_json(out / 'stages.jsonl', {'id':case_id, 'strategy':arm, 'attempt':attempt,
                        'request_bytes':len(json.dumps(body).encode('utf-8')),
                        'question_count':len(body['questions']), 'question_ids':list(body['questions']), 'answers':{}, 'input_tokens':0,
                        'failure':type(exc).__name__, 'elapsed_ms':int((time.monotonic()-started)*1000)}, append=True)
                raise
            answers = data.get('answers') if isinstance(data, dict) else None
            sanitized = sanitize_answers(answers, body['questions'])
            if raw_box is not None:
                raw_box.append(sanitized)
            with lock:
                secure_json(out / 'stages.jsonl', {'id':case_id, 'strategy':arm, 'attempt':attempt,
                            'request_bytes':len(json.dumps(body).encode('utf-8')),
                        'question_count':len(body['questions']), 'question_ids':list(body['questions']), 'answers':sanitized,
                            'input_tokens':jev._usage(data) if isinstance(data,dict) else 0,
                            'elapsed_ms':int((time.monotonic()-started)*1000)}, append=True)
            return data
        def post(body, key, seconds):
            done = threading.Event()
            with lock:
                inflight.append(done)
            try:
                return record_post(body, key, seconds)
            finally:
                done.set()
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
                for arm in (arms[1:] if i%2==0 else tuple(reversed(arms[1:]))):
                    candidates = expanded
                    if baseline_failure:
                        per_case[arm] = {**common, 'strategy':arm, 'prediction':None,
                            'domain_prediction':None, 'shortlist':[], 'top1_shortlist':[], 'choice':None,
                            'failure':baseline_failure, 'skipped_due_to_baseline':True,
                            'elapsed_ms':elapsed, 'input_tokens':base['input_tokens']}
                        continue
                    if arm in shortlist_builders:
                        candidates = shortlist_builders[arm](case['prompt'], case['context'], entries, index_path, expanded)
                    selector, extra = selectors[arm]
                    extra = dict(extra)
                    if arm in process_candidate_arms:
                        extra['process_candidate'] = gated(choice.process) if choice else None
                    result = selector.choose(case['prompt'], context=case['context'], entries=entries, timeout=timeout,
                                transport=capture(case['id'],arm), shortlist=candidates, **extra)
                    per_case[arm] = {**common, 'strategy':arm,
                            'prediction':selector_prediction(result, choice.process if choice else None, arm, gated_arms, no_fallback_arms),
                            'domain_prediction':gated(result.primary) if result.primary and result.primary.name in domain_names else None, 'shortlist':candidates,
                            'top1_shortlist':shortlist, 'choice':result.to_dict(),
                            'failure':result.failure, 'elapsed_ms':elapsed+result.ms,
                            'input_tokens':base['input_tokens']+result.tokens}
            baseline_bundle()
            for arm in arms:
                row = per_case[arm]
                row['top1_shortlist'] = row.get('top1_shortlist', row['shortlist'])
                row['top1_shortlist_hit'] = bool(set(row['top1_shortlist']) & set(row['domain_expected'])) if row['domain_expected'] else None
                row['shortlist_hit'] = bool(set(row['shortlist']) & set(row['domain_expected'])) if row['domain_expected'] else None
                rows.append(row)
                secure_json(out / 'rows.jsonl',row,append=True)
        summary = {}
        for arm in arms:
            rr = [r for r in rows if r['strategy']==arm]
            positives = [r for r in rr if r['domain_expected']]
            summary[arm] = {'primary':metrics(rr), 'domain':metrics([{**r,'prediction':r['domain_prediction'],'expected':r['domain_expected']} for r in rr]),
                            'shortlist_hits':sum(r['shortlist_hit'] for r in positives), 'shortlist_positive_n':len(positives),
                            'top1_shortlist_hits':sum(r['top1_shortlist_hit'] for r in positives),
                            'no_skill_controls':metrics([r for r in rr if not r['expected']]),
                            'buckets':{b:metrics([r for r in rr if r['bucket']==b]) for b in sorted({r['bucket'] for r in rr})}}
        by = {a:{r['id']:r for r in rows if r['strategy']==a} for a in arms}
        summary['paired_vs_current'] = {}
        for arm in arms[1:]:
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
        # Drain late transport accounting only; decisions are already frozen.
        drain_deadline = time.monotonic() + 5
        for done in list(inflight):
            done.wait(max(0, drain_deadline-time.monotonic()))
        manifest.update(completed_at=time.time(), provider_attempts=attempts[0], score_rows=len(rows),
                        pending_transport_calls=sum(not done.is_set() for done in inflight),
                        ledger_drain_limit_seconds=5)
        stage_path = out / 'stages.jsonl'
        stages = [json.loads(line) for line in stage_path.read_text().splitlines()] if stage_path.exists() else []
        manifest.update(recorded_stages=len(stages), actual_input_tokens=sum(s['input_tokens'] for s in stages),
                        provider_elapsed_ms=sum(s['elapsed_ms'] for s in stages),
                        token_limit='usage for completed responses only; late in-flight stages may arrive after manifest')
        if manifest['status']!='completed': manifest['status']='failed'
        secure_json(out / 'manifest.json',manifest)


def main():
    parser=argparse.ArgumentParser(__doc__)
    for name in ('cases','index','out'): parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--max-calls',type=int,default=72)
    args=parser.parse_args()
    try: summary=run(args.cases,args.index,args.out,args.max_calls)
    except ValueError as exc: parser.error(str(exc))
    print(json.dumps({a:summary[a]['primary'] for a in ARMS},indent=2))

if __name__=='__main__': main()
