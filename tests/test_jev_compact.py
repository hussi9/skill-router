import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import jev_compact as compact
import jev_source_v3 as source
import benchmark_compact as bench
from test_benchmark_source_v3 import fixtures,provider,rows


def test_bounds_negative_scope_and_provenance():
    evidence='Explicit scope sections:\n## Use when\nBuild dashboards. '+('More setup details. '*200)+'\nDo not publish or spend money.\nIntroduction:\nDashboard builder.\nFrontmatter:\nRepeated description.'
    result=compact.compact_evidence(evidence)
    assert len(result)<=1000 and 'Do not publish or spend money.' in result
    assert 'Dashboard builder.' in result
    assert compact.compact_evidence('')==''


def test_snapshot_preserves_provenance(tmp_path):
    import hashlib
    path=tmp_path/"skill.md"
    path.write_text("# Alpha\nIntro text.")
    sha=hashlib.sha256(path.read_bytes()).hexdigest()
    record={'name':'alpha','status':'source','source_path':str(path),'source_sha256':sha,'evidence':'Intro text.'}
    original={'version':1,'skills':{'alpha':record}}
    result=compact.build_snapshot(original)
    assert original['skills']['alpha']['evidence']=='Intro text.'
    assert result['skills']['alpha']['source_sha256']==sha
    assert compact.build_snapshot(original)==result


def test_question_parity_outside_evidence():
    entries=[{'name':'alpha','kind':'domain','type':'skill','description':'Build '+('alpha '*200),'use_when':['Build dashboards']}]
    snapshot={'version':1,'skills':{}}
    a,_=source.build_questions(entries,['alpha'],snapshot)
    b,_=source.build_questions(compact.compact_entries(entries),['alpha'],compact.build_snapshot(snapshot))
    assert a['needs_specialist']==b['needs_specialist']
    assert a['fit_0']['instructions']==b['fit_0']['instructions']
    for key in ('true','false'):
        assert a['fit_0']['criteria'][key].split('Evidence JSON:')[0]==b['fit_0']['criteria'][key].split('Evidence JSON:')[0]
    assert len(json.dumps(b))<len(json.dumps(a))


def test_real_harness_shared_pool_and_telemetry(tmp_path,monkeypatch):
    cp,ip,fp,out=fixtures(tmp_path,monkeypatch)
    sp=tmp_path/'compact.json';sp.write_text(json.dumps(compact.build_snapshot(json.loads(fp.read_text()))))
    monkeypatch.setattr(compact.source.applicability.base,'_post',provider(process=.2))
    result=bench.run(cp,ip,fp,sp,out,max_calls=3)
    rr=rows(out)
    assert rr['source_full']['shortlist']==rr['source_compact']['shortlist']==['alpha']
    assert all(result[a]['primary']['positive_hits']==1 for a in bench.ARMS)
    stages=[json.loads(x)for x in(out/'stages.jsonl').read_text().splitlines()]
    assert all(s['request_bytes']>0 and s['question_count']>0 for s in stages)
    assert sum(s['input_tokens'] for s in stages)==9


def test_late_accounting_drained_without_late_routes(tmp_path,monkeypatch):
    import time
    cp,ip,fp,out=fixtures(tmp_path,monkeypatch)
    sp=tmp_path/'compact.json';sp.write_text(json.dumps(compact.build_snapshot(json.loads(fp.read_text()))))
    base=compact.source.applicability.base
    original_deadline=base._deadline
    real=provider(process=.2)
    def post(body,key,timeout):
        if 'needs_specialist' in body['questions']:
            time.sleep(.04)
        return real(body,key,timeout)
    counter=[0]
    def short_test_deadline(fn,seconds):
        counter[0]+=1
        return fn() if counter[0]==1 else original_deadline(fn,.001)
    monkeypatch.setattr(base,'_post',post)
    monkeypatch.setattr(base,'_deadline',short_test_deadline)
    result=bench.run(cp,ip,fp,sp,out,max_calls=3)
    manifest=json.loads((out/'manifest.json').read_text())
    assert manifest['pending_transport_calls']==0
    assert manifest['actual_input_tokens']==9 and manifest['recorded_stages']==3
    assert result['source_full']['primary']['failures']==result['source_compact']['primary']['failures']==1
    assert rows(out)['source_compact']['prediction'] is None


def test_exact_scope_heading_and_safety_capture():
    text = "# Skill\nBuild docs.\n## When AskUserQuestion is unavailable\nDo not ask again.\n## When to use\nWrite technical documents.\n## Non-negotiable safety model\nNever delete active files."
    extracted=compact.authored_evidence(text)
    assert 'AskUserQuestion' not in extracted and 'Write technical documents.' in extracted
    assert 'Never delete active files.' in compact.compact_evidence(extracted)
