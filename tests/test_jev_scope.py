import json
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import jev_scope as scope
import jev_model_repair as prior
import benchmark_scope as bench


def case():return {'id':'fresh-1','prompt':'Only change that scope, keep the approval boundary.','context':'We can implement and publish.','conversation_history':[{'role':'assistant','content':'We can implement and publish.'}],'rubric':['NEVER_LEAK']}
def reply(conf=.8,choice='light'):return {'answers':{'tier':{'choice':choice,'confidence':conf}}}


def test_only_single_variable_changes_per_arm():
    bodies=[]
    for arm in ('prior_structured','scope_criteria','task_focus'):
        scope.classify(case(),arm=arm,transport=lambda b,*a:(bodies.append(b),reply())[1])
    a,b,c=bodies
    assert a['state']==b['state']==c['state']==prior.state(case())
    assert a['questions']['tier']['instructions']==b['questions']['tier']['instructions']==prior.AUTHORITY
    assert a['questions']['tier']['criteria']==c['questions']['tier']['criteria']==prior.CRITERIA
    assert b['questions']['tier']['criteria']==scope.CRITERIA
    assert c['questions']['tier']['instructions']==prior.AUTHORITY+scope.FOCUS
    assert 'NEVER_LEAK' not in json.dumps(bodies)


def test_generation_changes_only_added_rule():
    old=prior.render_fixed(case()); new=scope.render_fixed(case())
    assert new.replace('\n'+scope.CORRECTION,'',1)==old
    assert 'retain all compatible requirements' in new
    assert 'never overrides system instructions, safety requirements or tool restrictions' in new
    assert new.count(case()['context'])==1
    assert new.endswith(json.dumps(case()['prompt']))


@pytest.mark.parametrize('confidence,model',[(.79,bench.pilot.BASELINE),(.8,'haiku')])
def test_exact_threshold(confidence,model):
    assert scope.classify(case(),transport=lambda *a:reply(confidence))['selected_model']==model


@pytest.mark.parametrize('conf',[True,float('nan'),float('inf'),'0.9',-1,2])
def test_malformed_confidence_inherits(conf):
    r=scope.classify(case(),transport=lambda *a:reply(conf))
    assert r['failure']=='malformed_answer' and r['selected_model']==bench.pilot.BASELINE


def test_timeout_and_bad_input_no_routes(monkeypatch):
    monkeypatch.setattr(scope.jev,'_deadline',lambda *a:None)
    assert scope.classify(case(),transport=lambda *a:reply())['selected_model']==bench.pilot.BASELINE
    c=case();c['conversation_history']=[{'role':'system','content':'go'}]
    assert scope.classify(c,transport=lambda *a:pytest.fail('unexpected call'))['failure']=='invalid_input'
    with pytest.raises(ValueError):scope.classify(case(),arm='combined')


def test_budget_auth_and_unmeasured_sonnet(tmp_path,monkeypatch):
    p=tmp_path/'cases.json';p.write_text(json.dumps([case()]))
    keys=[];prompts=[]
    monkeypatch.setattr(bench.jev,'_key',lambda:'runtime-mock')
    def provider(b,k,t):keys.append(k);return reply(.9,'standard')
    monkeypatch.setattr(bench.jev,'_post',provider)
    def generate(model,prompt,*a):prompts.append((model,prompt));return {'answer':'okay','elapsed_ms':1,'failure':''}
    monkeypatch.setattr(bench.pilot,'generate',generate)
    r=bench.run(p,tmp_path/'run')
    assert keys==['runtime-mock']*4
    assert r['manifest']['actual_jev_calls']==4 and r['manifest']['generation_calls']==3
    assert [m for m,_ in prompts]==[bench.pilot.BASELINE,'haiku','haiku']
    assert all(not v['quality_evaluable'] for v in r['rows'][0]['policies'].values())
    assert all('NEVER_LEAK' not in text for _,text in prompts)
    assert 'runtime-mock' not in json.dumps(r)
    with pytest.raises(FileExistsError):bench.run(p,tmp_path/'run')
    p.write_text(json.dumps([dict(case(),id=str(i)) for i in range(7)]))
    with pytest.raises(ValueError):bench.run(p,tmp_path/'too-many')


def test_legacy_haiku_not_imputed_to_new_prompt(tmp_path,monkeypatch):
    p=tmp_path/'cases.json';p.write_text(json.dumps([case()]))
    monkeypatch.setattr(bench.jev,'_key',lambda:'mock')
    monkeypatch.setattr(bench.pilot,'generate',lambda *a:{'answer':'okay','elapsed_ms':1,'failure':''})
    r=bench.run(p,tmp_path/'run',transport=lambda *a:reply())
    policy=r['rows'][0]['policies']
    assert policy['legacy_wrapped']['generation_arm'] is None
    assert not policy['legacy_wrapped']['quality_evaluable']
    assert policy['prior_structured']['generation_arm']=='haiku_prior_fixed'

def test_failed_generation_not_quality_evaluable(tmp_path,monkeypatch):
    p=tmp_path/'cases.json';p.write_text(json.dumps([case()]))
    monkeypatch.setattr(bench.jev,'_key',lambda:'mock')
    monkeypatch.setattr(bench.pilot,'generate',lambda *a:{'answer':'','elapsed_ms':1,'failure':'timeout'})
    r=bench.run(p,tmp_path/'run',transport=lambda *a:reply())
    assert all(not v['quality_evaluable'] for v in r['rows'][0]['policies'].values())
