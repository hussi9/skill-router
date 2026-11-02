import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import benchmark_lazycoder_staged as b


def test_canonical_preserves_roles_and_latest():
    c = b.canonical({'current_request': 'do not ship', 'conversation': [
        {'role': 'assistant', 'content': 'Ship?'}, {'role': 'user', 'content': 'yes'},
        {'role': 'user', 'content': 'do not ship'}]})
    assert c['prompt'] == 'do not ship'
    assert len(c['conversation_history']) == 2
    assert c['conversation_history'][0]['role'] == 'assistant'


def test_three_arms_match_source_and_persist_receipts(tmp_path, monkeypatch):
    cases, manifest, protected = [tmp_path / name for name in ('cases.json', 'manifest.json', 'live.json')]
    cases.write_text(json.dumps([{'id': 'fresh', 'prompt': 'latest', 'workflow': 'approval', 'recipe_id': 'summarize-long-thing'}]))
    manifest.write_text('{}')
    protected.write_text('untouched')
    monkeypatch.setattr(b.connector, 'render_contract', lambda *a, **k: {'prompt': 'recipe only'})
    monkeypatch.setattr(b.connector, 'check_outcome', lambda *a, **k: {'verified': False})
    calls = []
    def generate(model, prompt, cwd, *, deadline_seconds):
        calls.append(prompt)
        assert model == b.base.MODEL
        assert deadline_seconds == 120
        return {'answer': 'done', 'failure': '', 'elapsed_ms': 1, 'estimated_cost_usd': .01}
    def workflow(case, contract, model, cwd, deadline, generator):
        result = generator(model, b.base.render(case, contract), cwd, deadline_seconds=deadline)
        result['generation_calls'] = 1
        return result
    monkeypatch.setattr(b.stages, 'workflow', workflow)
    out = tmp_path / 'run'
    receipt = b.run(cases, manifest, out, protected_paths=[protected], generator=generate)
    assert receipt['generation_calls'] == 3
    assert receipt['max_generation_calls'] == 5
    assert receipt['protected_files_unchanged']
    assert calls[0] == calls[1]
    assert calls[2].startswith(calls[0])
    assert 'recipe only' in calls[2]
    assert len(list(out.glob('*-step-*.json'))) == 3
    assert (out / 'manifest.json').stat().st_mode & 0o777 == 0o600


def test_invalid_workflow_before_provider(tmp_path, monkeypatch):
    cases, manifest = tmp_path / 'cases.json', tmp_path / 'manifest.json'
    cases.write_text(json.dumps([{'id': 'fresh', 'prompt': 'latest', 'workflow': 'other', 'recipe_id': 'summarize-long-thing'}]))
    manifest.write_text('{}')
    monkeypatch.setattr(b.connector, 'render_contract', lambda *a, **k: {'prompt': 'recipe'})
    with pytest.raises(ValueError, match='workflow'):
        b.run(cases, manifest, tmp_path / 'out', generator=lambda *a, **k: pytest.fail('called'))
    assert not (tmp_path / 'out').exists()


def test_cap_stops_extra_calls_and_records_interruption(tmp_path, monkeypatch):
    cases, manifest = tmp_path / 'cases.json', tmp_path / 'manifest.json'
    cases.write_text(json.dumps([{'id': 'fresh', 'prompt': 'latest', 'workflow': 'approval', 'recipe_id': 'summarize-long-thing'}]))
    manifest.write_text('{}')
    monkeypatch.setattr(b.connector, 'render_contract', lambda *a, **k: {'prompt': 'recipe'})
    monkeypatch.setattr(b.connector, 'check_outcome', lambda *a, **k: {})
    calls = []
    def generate(*a, **kw):
        calls.append(1)
        return {'answer': 'ok', 'failure': ''}
    def workflow(case, contract, model, cwd, deadline, generator):
        for _ in range(3):
            generator(model, 'p', cwd, deadline_seconds=deadline)
    monkeypatch.setattr(b.stages, 'workflow', workflow)
    out = tmp_path / 'out'
    with pytest.raises(RuntimeError, match='cap'):
        b.run(cases, manifest, out, generator=generate)
    assert len(calls) == 3  # one current plus two staged, fourth rejected
    record = json.loads((out / 'manifest.json').read_text())
    assert record['generation_calls'] == 3
    assert record['status'] == 'interrupted'
