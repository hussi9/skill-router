import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import router
import task_brief
import skill_invoked
import iron_rule_hook


def test_brief_never_reads_latest(tmp_path, monkeypatch):
    monkeypatch.setenv('SKILL_ROUTER_SESSION_DIR', str(tmp_path))
    (tmp_path / 'latest.json').write_text(json.dumps({'primary': 'other'}))
    assert task_brief.load_route('absent') == {}
    assert task_brief.load_route('') == {}


def test_pending_sessions_are_independent(tmp_path, monkeypatch):
    from session_state import pending_path
    monkeypatch.setenv('SKILL_ROUTER_SESSION_DIR', str(tmp_path))
    a = pending_path(router.PENDING, 'a')
    b = pending_path(router.PENDING, 'b')
    a.parent.mkdir(parents=True, exist_ok=True)
    for sid, p in [('a', a), ('b', b)]:
        p.write_text(json.dumps({'session_id': sid, 'primary': 'refactor', 'remaining': ['refactor']}))
    skill_invoked.satisfy_pending('refactor', 'a')
    assert json.loads(a.read_text())['remaining'] == []
    assert json.loads(b.read_text())['remaining'] == ['refactor']
    assert iron_rule_hook.read_pending_state('b')['remaining'] == ['refactor']
    router.clear_pending('a')
    assert json.loads(b.read_text())['remaining'] == ['refactor']
    monkeypatch.setattr(router, 'OVERRIDES_LOG', tmp_path / 'overrides.jsonl')
    monkeypatch.setattr(router, 'OVERRIDES_COUNT', tmp_path / 'counts.json')
    router.record_override('wrong route', session_id='a')
    assert json.loads(b.read_text())['remaining'] == ['refactor']


def test_session_key_collision_resistant(tmp_path):
    from session_state import pending_path
    assert pending_path(tmp_path / 'pending.json', 'a/b') != pending_path(tmp_path / 'pending.json', 'a?b')


def test_missing_session_does_not_touch_live_pending(monkeypatch, tmp_path):
    from session_state import pending_path
    live = Path.home() / '.claude' / 'skill_router_pending.json'
    monkeypatch.setenv('SKILL_ROUTER_SESSION_DIR', str(tmp_path))
    assert pending_path(live, '') is None


def test_simultaneous_route_writes_and_reads(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from session_state import pending_path
    monkeypatch.setenv('SKILL_ROUTER_SESSION_DIR', str(tmp_path))
    def write(sid):
        router.write_pending([router.Step('skill-' + sid)], 'OPERATE', [], {'session_id': sid})
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(write, ['alpha', 'beta']))
    for sid in ['alpha', 'beta']:
        assert json.loads(pending_path(router.PENDING, sid).read_text())['primary'] == 'skill-' + sid
        assert task_brief.load_route(sid)['primary'] == 'skill-' + sid
    assert not (tmp_path / 'latest.json').exists()


def test_missing_identity_hooks_fail_open(tmp_path):
    import os
    import subprocess
    root = Path(__file__).resolve().parents[1]
    pending = tmp_path / '.claude' / 'skill_router_pending.json'
    pending.parent.mkdir(parents=True)
    original = json.dumps({'primary': 'alpha', 'remaining': ['alpha']})
    pending.write_text(original)
    env = {**os.environ, 'HOME': str(tmp_path)}
    for script, args, payload in [
        ('iron_rule_hook.py', ['pre'], {'tool_name': 'Edit'}),
        ('iron_rule_hook.py', ['stop'], {}),
        ('skill_invoked.py', [], {'tool_input': {'skill': 'alpha'}}),
    ]:
        proc = subprocess.run([sys.executable, str(root / 'scripts' / script), *args], input=json.dumps(payload), text=True, capture_output=True, env=env)
        assert proc.returncode == 0, proc.stderr
        assert pending.read_text() == original
    proc = subprocess.run([sys.executable, str(root / 'scripts/router_override.py'), 'wrong route'], capture_output=True, text=True, env=env)
    assert proc.returncode == 2
    assert pending.read_text() == original


