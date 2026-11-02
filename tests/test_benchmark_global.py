import json
import os
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import benchmark_global as bench
import jev_choose as jev


@pytest.fixture
def inputs(tmp_path,monkeypatch):
    cases=[{'id':f'c{i}','prompt':('continue' if i==0 else ' '.join(['please']*16)),
            'context':f'Actual preceding message {i}','bucket':'continuation','expected_skills':['alpha']} for i in range(2)]
    cp,ip=tmp_path/'cases.json',tmp_path/'index.json'
    (tmp_path/'baseline_chooser.py').write_bytes(Path(jev.__file__).read_bytes())
    cp.write_text(json.dumps(cases))
    ip.write_text(json.dumps({'entries':[{'name':'alpha','kind':'domain','type':'skill','description':'Alpha domain'},
                                        {'name':'method','kind':'process','type':'skill','description':'Working method'}]}))
    monkeypatch.setenv('TYPESAFE_API_KEY','test-secret')
    monkeypatch.setattr(jev,'_key',lambda:'test-secret')
    monkeypatch.setattr(jev,'ROUTE_AT',.8)
    monkeypatch.setattr(jev,'CONTEXT_MAX_WORDS',15)
    return cp,ip,tmp_path/'out',cases


def test_five_request_design_and_accounting(inputs,monkeypatch):
    cp,ip,out,cases=inputs
    calls=[]
    def post(body,key,timeout):
        assert key=='test-secret'
        assert 0<timeout<=1.2
        calls.append(body)
        answers={}
        for q,question in body['questions'].items():
            option=next(k for k in question['criteria'] if k!='none')
            answers[q]={'type':'choice','choice':option,'confidence':.9,'probabilities':{option:.9,'none':.1}}
        return {'answers':answers,'usage':{'input_tokens':7}}
    monkeypatch.setattr(jev,'_post',post)
    monkeypatch.setattr(jev,'tier_only',lambda *a,**kw:pytest.fail('no tier calls'))
    original=jev.CACHE,jev._post,jev.CONTEXT_MAX_WORDS
    env=os.environ.get('SKILL_ROUTER_JEV')
    summary=bench.run(cp,ip,out,max_calls=10)
    assert (jev.CACHE,jev._post,jev.CONTEXT_MAX_WORDS)==original
    assert os.environ.get('SKILL_ROUTER_JEV')==env
    assert len(calls)==10
    # First case baseline+counterfactual precedes richer; second reverses bundle order.
    stages=[json.loads(x) for x in (out/'stages.jsonl').read_text().splitlines()]
    assert [s['strategy'] for s in stages]==['current','global_current_winners','global_current_top3','global_richer_top3','global_richer_top3','global_richer_top3','global_richer_top3','current','global_current_winners','global_current_top3']
    for body in calls:
        assert ('previous_assistant_message' in body['state'])==(len(body['state']['request'].split())<=15)
    manifest=json.loads((out/'manifest.json').read_text())
    assert manifest['status']=='completed' and manifest['provider_attempts']==10 and manifest['score_rows']==8
    assert all(summary[a]['primary']['n']==2 for a in bench.ARMS)
    assert summary['global_richer_top3']['top1_shortlist_hits']==2
    assert summary['global_richer_top3']['shortlist_hits']==2
    assert summary['global_current_winners']['primary']['input_tokens']==28
    assert (out/'stages.jsonl').stat().st_mode&0o777==0o600
    assert all('test-secret' not in p.read_text() for p in out.rglob('*') if p.is_file())
    with pytest.raises(ValueError,match='exists'):bench.run(cp,ip,out,max_calls=10)


def test_budget_and_duplicate_rejected_without_provider(inputs,monkeypatch):
    cp,ip,out,cases=inputs
    monkeypatch.setattr(jev,'_post',lambda *a:pytest.fail('provider forbidden'))
    with pytest.raises(ValueError,match='budget'):bench.run(cp,ip,out,max_calls=9)
    assert not out.exists()
    cp.write_text(json.dumps([cases[0],cases[0]]))
    with pytest.raises(ValueError,match='duplicate'):bench.run(cp,ip,out)
    assert not out.exists()


def test_failure_restores_live_globals(inputs,monkeypatch):
    cp,ip,out,cases=inputs
    original=jev.CACHE,jev._post
    def choose(*a,**kw):raise RuntimeError('test interruption')
    monkeypatch.setattr(jev,'choose',choose)
    with pytest.raises(RuntimeError,match='interruption'):bench.run(cp,ip,out)
    assert (jev.CACHE,jev._post)==original
    manifest=json.loads((out/'manifest.json').read_text())
    assert manifest['status']=='failed' and manifest['provider_attempts']==0


