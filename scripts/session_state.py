"""Session-scoped hook state. Never infer identity from global latest state."""
import hashlib
import os
import re
from pathlib import Path


def session_key(session_id: str) -> str:
    sid = str(session_id or '').strip()
    if not sid:
        return ''
    safe = re.sub(r'[^A-Za-z0-9_-]', '_', sid)[:80]
    if safe != sid:
        safe += '-' + hashlib.sha256(sid.encode()).hexdigest()[:16]
    return safe


def pending_path(legacy: Path, session_id: str = '') -> Path | None:
    key = session_key(session_id)
    if key:
        directory = Path(os.environ.get('SKILL_ROUTER_SESSION_DIR') or Path.home() / '.claude' / 'skill_router_session')
        return directory / (key + '.pending.json')
    # Explicitly redirected fixture paths retain the old test-only contract.
    if legacy != Path.home() / '.claude' / 'skill_router_pending.json':
        return legacy
    return None
