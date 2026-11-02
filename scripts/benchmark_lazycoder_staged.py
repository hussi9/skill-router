"""Offline current/staged/staged-with-recipe comparison; never changes live routing."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile

import benchmark_lazycoder as base
import lazycoder_connector as connector
import lazycoder_stages as stages
import lazycoder_artifacts as artifacts

ARMS = ('current_claude', 'staged_without_lazycoder', 'staged_with_lazycoder')


def canonical(case):
    result = dict(case)
    if 'current_request' in case:
        history = list(case.get('conversation', []))
        if history and history[-1].get('content') == case['current_request']:
            history.pop()
        result.update(prompt=case['current_request'], conversation_history=history)
    base.render(result)  # Validate source before any inference or output creation.
    return result


def run(cases_path, manifest_path, out, *, protected_paths=(), generator=base.generate, deadline_seconds=120):
    deadline_seconds = base._deadline(deadline_seconds)
    cases_path, manifest_path, out = map(Path, (cases_path, manifest_path, out))
    raw = json.loads(cases_path.read_text())
    cases = raw.get('cases') if isinstance(raw, dict) else raw
    if not isinstance(cases, list) or not 1 <= len(cases) <= 2:
        raise ValueError('one or two frozen cases required')
    ids = [c.get('id') for c in cases]
    if any(not isinstance(i, str) or not re.fullmatch('[a-z0-9]+(?:-[a-z0-9]+)*', i) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('unique safe case IDs required')
    cases = [canonical(c) for c in cases]
    manifest = json.loads(manifest_path.read_text())
    contracts = [connector.render_contract(manifest, c['recipe_id'], c.get('inputs'), preview=True) for c in cases]
    for c in cases:
        expected = {'approval': 'summarize-long-thing', 'landing': 'landing-page-no-code'}
        if c.get('workflow') not in expected:
            raise ValueError('workflow must be approval or landing')
        if c['recipe_id'] != expected[c['workflow']]:
            raise ValueError('workflow and recipe mismatch')
    watched = [Path(__file__), Path(base.__file__), Path(stages.__file__), Path(connector.__file__), Path(artifacts.__file__),
               cases_path, manifest_path, *map(Path, protected_paths)]
    before = {str(p.resolve()): base.digest(p) for p in watched}
    record = {'status': 'running', 'model': base.MODEL, 'threshold': .8, 'arms': ARMS,
              'max_generation_calls': sum(1 + 2 * (5 if c['workflow'] == 'landing' else 2) for c in cases),
              'generation_calls': 0, 'retries': 0, 'deadline_seconds': deadline_seconds,
              'per_call_estimated_budget_usd': .8, 'live_routing_changed': False,
              'selection': 'oracle recipe IDs; JEV selection not measured',
              'cost_basis': 'CLI API-equivalent estimate, not subscription charges',
              'comparison': 'current versus staged measures workflow; matched staged arms isolate recipe contribution',
              'hashes_before': before}
    out.mkdir(mode=0o700, exist_ok=False)
    base.write(out / 'manifest-start.json', record)
    try:
        for index, (case, contract) in enumerate(zip(cases, contracts)):
            order = ARMS[index % 3:] + ARMS[:index % 3]
            for arm in order:
                # Persist every receipt immediately, including intermediate failures.
                call_count = 0
                arm_cap = 1 if arm == 'current_claude' else (5 if case['workflow'] == 'landing' else 2)
                def capture(model, prompt, cwd, *, deadline_seconds):
                    nonlocal call_count
                    if call_count >= arm_cap or record['generation_calls'] >= record['max_generation_calls']:
                        raise RuntimeError('generation call cap reached')
                    call_count += 1
                    record['generation_calls'] += 1
                    base.write(out / f'{case["id"]}-{arm}-attempt-{call_count}.json',
                               {'status': 'started', 'model': model, 'deadline_seconds': deadline_seconds,
                                'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()})
                    receipt = generator(model, prompt, cwd, deadline_seconds=deadline_seconds)
                    receipt = dict(receipt, prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest())
                    base.write(out / f'{case["id"]}-{arm}-step-{call_count}.json', receipt)
                    return receipt
                with tempfile.TemporaryDirectory(prefix='lazycoder-staged-') as cwd:
                    if arm == 'current_claude':
                        result = capture(base.MODEL, base.render(case), cwd, deadline_seconds=deadline_seconds)
                        result['generation_calls'] = 1
                    else:
                        result = stages.workflow(case, contract['prompt'] if arm == 'staged_with_lazycoder' else None,
                                                 base.MODEL, cwd, deadline_seconds, capture)
                if not result.get('failure'):
                    result['checks'] = connector.check_outcome(manifest, case['recipe_id'], result['answer'], case.get('inputs'))
                if case['workflow'] == 'landing' and not result.get('failure'):
                    result['artifact_checks'] = artifacts.check_landing_page(result['answer'], node_check=True)
                base.write(out / f'{case["id"]}-{arm}.json', result)
        record['status'] = 'completed'
    finally:
        if record['status'] == 'running':
            record['status'] = 'interrupted'
        record['hashes_after'] = {str(p.resolve()): base.digest(p) for p in watched}
        record['protected_files_unchanged'] = record['hashes_after'] == before
        base.write(out / 'manifest.json', record)
    return record


if __name__ == '__main__':
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--cases', required=True, type=Path)
    p.add_argument('--manifest', required=True, type=Path)
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--protect', action='append', default=[])
    p.add_argument('--deadline-seconds', type=float, default=120)
    a = p.parse_args()
    print(json.dumps(run(a.cases, a.manifest, a.out, protected_paths=a.protect, deadline_seconds=a.deadline_seconds)))