def test_identified_hook_enforces_own_rule(tmp_path):
    import os
    import subprocess
    from session_state import pending_path
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, 'HOME': str(tmp_path), 'SKILL_ROUTER_SESSION_DIR': str(tmp_path / 'sessions')}
    target = tmp_path / 'sessions' / 'own.pending.json'
    target.parent.mkdir()
    target.write_text(json.dumps({'session_id': 'own', 'primary': 'alpha', 'remaining': ['alpha']}))
    (tmp_path / '.claude/skills/alpha').mkdir(parents=True)
    (tmp_path / '.claude/skills/alpha/SKILL.md').write_text('alpha')
    for sid, expected in [('other', False), ('own', True)]:
        proc = subprocess.run([sys.executable, str(root / 'scripts/iron_rule_hook.py'), 'stop'], input=json.dumps({'session_id': sid}), text=True, capture_output=True, env=env)
        assert proc.returncode == 0, proc.stderr
        assert bool(proc.stdout.strip()) == expected, proc.stderr
        if expected:
            assert '--session-id' in json.loads(proc.stdout)['reason']


def test_new_turn_clears_brief_before_escape_or_skip(tmp_path, monkeypatch):
    import io
    from contextlib import redirect_stdout
    monkeypatch.setenv('SKILL_ROUTER_SESSION_DIR', str(tmp_path))
    monkeypatch.setenv('SKILL_ROUTER_HOOK_MODE', '1')
    monkeypatch.delenv('CLAUDE_USER_INPUT', raising=False)
    monkeypatch.setenv('SKILL_ROUTER_NO_LEARN', '1')
    monkeypatch.setattr(router, 'STRIKES', tmp_path / 'strikes.json')
    monkeypatch.setattr(router, 'route', lambda prompt: ('SKIP', [], [], ''))
    loaded = tmp_path / 'same.loaded'
    loaded.write_text('already-loaded\n')
    for prompt in ['[no-router] continue', 'thanks']:
        router.write_pending([router.Step('old-skill')], 'OPERATE', [], {'session_id': 'same'})
        # Also cover a route surviving after its pending file was removed.
        (tmp_path / 'same.pending.json').unlink()
        assert task_brief.load_route('same')['primary'] == 'old-skill'
        monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps({'session_id': 'same', 'prompt': prompt})))
        with redirect_stdout(io.StringIO()):
            assert router.main() == 0
        assert task_brief.load_route('same') == {}
        monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps({
            'session_id': 'same', 'tool_name': 'Task',
            'tool_input': {'prompt': 'handle the follow-up', 'model': 'sonnet'},
        })))
        output = io.StringIO()
        with redirect_stdout(output):
            assert task_brief.main() == 0
        assert output.getvalue() == ''
        assert loaded.read_text() == 'already-loaded\n'


def test_override_clears_own_brief_only(tmp_path, monkeypatch):
    monkeypatch.setenv('SKILL_ROUTER_SESSION_DIR', str(tmp_path))
    monkeypatch.setattr(router, 'OVERRIDES_LOG', tmp_path / 'overrides.jsonl')
    monkeypatch.setattr(router, 'OVERRIDES_COUNT', tmp_path / 'counts.json')
    for sid in ['a', 'b']:
        router.write_pending([router.Step('old-skill')], 'OPERATE', [], {'session_id': sid})
        (tmp_path / (sid + '.loaded')).write_text('keep\n')
    router.record_override('irrelevant', session_id='a')
    assert task_brief.load_route('a') == {}
    assert task_brief.load_route('b')['primary'] == 'old-skill'
    assert (tmp_path / 'a.loaded').read_text() == 'keep\n'


def test_full_continuation_context_reaches_jev(tmp_path, monkeypatch):
    import jev_choose
    context = 'Security constraint at the beginning. ' + 'context ' * 650
    transcript = tmp_path / 'transcript.jsonl'
    transcript.write_text(json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': context}]}}) + '\n')
    captured = {}
    monkeypatch.setenv('SKILL_ROUTER_JEV', '1')
    monkeypatch.setenv('SKILL_ROUTER_LLM', '1')
    monkeypatch.setattr(router, 'PREV_ASSISTANT', router.previous_assistant_tail({'transcript_path': str(transcript)}))
    def choose(prompt, **kwargs):
        captured.update(kwargs)
        return object()
    monkeypatch.setattr(jev_choose, 'choose', choose)
    router._jev_decide('yes continue')
    assert captured['context'] == context.strip()
    assert len(captured['context']) > 4000
    assert router.previous_assistant_tail({'transcript_path': str(transcript)}, chars=300) == context.strip()[-300:]


def test_quota_markers_do_not_collide(tmp_path, monkeypatch):
    monkeypatch.setenv('SKILL_ROUTER_SESSION_DIR', str(tmp_path))
    monkeypatch.setenv('SKILL_ROUTER_HOOK_MODE', '1')
    for sid in ['a/b', 'a?b']:
        monkeypatch.setattr(router, 'SESSION_ID', sid)
        assert router._quota_hint_due('warning')
        assert not router._quota_hint_due('warning')
