import json
from pathlib import Path
import subprocess
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import benchmark_model_selection as bench
import jev_choose as jev


def case():return {'id':'a','prompt':'Summarize this data.','context':'Data: 3, 4, 5.', 'conversation_history':[],'rubric':['Accurate']}


def test_paired_exact_input_and_no_duplicate_inherit(tmp_path,monkeypatch):
    cp=tmp_path/'cases.json';cp.write_text(json.dumps([case()]))
    monkeypatch.setattr(jev,'tier_only',lambda *a,**kw:jev.Pick('light',.79,'suggest'))
    seen=[]
    def runner(cmd,**kw):
        seen.append((cmd,kw))
        return subprocess.CompletedProcess(cmd,0,'{"type":"item.completed","item":{"type":"agent_message","text":"Mean 4"}}\n{"type":"turn.completed","usage":{"input_tokens":12}}','')
    result=bench.run(cp,tmp_path/'out',runner=runner)
    assert len(seen)==1
    assert seen[0][1]['input']==bench.render(case())
    assert seen[0][1]['env']['SKILL_ROUTER_OFF']=='1'
    assert seen[0][1]['env']['SKILL_ROUTER_NO_LEARN']=='1'
    assert json.loads((tmp_path/'out'/'manifest-start.json').read_text())['status']=='running'
    assert result['manifest']['status']=='completed'
    assert result['rows'][0]['baseline_reused']
    assert result['rows'][0]['baseline']['reported_cost_usd'] is None
    assert result['manifest']['protected_files_unchanged']
    with pytest.raises(FileExistsError):bench.run(cp,tmp_path/'out',runner=runner)


def test_confident_light_dispatch_and_private_outputs(tmp_path,monkeypatch):
    cp=tmp_path/'cases.json';cp.write_text(json.dumps([case()]))
    monkeypatch.setattr(jev,'tier_only',lambda *a,**kw:jev.Pick('light',.8,'route'))
    seen=[]
    def runner(cmd,**kw):
        seen.append((cmd,kw['input']))
        raw='{"result":"Mean 4","subtype":"success","usage":{"input_tokens":3},"modelUsage":{"claude-haiku":{}},"total_cost_usd":0.001}' if cmd[0]=='claude' else '{"type":"item.completed","item":{"type":"agent_message","text":"Mean 4"}}'
        return subprocess.CompletedProcess(cmd,0,raw,'')
    got=bench.run(cp,tmp_path/'out',runner=runner)
    assert len(seen)==2 and seen[0][1]==seen[1][1]
    assert got['rows'][0]['selected_model']=='haiku'
    assert '--restricted' in seen[1][0] and '--bare' not in seen[1][0]
    assert (tmp_path/'out'/'manifest.json').stat().st_mode&0o777==0o600


def test_failures_and_input_bounds(tmp_path):
    def timeout(*a,**kw):raise subprocess.TimeoutExpired(a[0],90)
    assert bench.generate('haiku','x',tmp_path,timeout)['failure']=='timeout'
    with pytest.raises(ValueError):bench.render({**case(),'prompt':'x'*16000})
    with pytest.raises(ValueError):bench.render({**case(),'conversation_history':[{'role':'system','content':'bad'}]})
    with pytest.raises(ValueError):bench.parse_output(bench.BASELINE,'{"type":"item.completed","item":{"type":"command_execution"}}')


def test_claude_array_schema():
    raw=json.dumps([{'type':'system','model':'claude-haiku-5-5'},
        {'type':'result','subtype':'success','result':'done','modelUsage':{'claude-haiku-5-5':{'costBasis':'list'}},'total_cost_usd':.01}])
    assert bench.parse_output('haiku',raw)['resolved_models']==['claude-haiku-5-5']


def test_history_metadata_is_not_sent():
    c={**case(),'conversation_history':[{'role':'assistant','content':'prior','source_sha256':'secret-provenance'}]}
    assert 'secret-provenance' not in bench.render(c)
    assert 'prior' in bench.render(c)
