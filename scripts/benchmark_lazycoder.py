"""Local paired Claude experiment. Never installs a skill or changes routing."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time

import lazycoder_connector as connector

THRESHOLD = 0.8
MODEL = "claude-opus-5-5"
DEFAULT_DEADLINE_SECONDS = 180
SYSTEM = ('Answer the latest user request using supplied information only. '
          'Earlier conversation is context; latest corrections take precedence. '
          'Do not browse, call tools, send messages, or change files. '
          'Do not invent missing source facts. Return the requested deliverable directly.')


def digest(path):
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def write(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def render(case, contract=None):
    if not isinstance(case.get('prompt'), str) or not case['prompt'].strip():
        raise ValueError('current request required')
    history = case.get('conversation_history', [])
    if not isinstance(history, list):
        raise ValueError('history must be an array')
    for m in history:
        if m.get('role') not in ('user', 'assistant') or not isinstance(m.get('content'), str):
            raise ValueError('invalid history')
    state = {'conversation_history': history, 'current_user_request': case['prompt']}
    # Ingredients come from the same real source in BOTH arms; only the recipe differs.
    if case.get('inputs'):
        state['supplied_inputs'] = case['inputs']
    text = SYSTEM + '\n\n' + json.dumps(state, ensure_ascii=False)
    if contract is not None:
        text += '\n\nSelected LazyCoder recipe contract (offline preview):\n' + contract
        text += '\nApply the recipe only within the latest user request; do not revive superseded instructions.'
    if len(text) > 120000:
        raise ValueError('input too large; never truncate')
    return text


def _events(stdout):
    """Accept complete JSON or streaming NDJSON, retaining complete timeout events."""
    text = stdout.decode('utf-8', errors='replace') if isinstance(stdout, bytes) else (stdout or '')
    try:
        value = json.loads(text)
        return (value if isinstance(value, list) else [value]), len(text.encode()), 0
    except ValueError:
        events, malformed = [], 0
        for line in text.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                malformed += 1
        return events, len(text.encode()), malformed


def _observe(result, stdout):
    events, byte_count, malformed = _events(stdout)
    events = [e for e in events if isinstance(e, dict)]
    deltas, completed_messages = [], []
    for event in events:
        if event.get('type') == 'stream_event':
            delta = event.get('event', {}).get('delta', {})
            if delta.get('type') == 'text_delta':
                deltas.append(delta.get('text', ''))
        elif event.get('type') == 'assistant':
            blocks = event.get('message', {}).get('content', [])
            completed_messages.append(''.join(b.get('text', '') for b in blocks if b.get('type') == 'text'))
    partial = ''.join(completed_messages) if any(completed_messages) else ''.join(deltas)
    result['progress'] = {'stdout_bytes': byte_count, 'complete_events': len(events),
                          'malformed_or_incomplete_lines': malformed,
                          'event_types': sorted({str(e.get('type', 'unknown')) for e in events}),
                          'partial_text_characters': len(partial)}
    result['partial_answer'] = partial
    return next((e for e in reversed(events) if e.get('type') == 'result'), None)


def _deadline(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 1 <= value <= 600:
        raise ValueError('deadline must be finite and between 1 and 600 seconds')
    return value


def generate(model, prompt, cwd, runner=subprocess.run, *, deadline_seconds=DEFAULT_DEADLINE_SECONDS):
    if model != MODEL:
        raise ValueError(f'paired pilot model is pinned to {MODEL}')
    deadline_seconds = _deadline(deadline_seconds)
    # stream-json + partial messages makes stalled generations observable even if
    # the process is killed before its final result. --bare would disable native
    # OAuth/keychain auth, so preserve native auth with restricted/safe mode instead.
    cmd = ['claude', '-p', '--restricted', '--safe-mode', '--tools', '', '--strict-mcp-config',
           '--mcp-config', '{"mcpServers":{}}', '--no-session-persistence',
           '--disable-slash-commands', '--output-format', 'stream-json', '--verbose',
           '--include-partial-messages', '--system-prompt', SYSTEM,
           '--settings', '{"disableAllHooks":true}', '--setting-sources', 'user',
           '--model', model, '--max-budget-usd', '0.80']
    start = time.monotonic()
    result = {'answer': '', 'partial_answer': '', 'failure': '', 'requested_model': model,
              'deadline_seconds': deadline_seconds, 'completion_received': False,
              'progress': {'stdout_bytes': 0, 'complete_events': 0,
                           'malformed_or_incomplete_lines': 0, 'event_types': [], 'partial_text_characters': 0}}
    try:
        p = runner(cmd, input=prompt, text=True, capture_output=True, cwd=str(cwd),
                   timeout=deadline_seconds, check=False,
                   env={**os.environ, 'SKILL_ROUTER_OFF': '1', 'SKILL_ROUTER_NO_LEARN': '1'})
        d = _observe(result, p.stdout)
        result['stderr_bytes'] = len((p.stderr or '').encode())
        if p.returncode:
            result['failure'] = f'process_exit_{p.returncode}'
        elif d is None:
            result['failure'] = 'missing_final_result'
        else:
            result['completion_received'] = True
            result.update(usage=d.get('usage', {}), resolved_models=list(d.get('modelUsage', {})),
                          estimated_cost_usd=d.get('total_cost_usd'))
            if d.get('is_error') or d.get('subtype') not in (None, 'success'):
                result['failure'] = str(d.get('subtype', 'provider_error'))
            elif result['resolved_models'] != [model]:
                result['failure'] = 'resolved_model_mismatch'
            elif not str(d.get('result', '')).strip():
                result['failure'] = 'empty_answer'
            else:
                result['answer'] = d['result']
    except subprocess.TimeoutExpired as e:
        result['failure'] = 'timeout'
        d = _observe(result, e.output)
        result['stderr_bytes'] = len(e.stderr or b'')
        result['completion_received'] = d is not None
        # Timeout is always a failed generation; even a captured final result is
        # not accepted after the deadline. Partial data is diagnostic only.
        if d:
            result['partial_answer'] = str(d.get('result', '')) or result['partial_answer']
            result['usage'] = d.get('usage', {})
            result['estimated_cost_usd'] = d.get('total_cost_usd')
    except (OSError, ValueError, TypeError, AttributeError) as e:
        result['failure'] = type(e).__name__
    result['elapsed_ms'] = round((time.monotonic() - start) * 1000, 3)
    return result


def run(cases_path, manifest_path, out, model, *, protected_paths=(), runner=subprocess.run, deadline_seconds=DEFAULT_DEADLINE_SECONDS):
    if model != MODEL:
        raise ValueError(f'paired pilot model is pinned to {MODEL}')
    deadline_seconds = _deadline(deadline_seconds)
    cases_path, manifest_path, out = map(Path, (cases_path, manifest_path, out))
    source_cases = json.loads(cases_path.read_text())
    cases = source_cases.get('cases') if isinstance(source_cases, dict) else source_cases
    manifest = json.loads(manifest_path.read_text())
    connector.validate_manifest(manifest)
    if not isinstance(cases, list) or not 1 <= len(cases) <= 6:
        raise ValueError('one to six frozen cases required')
    ids = [c.get('id') for c in cases]
    if any(not isinstance(i, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', i) for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('unique safe slug case ids required')
    prompts = []
    adapters = {}
    for c in cases:
        if 'current_request' in c:
            c = dict(c)
            history = list(c.get('conversation', []))
            if history and history[-1].get('content') == c['current_request']:
                history.pop()
            c['conversation_history'] = history
            c['prompt'] = c['current_request']
        contract = connector.render_contract(manifest, c['recipe_id'], c.get('inputs'), preview=True)
        adapters[c['id']] = {'version': contract['adapter_version'], 'fingerprint': contract['adapter_fingerprint']}
        prompts.append({'without_lazycoder': render(c), 'with_lazycoder': render(c, contract['prompt'])})
    watched = [Path(__file__), Path(connector.__file__), cases_path, manifest_path,
               *map(Path, protected_paths)]
    before = {str(p.resolve()): digest(p) for p in watched}
    out.mkdir(mode=0o700, parents=False, exist_ok=False)
    record = {'status': 'running', 'threshold': THRESHOLD, 'model': model,
              'arms': ['without_lazycoder', 'with_lazycoder'], 'local_adapters': adapters, 'max_generation_calls': 2 * len(cases),
              'generation_calls': 0, 'retries': 0, 'per_call_estimated_budget_usd': .8,
              'provider_timeout_seconds': deadline_seconds, 'output_observability': 'stream-json partial messages; final result required', 'hashes_before': before,
              'selection': 'oracle explicit recipe IDs; automatic JEV selection NOT measured',
              'cost_basis': 'CLI API-equivalent estimate, not actual subscription billing',
              'live_routing_changed': False, 'review': 'independent semantic review required'}
    write(out / 'manifest-start.json', record)
    try:
        for n, (case, pair) in enumerate(zip(cases, prompts)):
            outputs = {}
            order = list(pair) if n % 2 == 0 else list(reversed(pair))
            for arm in order:
                with tempfile.TemporaryDirectory(prefix='lazycoder-pair-') as cwd:
                    outputs[arm] = generate(model, pair[arm], cwd, runner, deadline_seconds=deadline_seconds)
                record['generation_calls'] += 1
                if not outputs[arm]['failure']:
                    outputs[arm]['checks'] = connector.check_outcome(
                        manifest, case['recipe_id'], outputs[arm]['answer'], case.get('inputs'))
                write(out / f'{case["id"]}-{arm}.json', outputs[arm])
            write(out / f'case-{n+1:02d}.json', {'id': case['id'], 'outputs': outputs,
                  'prompt_hashes': {arm: hashlib.sha256(p.encode()).hexdigest() for arm, p in pair.items()}})
        record['status'] = 'completed'
    finally:
        record['hashes_after'] = {str(p.resolve()): digest(p) for p in watched}
        record['protected_files_unchanged'] = before == record['hashes_after']
        write(out / 'manifest.json', record)
    return record


if __name__ == '__main__':
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--cases', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--model', required=True)
    p.add_argument('--protect', action='append', default=[])
    p.add_argument('--deadline-seconds', type=float, default=DEFAULT_DEADLINE_SECONDS)
    a = p.parse_args()
    print(json.dumps(run(a.cases, a.manifest, a.out, a.model, protected_paths=a.protect, deadline_seconds=a.deadline_seconds)))
