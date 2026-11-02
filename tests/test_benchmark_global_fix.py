import json
import os
from pathlib import Path
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import benchmark_global_fix as bench
from types import SimpleNamespace
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
    import jev_global
    monkeypatch.setitem(sys.modules, 'jev_applicability', SimpleNamespace(__file__=jev_global.__file__, choose=lambda *a,**kw:jev_global.choose(*a,**kw,representation='current')))
    return cp,ip,tmp_path/'out',cases


def test_three_request_design_and_accounting(inputs,monkeypatch):
    cp,ip,out,cases=inputs
    calls=[]
    def post(body,key,timeout):
        assert key=='test-secret'
        calls.append(body)
        answers={q:{'type':'choice','choice':next(k for k in question['criteria'] if k!='none'),
                    'confidence':.9,'probabilities':{next(k for k in question['criteria'] if k!='none'):.9,'none':.1}}
                 for q,question in body['questions'].items()}
        return {'answers':answers,'usage':{'input_tokens':7}}
    monkeypatch.setattr(jev,'_post',post)
    original=jev.CACHE,jev._post
    summary=bench.run(cp,ip,out,max_calls=6)
    assert (jev.CACHE,jev._post)==original
    assert len(calls)==6
    stages=[json.loads(x) for x in (out/'stages.jsonl').read_text().splitlines()]
    assert [s['strategy'] for s in stages]==['current','global_current_top3','global_applicability','current','global_applicability','global_current_top3']
    manifest=json.loads((out/'manifest.json').read_text())
    assert manifest['status']=='completed' and manifest['provider_attempts']==6 and manifest['score_rows']==6
    assert manifest['actual_input_tokens']==42
    assert all(summary[a]['primary']['n']==2 for a in bench.ARMS)
    assert summary['global_current_top3']['shortlist_hits']==2
    assert (out/'stages.jsonl').stat().st_mode&0o777==0o600
    assert all('test-secret' not in p.read_text() for p in out.rglob('*') if p.is_file())
    with pytest.raises(ValueError,match='exists'):bench.run(cp,ip,out,max_calls=6)


def test_budget_and_duplicate_rejected_without_provider(inputs,monkeypatch):
    cp,ip,out,cases=inputs
    monkeypatch.setattr(jev,'_post',lambda *a:pytest.fail('provider forbidden'))
    with pytest.raises(ValueError,match='budget'):bench.run(cp,ip,out,max_calls=5)
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
        pytest.fail('all selectors must skip entirely after baseline failure')
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
        pytest.fail('all selectors must skip after shortlist parse error')
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


def test_action_gate_blocks_process_fallback():
    import jev_global
    result=jev_global.GlobalResult(stages=[{'needs_specialist':.79}])
    process=jev.Pick('method',.99,'route')
    assert bench.selector_prediction(result,process,'global_applicability') is None
    assert bench.selector_prediction(result,process,'global_current_top3') == 'method'
    result.stages[-1]['needs_specialist']=.8
    assert bench.selector_prediction(result,process,'global_applicability') == 'method'
    result.failure='malformed_applicability'
    assert bench.selector_prediction(result,process,'global_applicability') is None


@pytest.mark.parametrize('value',[True,float('nan'),float('inf'),-0.1,1.1,'PRIVATE_TEXT'])
def test_noul_sanitization_rejects_invalid_values(value):
    cleaned=bench.sanitize_answers({'fit_0':{'type':'noul','noul':value},
                                    'PRIVATE_QUESTION':{'type':'noul','noul':.9}},
                                   {'fit_0':{'type':'noul','criteria':{'true':'Fit','false':'No fit'}}})
    assert cleaned == {'fit_0':{'type':'noul','noul':None}}


def test_noul_sanitization_keeps_known_numeric_answers_only():
    assert bench.sanitize_answers({'fit_0':{'type':'noul','noul':.8,'reason':'PRIVATE_TEXT'}},
                                   {'fit_0':{'type':'noul'}}) == {'fit_0':{'type':'noul','noul':.8}}


def test_real_applicability_integration_routes_and_blocks_control(inputs,monkeypatch):
    import importlib
    cp,ip,out,cases=inputs
    monkeypatch.delitem(sys.modules,'jev_applicability')
    actual=importlib.import_module('jev_applicability')
    assert actual.__file__.endswith('jev_applicability.py')
    cases[1]['expected_skills']=[]
    cp.write_text(json.dumps(cases))
    calls=[]
    def post(body,key,timeout):
        calls.append(body)
        answers={}
        for q,question in body['questions'].items():
            if question['type']=='noul':
                value=.1 if q=='needs_specialist' and body['state']['request'].startswith('please') else .95
                answers[q]={'type':'noul','noul':value,'reason':'PRIVATE_ECHO'}
            else:
                option=next(k for k in question['criteria'] if k!='none')
                answers[q]={'type':'choice','choice':option,'confidence':.95,'probabilities':{option:.95,'none':.05}}
        return {'answers':answers,'usage':{'input_tokens':9}}
    monkeypatch.setattr(jev,'_post',post)
    summary=bench.run(cp,ip,out,max_calls=6)
    rows=[json.loads(x) for x in (out/'rows.jsonl').read_text().splitlines()]
    new=[r for r in rows if r['strategy']=='global_applicability']
    assert [r['prediction'] for r in new]==['alpha',None]
    assert new[0]['choice']['primary']['confidence']>=.8
    assert new[1]['choice']['stages'][-1]['needs_specialist']==.1
    assert len(calls)==6 and summary['global_applicability']['primary']['n_correct']==2
    stages=(out/'stages.jsonl').read_text()
    assert 'PRIVATE_ECHO' not in stages
    assert any('noul' in s['answers'].get('needs_specialist',{})
               for s in map(json.loads,stages.splitlines()))
