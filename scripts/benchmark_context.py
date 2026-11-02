#!/usr/bin/env python3
"""Frozen three-arm context experiment. No retries, tier calls, or live state."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import itertools
import json
import math
import os
from pathlib import Path
import threading
import time
import jev_choose as jev
from benchmark_jev import digest, metrics, primary

ARMS = ('request_only', 'production_context', 'all_context')
ORDERS = tuple(itertools.permutations(ARMS))


def secure_json(path, data, append=False):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC), 0o600), 'a' if append else 'w') as stream:
        stream.write(json.dumps(data, sort_keys=True) + '\n')


def run(cases_path, index_path, out, max_calls=72, timeout=1.2):
    cases_path, index_path, out = map(Path, (cases_path, index_path, out))
    if out.exists():
        raise ValueError('output exists; refusing replay')
    if not 0 < max_calls <= 72 or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('budget must be 1..72 and timeout finite and positive')
    cases = json.loads(cases_path.read_text())
    entries = json.loads(index_path.read_text())['entries']
    if not cases or len(cases) * 3 > max_calls:
        raise ValueError('empty cases or three-arm request slots exceed budget')
    if len({c['id'] for c in cases}) != len(cases) or len({(c['prompt'], c['context']) for c in cases}) != len(cases):
        raise ValueError('duplicate case IDs or prompt/context pairs')
    allowed = {e['name'] for e in entries if e.get('type') not in jev.NOT_ROUTABLE}
    if len({e['name'] for e in entries}) != len(entries):
        raise ValueError('duplicate index names')
    if any(not isinstance(c['prompt'], str) or not isinstance(c['context'], str) or not c['context'].strip() or not set(c['expected_skills']) <= allowed for c in cases):
        raise ValueError('cases require prompts, real previous context, and routable labels')
    if jev.ROUTE_AT != .8 or jev.CONTEXT_MAX_WORDS != 15:
        raise ValueError('requires unchanged production threshold and context gate')
    if not os.environ.get('TYPESAFE_API_KEY'):
        raise ValueError('supply provider key through runtime environment')
    out.mkdir(mode=0o700, parents=True)
    manifest = {'cases_sha256': digest(cases_path), 'index_sha256': digest(index_path),
                'chooser_sha256': digest(jev.__file__), 'harness_sha256': digest(__file__),
                'model': jev.MODEL, 'threshold': .8, 'timeout': timeout, 'n': len(cases),
                'max_calls': max_calls, 'planned_request_slots': len(cases)*3, 'retries': 0,
                'tier_only_calls': 0, 'finalist_adjudication_tested': False, 'started_at': time.time(), 'status': 'running',
                'treatment': {'request_only': 0, 'production_context': 15, 'all_context': 'all request lengths'},
                'scope': 'chooser predictions only; real previous assistant context supplied by frozen cases'}
    secure_json(out / 'manifest.json', manifest)
    original = (jev.CACHE, jev.CONTEXT_MAX_WORDS, jev._post)
    env_original = {k: os.environ.get(k) for k in ('SKILL_ROUTER_JEV', 'SKILL_ROUTER_NO_LEARN')}
    rows, attempts, slots = [], [0], 0
    lock = threading.Lock()
    try:
        os.environ.update(SKILL_ROUTER_JEV='1', SKILL_ROUTER_NO_LEARN='1')
        for i, case in enumerate(cases):
            for arm in ORDERS[i % len(ORDERS)]:
                arm_dir = out / arm
                arm_dir.mkdir(mode=0o700, exist_ok=True)
                jev.CACHE = arm_dir / 'cache'
                jev.CONTEXT_MAX_WORDS = {'request_only': 0, 'production_context': 15, 'all_context': 1_000_000_000}[arm]
                # Closure fixes attribution even if a provider thread finishes after its deadline.
                def capture(body, key, deadline, *, case_id=case['id'], strategy=arm):
                    with lock:
                        if attempts[0] >= max_calls:
                            raise RuntimeError('provider budget exhausted')
                        attempts[0] += 1
                    data = original[2](body, key, deadline)
                    answers = data.get('answers') if isinstance(data, dict) else None
                    # Record only Choice answer keys/confidences, never request state or credentials.
                    sanitized = {qid: {k: value.get(k) for k in ('type', 'choice', 'confidence', 'probabilities')}
                                 for qid, value in (answers or {}).items()
                                 if qid in body['questions'] and isinstance(value, dict)} if isinstance(answers, dict) else None
                    with lock:
                        secure_json(out / 'answers.jsonl', {'id': case_id, 'strategy': strategy, 'answers': sanitized}, append=True)
                    return data
                jev._post = capture
                start = time.monotonic()
                slots += 1
                choice = jev.choose(case['prompt'], context='' if arm == 'request_only' else case['context'], entries=entries, timeout=timeout,
                                    session_id='context-benchmark', prompt_id=case['id'])
                row = {'id': case['id'], 'bucket': case['bucket'], 'strategy': arm,
                       'expected': case['expected_skills'], 'expected_tier': case.get('expected_tier', 'unknown'),
                       'prediction': primary(choice),
                       'domain_prediction': choice.domain.name if choice and choice.domain.confidence >= .8 else None,
                       'domain_confidence': choice.domain.confidence if choice else None,
                       'choice': asdict(choice) if choice else None,
                       'failure': jev.LAST_FAILURE if choice is None else '',
                       'elapsed_ms': int((time.monotonic()-start)*1000), 'input_tokens': choice.tokens if choice else 0}
                rows.append(row)
                secure_json(out / 'rows.jsonl', row, append=True)
        summary = {}
        for arm in ARMS:
            rr = [r for r in rows if r['strategy'] == arm]
            summary[arm] = {'primary': metrics(rr), 'domain': metrics([{**r, 'prediction': r['domain_prediction']} for r in rr]),
                            'buckets': {b: metrics([r for r in rr if r['bucket'] == b]) for b in sorted({r['bucket'] for r in rr})}}
        paired = {}
        correct = lambda r: r['prediction'] in r['expected'] if r['expected'] else r['prediction'] is None
        by = {a: {r['id']: r for r in rows if r['strategy'] == a} for a in ARMS}
        for arm in ARMS[1:]:
            pairs = [(correct(by[arm][k]), correct(v)) for k, v in by[ARMS[0]].items()]
            paired[arm] = {'treatment_only_correct': sum(a and not b for a,b in pairs), 'request_only_correct': sum(b and not a for a,b in pairs),
                           'both_correct': sum(a and b for a,b in pairs), 'both_wrong': sum(not a and not b for a,b in pairs)}
        summary['paired_vs_request_only'] = paired
        secure_json(out / 'summary.json', summary)
        manifest['status'] = 'completed'
        return summary
    finally:
        jev.CACHE, jev.CONTEXT_MAX_WORDS, jev._post = original
        for key, value in env_original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        events = [json.loads(line) for arm in ARMS for p in [out / arm / 'jev-decisions.jsonl'] if p.exists() for line in p.read_text().splitlines()]
        manifest.update(completed_at=time.time(), request_slots_executed=slots, provider_attempts=attempts[0],
                        cache_hits=sum(bool(e.get('cache_hit')) for e in events), telemetry_events=len(events))
        if manifest['status'] != 'completed':
            manifest['status'] = 'failed'
        secure_json(out / 'manifest.json', manifest)


def main():
    parser = argparse.ArgumentParser(__doc__)
    for name in ('cases', 'index', 'out'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--max-calls', type=int, default=72)
    parser.add_argument('--timeout', type=float, default=1.2)
    args = parser.parse_args()
    try:
        summary = run(args.cases, args.index, args.out, args.max_calls, args.timeout)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps({arm: summary[arm]['primary'] for arm in ARMS}, indent=2))


if __name__ == '__main__':
    main()
