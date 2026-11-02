import importlib.util
import json
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location('lazycoder_stages', Path(__file__).resolve().parents[1] / 'scripts/lazycoder_stages.py')
s = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s)


def case(recipe='summarize-long-thing'):
    return {'recipe_id': recipe, 'conversation_history': [{'role':'assistant','content':'I propose deletion.'}, {'role':'user','content':'Yes, delete it.'}], 'prompt':'Do not delete it now.'}


def ledger():
    return {'decisions':[{'topic':'deletion','status':'reversed','events':[{'turn':1,'quote':'I propose deletion.','kind':'proposal'}, {'turn':2,'quote':'Yes, delete it.','kind':'explicit_acceptance'}, {'turn':3,'quote':'Do not delete it now.','kind':'explicit_reversal'}]}]}


def fake(answers, seen=None):
    values = iter(answers)
    def generate(model, prompt, cwd, *, deadline_seconds):
        assert model == s.MODEL
        if seen is not None:
            seen.append(prompt)
        v = next(values)
        if isinstance(v, dict) and 'failure' in v:
            return v
        return {'answer':json.dumps(v) if isinstance(v, dict) else v, 'failure':'', 'estimated_cost_usd':.01}
    return generate


def plan():
    return {'lang':'en','brief':'Draft supplied offer. Style used: system fonts.', 'headlines':'A page', 'page_plan':'Hero then form', 'connect_form':'Connect endpoint later.', 'publish':'Preview locally.', 'checks':'Not previewed.', 'sections':[{'id':'hero','purpose':'Describe offer'}, {'id':'signup','purpose':'Disabled form'}]}


def components():
    return [plan(), {'head':'<title>Offer</title><meta name="viewport" content="width=device-width"><style>body{font-family:system-ui}</style>'}, {'body':'<section id="hero"><h1>Offer</h1></section>'}, {'body':'<section id="signup"><button disabled>Join list</button></section>'}, {'javascript':''}]


def test_approval_two_calls_full_context_and_provenance(tmp_path):
    seen=[]
    r=s.workflow(case(), 'full recipe', s.MODEL, tmp_path,120,fake([ledger(),'Do not delete; pending approval.'],seen))
    assert not r['failure'] and r['generation_calls']==2
    assert r['answer']=='Do not delete; pending approval.' and not r['verified']
    assert all('Yes, delete it.' in p and 'Do not delete it now.' in p for p in seen)
    assert 'Ledger:' in seen[1] and 'full recipe' in seen[1]
    assert r['estimated_cost_usd']==.02
    assert all(len(step['prompt_sha256'])==64 for step in r['steps'])


def test_unmatched_quote_fails_closed_after_one_call(tmp_path):
    v=ledger();v['decisions'][0]['events'][0]['quote']='not source'
    r=s.workflow(case(),None,s.MODEL,tmp_path,120,fake([v]))
    assert 'not present' in r['failure'] and r['generation_calls']==1 and r['answer']==''


def test_assistant_cannot_supply_acceptance_and_turns_must_be_chronological():
    turns,_=s._context(case())
    v=ledger();v['decisions'][0]['events'][0]['kind']='explicit_acceptance'
    with pytest.raises(ValueError,match='assistant'):
        s.validate_ledger(v,turns)
    v=ledger();v['decisions'][0]['events'].reverse()
    with pytest.raises(ValueError,match='chronological'):
        s.validate_ledger(v,turns)


def test_landing_assembly_five_calls_without_recipe(tmp_path):
    seen=[]
    r=s.workflow(case('landing-page-no-code'),None,s.MODEL,tmp_path,120,fake(components(),seen))
    assert not r['failure'] and r['generation_calls']==5
    assert r['answer'].count('<!doctype html>')==1
    assert r['answer'].count('</html>')==1
    assert r['answer'].index('id="hero"')<r['answer'].index('id="signup"')
    assert 'No UI rendering' not in r['answer'] # wording below is explicit
    assert 'no UI rendering' in r['answer']
    assert all('Complete original conversation' in p for p in seen)
    assert not any('Complete selected recipe contract' in p for p in seen)
    assert not r['verified']


def test_timeout_partial_never_assembled(tmp_path):
    outputs=components()[:2]+[{'failure':'timeout','answer':'','partial_answer':'<section id="hero">half','estimated_cost_usd':None}]
    r=s.workflow(case('landing-page-no-code'),'recipe',s.MODEL,tmp_path,120,fake(outputs))
    assert r['failure']=='generation_failed:timeout' and r['answer']==''
    assert r['generation_calls']==3 and not r['cost_complete']
    assert r['steps'][-1]['partial_answer'].endswith('half')


@pytest.mark.parametrize('body', ['<body><section id="hero"></section></body>', '<section id="hero"><p>truncated', '<section id="hero"></section><section id="hero"></section>'])
def test_invalid_or_duplicate_components_fail_closed(tmp_path,body):
    outputs=components();outputs[2]={'body':body}
    r=s.workflow(case('landing-page-no-code'),'recipe',s.MODEL,tmp_path,120,fake(outputs))
    assert r['failure'] and not r['answer'] and r['generation_calls']==3