@pytest.mark.parametrize('failure', ['timeout', 'malformed_answers'])
def test_failed_baseline_never_uses_captured_response(inputs,monkeypatch,failure):
    import jev_global as experimental
    cp,ip,out,cases=inputs
    # One case is enough; simulated captured answers must not recover a rejected choice.
    cases[0]['expected_skills']=[]
    cp.write_text(json.dumps(cases[:1]))
    calls=[]
    def post(body,key,timeout):
        calls.append(body)
        return {'answers':{'domain_0':{'choice':'d0','confidence':.99,'probabilities':{'d0':.99}}}}
    monkeypatch.setattr(jev,'_post',post)
    def failed_choose(prompt,**kwargs):
        # Capture a response as if it arrived at/after rejection, then reject it.
        jev._post({'state':{},'questions':{'domain_0':{}}},'test-secret',1.2)
        jev.LAST_FAILURE=failure
        return None
    monkeypatch.setattr(jev,'choose',failed_choose)
    def experimental_choose(prompt,**kwargs):
        assert 'shortlist' not in kwargs, 'counterfactual must skip entirely after baseline failure'
        return experimental.GlobalResult(failure='stub_challenger')
    monkeypatch.setattr(experimental,'choose',experimental_choose)
    monkeypatch.setattr(experimental,'current_shortlist',lambda *a,**kw:pytest.fail('rejected raw answers must not be parsed'))
    summary=bench.run(cp,ip,out,max_calls=5)
    rows=[json.loads(x) for x in (out/'rows.jsonl').read_text().splitlines()]
    for arm in bench.ARMS:
        controls=summary[arm]['no_skill_controls']
        assert controls['n_correct']==0 and controls['valid_control_abstentions']==0
        assert controls['failures']==1
    assert all(p['both_wrong']==1 for p in summary['paired_vs_current'].values())
    assert len(calls)==1
    for row in rows:
        if row['strategy'].startswith('global_current'):
            assert row['failure']==failure and row['prediction'] is None
            assert row['shortlist']==[] and row['skipped_due_to_baseline'] is True
    assert json.loads((out/'manifest.json').read_text())['provider_attempts']==1


def test_shortlist_parse_error_fails_closed_per_case(inputs,monkeypatch):
    import jev_global as experimental
    cp,ip,out,cases=inputs
    cp.write_text(json.dumps(cases[:1]))
    def post(body,key,timeout):
        answers={q:{'choice':next(iter(v['criteria'])),'confidence':.9} for q,v in body['questions'].items()}
        return {'answers':answers}
    monkeypatch.setattr(jev,'_post',post)
    def parse(*a,**kw):raise ValueError('malformed shortlist')
    monkeypatch.setattr(experimental,'current_shortlist',parse)
    def challenger(prompt,**kwargs):
        assert 'shortlist' not in kwargs
        return experimental.GlobalResult()
    monkeypatch.setattr(experimental,'choose',challenger)
    bench.run(cp,ip,out,max_calls=5)
    rows=[json.loads(x) for x in (out/'rows.jsonl').read_text().splitlines()]
    assert all(r['failure']=='baseline_shortlist_error' and r['prediction'] is None
               for r in rows if r['strategy'].startswith('global_current'))


def test_failed_controls_are_not_correct_or_valid_abstentions():
    row={'expected':[],'prediction':None,'failure':'timeout','expected_tier':'unknown'}
    result=bench.metrics([row])
    assert result['n']==1 and result['n_correct']==0
    assert result['correct_including_abstentions']==0
    assert result['valid_abstentions']==0 and result['valid_control_abstentions']==0
    assert result['failures']==1 and not bench.is_correct(row)
    assert bench.metrics([{**row,'failure':''}])['n_correct']==1
    failed_route={**row,'prediction':'alpha'}
    assert bench.metrics([failed_route])['route_precision'] is None
    assert bench.metrics([failed_route])['routed']==0


def test_provider_echo_sanitization():
    questions={'domain_0':{'criteria':{'d0':'Alpha','none':'No match'}}}
    answers={'domain_0':{'type':'PRIVATE_TEXT','choice':'PRIVATE_TEXT',
        'confidence':float('nan'),'probabilities':{'PRIVATE_TEXT':.7,'d0':.3,'none':True}},
        'PRIVATE_QUESTION':{'choice':'PRIVATE_TEXT'}}
    sanitized=bench.sanitize_answers(answers,questions)
    assert 'PRIVATE' not in json.dumps(sanitized)
    assert sanitized['domain_0']=={'type':None,'choice':None,'confidence':None,'probabilities':{'d0':.3}}
    answers['domain_0']={'type':'choice','choice':'d0','confidence':.9,'probabilities':{'d0':.9}}
    assert bench.sanitize_answers(answers,questions)['domain_0']['choice']=='d0'
