import json
import pytest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import benchmark_ablation as bench
import jev_choose as jev
from test_benchmark_source_v3 import fixtures, provider, rows


@pytest.mark.parametrize('mode', ['shortlist','scoring'])
def test_controlled_pools_and_formats(tmp_path, monkeypatch, mode):
    cp, ip, sp, out = fixtures(tmp_path, monkeypatch)
    seen = []
    base = provider(process=.2)
    def post(body,key,timeout):
        seen.append(body)
        answer = base(body,key,timeout)
        for qid,q in body['questions'].items():
            if qid.startswith('fit_') and q['type'] == 'choice':
                answer['answers'][qid] = {'type':'choice','choice':'supported','confidence':.95,
                    'probabilities':{'supported':.95,'unsupported':.05}}
        return answer
    monkeypatch.setattr(jev,'_post',post)
    calls=[]
    def expand(prompt,context,entries,index_path,baseline):
        calls.append(list(baseline))
        return list(baseline)
    monkeypatch.setattr(bench.shortlist_reuse,'expand',expand)
    summary = bench.run(mode,cp,ip,sp,out,max_calls=3)
    assert len(seen)==3 and len(calls)==(1 if mode=='shortlist' else 2)
    rr=rows(out)
    arms=[a for a in rr if a!='current']
    assert rr[arms[0]]['shortlist']==rr[arms[1]]['shortlist']
    assert all(summary[a]['primary']['n_correct']==1 for a in arms)
    assert seen[1]['state']==seen[2]['state']
    assert seen[1]['questions']['needs_specialist']==seen[2]['questions']['needs_specialist']
    manifest=json.loads((out/'manifest.json').read_text())
    assert manifest['provider_attempts']==3 and manifest['actual_input_tokens']==9
    assert len(manifest['shortlist_builders'])==len(calls)


def test_bounds_precede_calls(tmp_path, monkeypatch):
    cp,ip,sp,out=fixtures(tmp_path,monkeypatch)
    with pytest.raises(ValueError):
        bench.run('scoring',cp,ip,sp,out,max_calls=37)
    assert not out.exists()


def test_retrieval_changes_only_treatment_pool(tmp_path, monkeypatch):
    cp,ip,sp,out=fixtures(tmp_path,monkeypatch)
    index=json.loads(ip.read_text())
    index['entries'].append({'name':'beta','kind':'domain','type':'skill','description':'Other specialist'})
    ip.write_text(json.dumps(index))
    original=provider(process=.2)
    bodies=[]
    def post(body,key,timeout):
        bodies.append(body)
        response=original(body,key,timeout)
        if 'domain' in response['answers']:
            response['answers']['domain']['probabilities']['beta']=.01
        return response
    monkeypatch.setattr(jev,'_post',post)
    monkeypatch.setattr(bench.shortlist_reuse,'expand',lambda prompt,context,entries,index_path,baseline: ['alpha','beta'])
    bench.run('shortlist',cp,ip,sp,out,max_calls=3)
    rr=rows(out)
    assert rr['source_fixed']['shortlist']==['alpha']
    assert rr['source_retrieval']['shortlist']==['alpha','beta']
    assert bodies[1]['state']==bodies[2]['state']
    assert bodies[1]['questions']['needs_specialist']==bodies[2]['questions']['needs_specialist']
