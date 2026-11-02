import json
import os
from pathlib import Path
import sys

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import benchmark_context as bench
import jev_choose as jev


@pytest.fixture
def data(tmp_path, monkeypatch):
    cases = [{'id': f'c{i}', 'prompt': ('continue' if i == 0 else ' '.join(['please']*16)),
              'context': f'Actual previous assistant message {i}', 'bucket': 'continuation', 'expected_skills': ['alpha']}
             for i in range(2)]
    cp, ip = tmp_path / 'cases.json', tmp_path / 'index.json'
    cp.write_text(json.dumps(cases))
    ip.write_text(json.dumps({'entries': [{'name': 'alpha', 'kind': 'domain', 'type': 'skill', 'description': 'alpha', 'use_when': []}]}))
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-secret')
    monkeypatch.setattr(jev, '_key', lambda: 'test-secret')
    monkeypatch.setattr(jev, 'CONTEXT_MAX_WORDS', 15)
    monkeypatch.setattr(jev, 'ROUTE_AT', .8)
    return cp, ip, tmp_path / 'out', cases


def test_arm_context_budget_and_restoration(data, monkeypatch):
    cp, ip, out, cases = data
    calls = []
    def post(body, key, timeout):
        assert key == 'test-secret'
        calls.append((dict(body['state']), jev.CACHE.parent.name, jev.CONTEXT_MAX_WORDS))
        answers = {q: {'type': 'choice', 'choice': ('heavy' if q == 'tier' else 'OPERATE' if q == 'path' else 'none'), 'confidence': .9}
                   for q in body['questions']}
        return {'answers': answers, 'usage': {'input_tokens': 12}}
    monkeypatch.setattr(jev, '_post', post)
    monkeypatch.setattr(jev, 'tier_only', lambda *a, **kw: pytest.fail('tier-only forbidden'))
    original = (jev.CACHE, jev.CONTEXT_MAX_WORDS, jev._post)
    env = os.environ.get('SKILL_ROUTER_JEV')
    summary = bench.run(cp, ip, out, max_calls=6)
    assert (jev.CACHE, jev.CONTEXT_MAX_WORDS, jev._post) == original
    assert os.environ.get('SKILL_ROUTER_JEV') == env
    assert len(calls) == 6
    assert [arm for _, arm, _ in calls] == list(bench.ORDERS[0] + bench.ORDERS[1])
    for state, arm, cutoff in calls:
        request_short = len(state['request'].split()) <= 15
        assert ('previous_assistant_message' in state) == (arm == 'all_context' or (arm == 'production_context' and request_short))
    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['provider_attempts'] == manifest['request_slots_executed'] == manifest['telemetry_events'] == 6
    assert manifest['cache_hits'] == 0
    assert manifest['status'] == 'completed'
    assert manifest['finalist_adjudication_tested'] is False
    assert all(summary[arm]['primary']['n'] == 2 for arm in bench.ARMS)
    assert len((out / 'answers.jsonl').read_text().splitlines()) == 6
    for p in out.rglob('*'):
        if p.is_file():
            assert 'test-secret' not in p.read_text()
    assert (out / 'answers.jsonl').stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match='exists'):
        bench.run(cp, ip, out, max_calls=6)


def test_budget_duplicates_rejected_before_provider(data, monkeypatch):
    cp, ip, out, cases = data
    monkeypatch.setattr(jev, '_post', lambda *a: pytest.fail('provider must not run'))
    with pytest.raises(ValueError, match='budget'):
        bench.run(cp, ip, out, max_calls=5)
    assert not out.exists()
    cp.write_text(json.dumps([cases[0], cases[0]]))
    with pytest.raises(ValueError, match='duplicate'):
        bench.run(cp, ip, out)
    assert not out.exists()


def test_exception_restores_gate_cache_and_transport(data, monkeypatch):
    cp, ip, out, cases = data
    original = (jev.CACHE, jev.CONTEXT_MAX_WORDS, jev._post)
    def choose(*a, **kw):
        raise RuntimeError('test interruption')
    monkeypatch.setattr(jev, 'choose', choose)
    with pytest.raises(RuntimeError, match='interruption'):
        bench.run(cp, ip, out)
    assert (jev.CACHE, jev.CONTEXT_MAX_WORDS, jev._post) == original
    manifest = json.loads((out / 'manifest.json').read_text())
    assert manifest['status'] == 'failed'
    assert manifest['request_slots_executed'] == 1
    assert manifest['provider_attempts'] == 0
