import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import benchmark_lazycoder as b


def test_identical_source_and_only_contract_differs():
    case = {'prompt': 'Now omit names.', 'inputs': {'notes': 'source words'},
            'conversation_history': [{'role': 'user', 'content': 'Summarize notes with names.'}]}
    plain = b.render(case)
    enhanced = b.render(case, 'exact recipe')
    assert enhanced.startswith(plain)
    assert 'source words' in plain
    assert 'exact recipe' not in plain
    assert 'latest corrections' in plain


def test_model_pin_and_tool_isolation(tmp_path):
    def runner(cmd, **kw):
        assert cmd[cmd.index('--model') + 1] == b.MODEL
        assert cmd[cmd.index('--tools') + 1] == ''
        assert '--no-session-persistence' in cmd
        assert '--disable-slash-commands' in cmd
        assert kw['env']['SKILL_ROUTER_OFF'] == '1'
        assert kw['cwd'] == str(tmp_path)
        return subprocess.CompletedProcess(cmd, 0, json.dumps([{
            'type': 'result', 'subtype': 'success', 'result': 'done',
            'modelUsage': {b.MODEL: {}}, 'total_cost_usd': .02}]), '')
    result = b.generate(b.MODEL, 'task', tmp_path, runner)
    assert not result['failure']
    assert result['estimated_cost_usd'] == .02


def test_model_drift_and_provider_errors_fail(tmp_path):
    def runner(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, json.dumps({
            'type': 'result', 'result': 'done', 'modelUsage': {'other-model': {}}}), '')
    assert b.generate(b.MODEL, 'task', tmp_path, runner)['failure'] == 'resolved_model_mismatch'
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired('claude', 90)
    assert b.generate(b.MODEL, 'task', tmp_path, timeout)['failure'] == 'timeout'


def test_no_silent_truncation():
    import pytest
    with pytest.raises(ValueError, match='never truncate'):
        b.render({'prompt': 'x' * 120001})


def test_unsafe_case_id_rejected_before_call(tmp_path, monkeypatch):
    import pytest
    cases = tmp_path / 'cases.json'
    manifest = tmp_path / 'snapshot.json'
    cases.write_text(json.dumps([{'id': '../escape', 'prompt': 'test'}]))
    manifest.write_text('{}')
    monkeypatch.setattr(b.connector, 'validate_manifest', lambda m: None)
    with pytest.raises(ValueError, match='safe slug'):
        b.run(cases, manifest, tmp_path / 'outputs', b.MODEL, runner=lambda *a, **k: pytest.fail('provider called'))


def test_paired_run_freezes_inputs_counts_and_preserves_files(tmp_path, monkeypatch):
    cases, manifest, protect = [tmp_path / p for p in ('cases.json', 'source.json', 'live-settings.json')]
    cases.write_text(json.dumps({'cases': [{'id': 'real-case', 'recipe_id': 'recipe',
        'current_request': 'latest correction', 'prompt': 'legacy combined prompt',
        'conversation': [{'role': 'user', 'content': 'earlier'},
                         {'role': 'user', 'content': 'latest correction'}]}]}))
    manifest.write_text('{}')
    protect.write_text('unchanged')
    monkeypatch.setattr(b.connector, 'validate_manifest', lambda m: None)
    monkeypatch.setattr(b.connector, 'render_contract', lambda *a, **k: {'prompt': 'full contract', 'adapter_version': 3, 'adapter_fingerprint': 'f' * 64})
    monkeypatch.setattr(b.connector, 'check_outcome', lambda *a, **k: {'verified': False})
    seen = []
    def runner(cmd, **kw):
        seen.append(kw['input'])
        return subprocess.CompletedProcess(cmd, 0, json.dumps({'type': 'result', 'result': 'answer',
            'modelUsage': {b.MODEL: {}}, 'total_cost_usd': .01}), '')
    out = tmp_path / 'results'
    result = b.run(cases, manifest, out, b.MODEL, protected_paths=[protect], runner=runner)
    assert result['local_adapters']['real-case']['version'] == 3
    assert result['generation_calls'] == 2
    assert result['protected_files_unchanged']
    assert seen[1].startswith(seen[0])
    assert 'legacy combined prompt' not in seen[0]
    assert seen[0].count('latest correction') == 2  # system precedence + current request
    assert 'full contract' not in seen[0]
    assert (out / 'real-case-with_lazycoder.json').stat().st_mode & 0o777 == 0o600


