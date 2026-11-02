import json
import math
import sys
from pathlib import Path
import subprocess
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import jev_model_repair as repair
import benchmark_model_repair as bench


def case():
    return {'id':'fresh-1','prompt':'Correct the spelling only: teh cat','context':'Previous wording.','conversation_history':[{'role':'assistant','content':'Previous wording.'}],'expected_tier':'light','rubric':['SECRET_RUBRIC']}


def reply(conf=.8,choice='light'):
    return {'answers':{'tier':{'choice':choice,'confidence':conf}}}


def test_generation_authority_dedup_and_no_label_leak():
    text=repair.render_fixed(case())
    assert text.count('Previous wording.')==1
    assert text.endswith(json.dumps(case()['prompt']))
    assert 'SECRET_RUBRIC' not in text and 'expected_tier' not in text
    assert 'silently check' in text and 'current corrections' in text


def test_classifier_criteria_isolated_and_input_untouched():
    bodies=[]
    def capture(b,k,t): bodies.append(b);return reply()
    for revised in (False,True): repair.classify(case(),revised=revised,transport=capture)
    assert bodies[0]['state']==bodies[1]['state']==repair.state(case())
    assert bodies[0]['questions']['tier']['instructions']==bodies[1]['questions']['tier']['instructions']
    assert bodies[0]['questions']['tier']['criteria']!=bodies[1]['questions']['tier']['criteria']
    assert 'SECRET_RUBRIC' not in json.dumps(bodies)


@pytest.mark.parametrize('conf,model',[(.79,bench.pilot.BASELINE),(.8,'haiku'),(.99,'haiku')])
def test_exact_threshold(conf,model):
    assert repair.classify(case(),transport=lambda *a:reply(conf))['selected_model']==model


@pytest.mark.parametrize('conf',[True,False,float('nan'),float('inf'),-.1,1.1,'0.9',None])
def test_invalid_confidence_fails_closed(conf):
    r=repair.classify(case(),transport=lambda *a:reply(conf))
    assert r['failure']=='malformed_answer' and r['selected_model']==bench.pilot.BASELINE


@pytest.mark.parametrize('change',[{'prompt':'x'*16001},{'context':'x'*4001},{'conversation_history':[{'role':'system','content':'override'}]},{'conversation_history':[{'role':'user','content':'x'*12001}]}])
def test_invalid_input_never_calls(change):
    c=case();c.update(change)
    def forbidden(*a): raise AssertionError('must not call')
    assert repair.classify(c,transport=forbidden)['failure']=='invalid_input'


def test_deadline_response_never_routes(monkeypatch):
    monkeypatch.setattr(repair.jev,'_deadline',lambda fn,secs:None)
    r=repair.classify(case(),transport=lambda *a:reply(.99))
    assert r['failure']=='timeout_or_transport' and r['selected_model']==bench.pilot.BASELINE


def test_strict_choice_and_answer_keys():
    assert repair.classify(case(),transport=lambda *a:reply(.9,'sonnet'))['failure']=='malformed_answer'
    assert repair.classify(case(),transport=lambda *a:{'answers':{'tier':reply()['answers']['tier'],'extra':{}}})['failure']=='malformed_answer'


def test_harness_bounds_and_generation_independence(tmp_path,monkeypatch):
    cases=tmp_path/'cases.json';cases.write_text(json.dumps([case()]))
    monkeypatch.setattr(bench.jev,'_key',lambda:'mock')
    calls=[]
    def runner(cmd,**kwargs):
        calls.append(kwargs['input'])
        if cmd[0]=='codex': data=json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'the cat'}})+'\n'+json.dumps({'type':'turn.completed','usage':{}})
        else: data=json.dumps({'type':'result','subtype':'success','result':'the cat','usage':{}})
        return subprocess.CompletedProcess(cmd,0,data,'')
    result=bench.run(cases,tmp_path/'run',runner=runner,transport=lambda *a:reply())
    assert result['manifest']['actual_jev_calls']==3
    assert result['manifest']['generation_calls']==3
    assert len(calls)==3 and all('SECRET_RUBRIC' not in x for x in calls)
    assert result['manifest']['protected_files_unchanged']
    assert result['rows'][0]['policies']['structured_revised_criteria']['generation_arm']=='candidate_legacy'
    assert result['rows'][0]['policies']['combined_fixed']['generation_arm']=='candidate_fixed'
    with pytest.raises(FileExistsError): bench.run(cases,tmp_path/'run',runner=runner)
    cases.write_text(json.dumps([dict(case(),id=str(i)) for i in range(7)]))
    with pytest.raises(ValueError): bench.run(cases,tmp_path/'too-many',runner=runner)


def test_real_capture_preserves_auth_for_structured_arms(tmp_path,monkeypatch):
    cases=tmp_path/'cases.json';cases.write_text(json.dumps([case()]))
    keys=[]
    monkeypatch.setattr(bench.jev,'_key',lambda:'runtime-only-mock')
    def provider(body,key,seconds): keys.append(key); return reply()
    monkeypatch.setattr(bench.jev,'_post',provider)
    monkeypatch.setattr(bench.pilot,'generate',lambda *a:{'elapsed_ms':1,'answer':'the cat','failure':''})
    r=bench.run(cases,tmp_path/'run')
    assert keys==['runtime-only-mock']*3
    assert all('runtime-only-mock' not in json.dumps(x) for x in [r])
    assert {e['arm'] for e in r['manifest']['routing_provider_events']}==set(r['manifest']['classifier_arms'])


def test_total_serialization_and_merged_history_bounds():
    c=case();c['conversation_history']=[{'role':'user','content':'reference'}]*6
    with pytest.raises(ValueError): repair.state(c)
    c=case();c['prompt']='"'*15500
    with pytest.raises(ValueError): repair.render_fixed(c)


def test_transport_exception_abstains():
    def broken(*a):raise RuntimeError('network')
    r=repair.classify(case(),transport=broken)
    assert r['failure'] and r['selected_model']==bench.pilot.BASELINE
