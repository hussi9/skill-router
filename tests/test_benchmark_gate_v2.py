import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import benchmark_gate_v2 as bench
import jev_choose as jev


def fixture_files(tmp_path, monkeypatch, *, control=False):
    cases = [{'id':'fresh', 'prompt':' '.join(['please'] * 16),
              'context':'The requested implementation context', 'bucket':'real-context',
              'expected_skills':[] if control else ['alpha']}]
    cp, ip = tmp_path/'cases.json', tmp_path/'index.json'
    cp.write_text(json.dumps(cases))
    ip.write_text(json.dumps({'entries':[
        {'name':'alpha','kind':'domain','type':'skill','description':'Implement alpha'},
        {'name':'method','kind':'process','type':'skill','description':'Working method'}]}))
    (tmp_path/'baseline_chooser.py').write_bytes(Path(jev.__file__).read_bytes())
    monkeypatch.setenv('TYPESAFE_API_KEY', 'test-secret')
    monkeypatch.setattr(jev, '_key', lambda:'test-secret')
    return cp, ip, tmp_path/'out'


def provider(gate=.95, malformed=False):
    def post(body, key, timeout):
        answers = {}
        for qid, question in body['questions'].items():
            if question['type'] == 'noul':
                answers[qid] = {'type':'noul','noul':gate if qid == 'needs_specialist' else .95}
            else:
                option = next(k for k in question['criteria'] if k != 'none')
                answers[qid] = {'type':'choice','choice':option,'confidence':.95,
                                'probabilities':{option:.95,'none':.05}}
        if malformed and 'needs_specialist' in answers:
            answers['needs_specialist']['noul'] = True
        return {'answers':answers,'usage':{'input_tokens':3}}
    return post


def test_real_selectors_share_shortlist_and_accounting(tmp_path, monkeypatch):
    cp, ip, out = fixture_files(tmp_path, monkeypatch)
    calls = []
    post = provider()
    def capture(body, key, timeout):
        calls.append(body)
        return post(body, key, timeout)
    monkeypatch.setattr(jev, '_post', capture)
    summary = bench.run(cp, ip, out, max_calls=3)
    rows = [json.loads(line) for line in (out/'rows.jsonl').read_text().splitlines()]
    assert [r['strategy'] for r in rows] == list(bench.ARMS)
    assert rows[1]['shortlist'] == rows[2]['shortlist'] == ['alpha']
    assert all(summary[arm]['primary']['n_correct'] == 1 for arm in bench.ARMS)
    assert len(calls) == 3
    assert 'previous_assistant_message' not in calls[1]['state']
    assert calls[2]['state']['previous_assistant_message'] == 'The requested implementation context'
    manifest = json.loads((out/'manifest.json').read_text())
    assert manifest['provider_attempts'] == 3 and manifest['actual_input_tokens'] == 9
    assert manifest['arms'] == list(bench.ARMS)
    assert all(s['requires_gate'] for s in manifest['selector_sources'].values())
    assert str(Path(bench.__file__).resolve()) in manifest['dependency_sources']
    assert {'jev_choose.py','jev_global.py','jev_applicability.py','benchmark_global_fix.py',
            'benchmark_global.py','benchmark_context.py','benchmark_jev.py'} <= {
                Path(path).name for path in manifest['dependency_sources']}
    with pytest.raises(ValueError, match='exists'):
        bench.run(cp, ip, out, max_calls=3)


@pytest.mark.parametrize('malformed', [False, True])
def test_real_gates_block_fallback_and_failed_controls_are_exclusive(tmp_path, monkeypatch, malformed):
    cp, ip, out = fixture_files(tmp_path, monkeypatch, control=True)
    monkeypatch.setattr(jev, '_post', provider(gate=.79, malformed=malformed))
    summary = bench.run(cp, ip, out, max_calls=3)
    rows = [json.loads(line) for line in (out/'rows.jsonl').read_text().splitlines()]
    for arm in bench.ARMS[1:]:
        row = next(r for r in rows if r['strategy'] == arm)
        assert row['prediction'] is None and row['domain_prediction'] is None
        metrics = summary[arm]['no_skill_controls']
        assert metrics['failures'] == int(malformed)
        assert metrics['valid_control_abstentions'] == int(not malformed)
        assert metrics['n_correct'] == int(not malformed)