def test_streaming_success_requires_final_and_uses_shared_bounds(tmp_path):
    def runner(cmd, **kw):
        assert kw['timeout'] == 180
        assert cmd[cmd.index('--output-format') + 1] == 'stream-json'
        assert '--include-partial-messages' in cmd
        assert '--safe-mode' in cmd
        assert '--bare' not in cmd  # native OAuth/keychain remains available
        assert cmd[cmd.index('--system-prompt') + 1] == b.SYSTEM
        events = [
            {'type': 'stream_event', 'event': {'delta': {'type': 'text_delta', 'text': 'part'}}},
            {'type': 'result', 'subtype': 'success', 'result': 'complete', 'modelUsage': {b.MODEL: {}}},
        ]
        return subprocess.CompletedProcess(cmd, 0, '\n'.join(json.dumps(e) for e in events), '')
    result = b.generate(b.MODEL, 'task', tmp_path, runner)
    assert result['answer'] == 'complete'
    assert result['completion_received']
    assert result['progress']['complete_events'] == 2
    assert result['partial_answer'] == 'part'


def test_timeout_keeps_partial_bytes_without_scoring_answer(tmp_path):
    def runner(cmd, **kw):
        assert kw['timeout'] == 240
        chunk = json.dumps({'type': 'stream_event', 'event': {'delta': {
            'type': 'text_delta', 'text': '<!doctype html>\n<html>'}}}).encode()
        raise subprocess.TimeoutExpired(cmd, kw['timeout'], output=chunk + b'\n{"type":', stderr=b'progress')
    result = b.generate(b.MODEL, 'task', tmp_path, runner, deadline_seconds=240)
    assert result['failure'] == 'timeout'
    assert result['answer'] == ''
    assert result['partial_answer'] == '<!doctype html>\n<html>'
    assert result['progress']['malformed_or_incomplete_lines'] == 1
    assert result['stderr_bytes'] == 8
    assert not result['completion_received']


def test_stream_only_and_error_final_never_succeed(tmp_path):
    def runner(cmd, **kw):
        event = {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'partial'}]}}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(event), '')
    result = b.generate(b.MODEL, 'task', tmp_path, runner)
    assert result['failure'] == 'missing_final_result'
    assert result['answer'] == ''
    assert result['partial_answer'] == 'partial'
    def error(cmd, **kw):
        event = {'type': 'result', 'subtype': 'error_max_budget_usd', 'result': 'unfinished', 'modelUsage': {b.MODEL: {}}}
        return subprocess.CompletedProcess(cmd, 0, json.dumps(event), '')
    result = b.generate(b.MODEL, 'task', tmp_path, error)
    assert result['failure'] == 'error_max_budget_usd'
    assert result['answer'] == ''


def test_invalid_model_or_deadline_rejected_before_provider(tmp_path):
    import pytest
    def runner(*a, **k):
        pytest.fail('provider must not be called')
    with pytest.raises(ValueError, match='pinned'):
        b.generate('opus', 'task', tmp_path, runner)
    for deadline in (0, 601, float('nan'), True):
        with pytest.raises(ValueError, match='deadline'):
            b.generate(b.MODEL, 'task', tmp_path, runner, deadline_seconds=deadline)


def test_timeout_after_final_is_still_failure(tmp_path):
    def runner(cmd, **kw):
        event = {'type': 'result', 'subtype': 'success', 'result': 'final seen but process hung', 'modelUsage': {b.MODEL: {}}}
        raise subprocess.TimeoutExpired(cmd, 180, output=json.dumps(event))
    result = b.generate(b.MODEL, 'task', tmp_path, runner)
    assert result['failure'] == 'timeout'
    assert result['completion_received']
    assert result['answer'] == ''
    assert result['partial_answer'] == 'final seen but process hung'
