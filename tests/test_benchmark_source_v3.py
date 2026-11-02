import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import benchmark_source_v3 as bench
import jev_choose as jev
import skill_capabilities


def fixtures(tmp_path, monkeypatch, expected=('alpha',)):
    cp, ip, sp = (tmp_path/name for name in ('cases.json','index.json','capabilities.json'))
    cp.write_text(json.dumps([{'id':'fresh', 'prompt':'Please do the requested work',
        'context':'Implement alpha and use the requested working method',
        'bucket':'real-context','expected_skills':list(expected)}]))
    entries = [{'name':'alpha','kind':'domain','type':'skill','description':'Implement alpha'},
               {'name':'method','kind':'process','type':'skill','description':'Working method'}]
    ip.write_text(json.dumps({'entries':entries}))
    snapshot = skill_capabilities.build_snapshot(entries, {'entries':[]})
    sp.write_text(json.dumps(snapshot))
    (tmp_path/'baseline_chooser.py').write_bytes(Path(jev.__file__).read_bytes())
    monkeypatch.setenv('TYPESAFE_API_KEY','test-secret')
    monkeypatch.setattr(jev, '_key', lambda:'test-secret')
    return cp, ip, sp, tmp_path/'out'


def provider(*, domain=.95, process=.95, process_fit=.2, gate=.95):
    def post(body, key, timeout):
        answers = {}
        for qid, question in body['questions'].items():
            if question['type'] == 'noul':
                value = gate if qid == 'needs_specialist' else (
                    process_fit if 'Skill identity: method' in question['criteria']['true'] else domain)
                answers[qid] = {'type':'noul','noul':value}
            else:
                option = next(k for k in question['criteria'] if k != 'none')
                score = process if qid == 'process' else domain
                answers[qid] = {'type':'choice','choice':option,'confidence':score,
                    'probabilities':{option:score,'none':1-score}}
        return {'answers':answers,'usage':{'input_tokens':3}}
    return post


def rows(out):
    return {row['strategy']:row for row in map(json.loads,(out/'rows.jsonl').read_text().splitlines())}


def test_real_selectors_share_domain_pool_and_freeze_sources(tmp_path, monkeypatch):
    args = fixtures(tmp_path, monkeypatch)
    monkeypatch.setattr(jev, '_post', provider())
    summary = bench.run(*args, max_calls=3)
    rr = rows(args[-1])
    assert all(summary[arm]['primary']['n_correct'] == 1 for arm in bench.ARMS)
    assert rr['global_gate_v2']['shortlist'] == rr['global_source_v3']['shortlist'] == ['alpha']
    manifest = json.loads((args[-1]/'manifest.json').read_text())
    assert manifest['provider_attempts'] == 3 and manifest['actual_input_tokens'] == 9
    assert manifest['no_fallback_arms'] == ['global_source_v3']
    assert manifest['process_candidate_arms'] == ['global_source_v3']
    deps = {Path(path).name for path in manifest['dependency_sources']}
    assert {'capabilities.json','skill_capabilities.py','build_index.py','jev_gate_v2.py',
            'jev_source_v3.py','benchmark_source_v3.py'} <= deps
    with pytest.raises(ValueError,match='exists'):
        bench.run(*args, max_calls=3)


@pytest.mark.parametrize('fit,accepted',[(.2,False),(.95,True)])
def test_real_process_candidate_requires_independent_fit_and_is_not_domain(tmp_path, monkeypatch, fit, accepted):
    args = fixtures(tmp_path, monkeypatch, expected=('method',))
    monkeypatch.setattr(jev, '_post', provider(domain=.7,process_fit=fit))
    summary = bench.run(*args,max_calls=3)
    rr = rows(args[-1])
    assert rr['current']['prediction'] == rr['global_gate_v2']['prediction'] == 'method'
    assert rr['global_source_v3']['prediction'] == ('method' if accepted else None)
    assert rr['global_source_v3']['domain_prediction'] is None
    assert summary['global_source_v3']['primary']['routed'] == int(accepted)
    assert summary['global_source_v3']['domain']['routed'] == 0


def test_false_gate_blocks_even_confident_process(tmp_path, monkeypatch):
    args = fixtures(tmp_path, monkeypatch, expected=())
    monkeypatch.setattr(jev, '_post', provider(domain=.7,process_fit=.95,gate=.79))
    summary = bench.run(*args,max_calls=3)
    assert rows(args[-1])['global_source_v3']['prediction'] is None
    assert summary['global_source_v3']['no_skill_controls']['valid_control_abstentions'] == 1


def test_hard_request_bound_before_provider(tmp_path, monkeypatch):
    args = fixtures(tmp_path, monkeypatch)
    with pytest.raises(ValueError,match='54 requests'):
        bench.run(*args,max_calls=55)
    assert not args[-1].exists()


def test_malformed_answers_are_failures_not_correct_abstentions(tmp_path, monkeypatch):
    args = fixtures(tmp_path, monkeypatch, expected=())
    post = provider()
    def malformed(body, key, timeout):
        result = post(body, key, timeout)
        if 'needs_specialist' in result['answers']:
            result['answers']['needs_specialist']['noul'] = True
        return result
    monkeypatch.setattr(jev, '_post', malformed)
    summary = bench.run(*args,max_calls=3)
    for arm in bench.ARMS[1:]:
        metrics = summary[arm]['no_skill_controls']
        assert metrics['failures'] == 1
        assert metrics['valid_control_abstentions'] == metrics['n_correct'] == 0