def test_missing_script_json_does_not_succeed(tmp_path):
    outputs=components();outputs[-1]='{"javascript":'
    r=s.workflow(case('landing-page-no-code'),'recipe',s.MODEL,tmp_path,120,fake(outputs))
    assert r['failure'] and r['answer']=='' and r['generation_calls']==5


def test_prompt_bound_prevents_call_without_truncation(tmp_path):
    def never(*a,**k):pytest.fail('must not call')
    r=s.workflow(case(),'x'*120000,s.MODEL,tmp_path,120,never)
    assert 'never truncate' in r['failure'] and r['generation_calls']==0


def test_wrong_model_rejected_before_call(tmp_path):
    with pytest.raises(ValueError,match='pinned'):
        s.workflow(case(),None,'opus',tmp_path,120,lambda *a,**k:pytest.fail('must not call'))


def test_components_receive_actual_frozen_styles_and_markup(tmp_path):
    seen=[]
    r=s.workflow(case('landing-page-no-code'),None,s.MODEL,tmp_path,120,fake(components(),seen))
    assert not r['failure']
    assert '<style>body{font-family:system-ui}</style>' in seen[2]
    assert '<style>body{font-family:system-ui}</style>' in seen[3]
    assert '<section id="hero">' in seen[3]
    assert '<section id="hero">' in seen[4] and '<section id="signup">' in seen[4]


def test_ledger_cannot_accept_later_reversal_or_reject_without_rejection():
    turns,_=s._context(case())
    v=ledger();v['decisions'][0]['status']='accepted'
    with pytest.raises(ValueError,match='later'):
        s.validate_ledger(v,turns)
    v=ledger();v['decisions'][0]['status']='rejected'
    with pytest.raises(ValueError,match='rejection citation'):
        s.validate_ledger(v,turns)


def test_workflow_must_match_recipe(tmp_path):
    v=case();v['workflow']='landing'
    with pytest.raises(ValueError,match='mismatch'):
        s.workflow(v,None,s.MODEL,tmp_path,120,lambda *a,**k:pytest.fail('must not call'))


def test_thirteen_section_plan_supported():
    v=plan();v['sections']=[{'id':f'section-{i}','purpose':'Source requested section'} for i in range(13)]
    assert len(s._plan(v)['sections'])==13


def test_standing_user_constraint_does_not_need_option_acceptance():
    turns = [{'role': 'user', 'content': 'Preserve every working feature.'}]
    value = {'decisions': [{'topic': 'features', 'status': 'active_constraint',
                           'events': [{'turn': 1, 'quote': 'Preserve every working feature.', 'kind': 'constraint'}]}]}
    assert s.validate_ledger(value, turns) == value
    with pytest.raises(ValueError, match='user instruction'):
        s.validate_ledger(value, [{'role': 'assistant', 'content': 'Preserve every working feature.'}])


def test_delegation_distinct_from_specific_option_approval():
    turns = [{'role': 'user', 'content': 'Choose the architecture yourself; do not delete features.'}]
    value = {'decisions': [{'topic': 'architecture', 'status': 'delegated',
        'events': [{'turn': 1, 'quote': 'Choose the architecture yourself', 'kind': 'explicit_delegation'}]}]}
    assert s.validate_ledger(value, turns) == value
    with pytest.raises(ValueError, match='assistant'):
        s.validate_ledger(value, [{'role': 'assistant', 'content': turns[0]['content']}])
    value['decisions'][0]['events'][0]['kind'] = 'constraint'
    with pytest.raises(ValueError, match='delegation'):
        s.validate_ledger(value, turns)


def test_active_constraint_cannot_ignore_cited_later_reversal():
    turns = [{'role': 'user', 'content': 'Use minimal stack.'}, {'role': 'user', 'content': 'Do not use minimal stack.'}]
    value = {'decisions': [{'topic': 'stack', 'status': 'active_constraint', 'events': [
        {'turn': 1, 'quote': 'Use minimal stack.', 'kind': 'constraint'},
        {'turn': 2, 'quote': 'Do not use minimal stack.', 'kind': 'explicit_reversal'}]}]}
    with pytest.raises(ValueError, match='later rejection or reversal'):
        s.validate_ledger(value, turns)


@pytest.mark.parametrize('status,kind', [('active_constraint', 'constraint'), ('delegated', 'explicit_delegation')])
def test_question_after_reversal_cannot_reactivate_permission(status, kind):
    turns = [{'role': 'user', 'content': 'Choose it.'}, {'role': 'user', 'content': 'Stop that choice.'},
             {'role': 'user', 'content': 'What are the alternatives?'}]
    value = {'decisions': [{'topic': 'choice', 'status': status, 'events': [
        {'turn': 1, 'quote': 'Choose it.', 'kind': kind},
        {'turn': 2, 'quote': 'Stop that choice.', 'kind': 'explicit_reversal'},
        {'turn': 3, 'quote': 'What are the alternatives?', 'kind': 'open_question'}]}]}
    with pytest.raises(ValueError, match='later rejection or reversal'):
        s.validate_ledger(value, turns)
