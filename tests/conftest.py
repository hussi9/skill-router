"""Suite-wide isolation for the outcomes log, the Jev features file and the embedder.

Any test that drives a hook (router in hook mode, skill_invoked.py, the Bash
PostToolUse hook) would otherwise append to the developer's live
~/.claude/skill_router_outcomes.jsonl. Subprocesses inherit os.environ, so
pointing the override at a temp file here covers them too.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_OUTCOMES_DIR = Path(tempfile.mkdtemp(prefix="router-outcomes-"))
# router binds SESSION_DIR at import time, so this must precede any router import.
os.environ["SKILL_ROUTER_SESSION_DIR"] = str(_OUTCOMES_DIR / "sessions")
os.environ["SKILL_ROUTER_OUTCOMES_FILE"] = str(_OUTCOMES_DIR / "outcomes.jsonl")

# Hook-mode tests must not be randomly silenced by the 10% holdout; the
# holdout tests set their own rate.
os.environ["SKILL_ROUTER_HOLDOUT"] = "0"

# Jev distillation rows go to a temp file, never the live cache; tests that check
# the rows point the override at their own tmp_path.
os.environ["SKILL_ROUTER_FEATURES_FILE"] = str(_OUTCOMES_DIR / "jev-features.jsonl")

# No test reaches the live embedding daemon by default; tests that need the daemon
# path unset this with monkeypatch.
os.environ["SKILL_ROUTER_NO_EMBED"] = "1"

# Payload-shape capture is opt-in per session; if a test turns it on it must not
# write to the live cache.
os.environ["SKILL_ROUTER_SHAPES_FILE"] = str(_OUTCOMES_DIR / "payload-shapes.jsonl")


# --- Live-data guard -------------------------------------------------------
# Tests reached the developer's live ~/.claude more than once. This audit hook
# makes that impossible: any open/listdir/scandir under the REAL home's .claude
# raises, unless SKILL_ROUTER_CHECK_LIVE=1 (the explicit live-install tests).
# The real home is captured once here, before any test patches HOME. The hook
# only covers this process; subprocess tests must pass a temp HOME/env paths.
import sys  # noqa: E402

_REAL_CLAUDE = os.path.realpath(os.path.join(os.path.expanduser("~"), ".claude"))
_REAL_HOME_STR = os.path.dirname(_REAL_CLAUDE)

# Subprocess tests inherit os.environ, so point HOME at a fresh temp home for the whole
# session (the audit hook above only covers this process). Live mode keeps the real HOME.
# This runs before any test module imports a script that computes Path.home() at import.
if os.environ.get("SKILL_ROUTER_CHECK_LIVE") != "1":
    _TEST_HOME = Path(tempfile.mkdtemp(prefix="router-home-"))
    (_TEST_HOME / ".claude").mkdir()
    os.environ["HOME"] = str(_TEST_HOME)

LIVE_TRIPS: list[tuple[str, str]] = []


def _live_guard(event: str, args: tuple) -> None:
    if event not in ("open", "os.listdir", "os.scandir"):
        return
    if not args:
        return
    raw = args[0]
    if isinstance(raw, bytes):
        try:
            raw = os.fsdecode(raw)
        except Exception:
            return
    elif isinstance(raw, os.PathLike):
        raw = os.fspath(raw)
        if isinstance(raw, bytes):
            raw = os.fsdecode(raw)
    if not isinstance(raw, str) or not raw:
        return
    # Cheap pre-filter: a path can only reach <home>/.claude if it spells
    # ".claude" or is relative (it may resolve there via the cwd). Absolute
    # paths that reach it through a symlink with another spelling are not
    # caught; only the realpath of paths passing this filter is checked.
    if ".claude" not in raw and os.path.isabs(raw):
        return
    if os.environ.get("SKILL_ROUTER_CHECK_LIVE") == "1":
        return
    try:
        if not os.path.isabs(raw):
            raw = os.path.join(os.getcwd(), raw)
        real = os.path.realpath(raw)
    except (ValueError, OSError):
        return
    if sys.platform == "darwin":
        real, live = real.lower(), _REAL_CLAUDE.lower()   # default APFS is case-insensitive
    else:
        live = _REAL_CLAUDE
    if real == live or real.startswith(live + os.sep):
        rel = os.path.relpath(real, _REAL_HOME_STR)
        # Recorded so a test that swallows the PermissionError still fails.
        LIVE_TRIPS.append((os.environ.get("PYTEST_CURRENT_TEST", "<collection>"), rel))
        raise PermissionError(f"test touched live ~/.claude: {rel}")


sys.addaudithook(_live_guard)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _live_guard_tripwire():
    """Fail a test that touched the live install even if the code under test
    caught the PermissionError and carried on."""
    # Trips from import/collection/setUpClass land on the next test's teardown.
    yield
    new = LIVE_TRIPS[:]
    LIVE_TRIPS.clear()
    if new:
        who = sorted({f"{t} -> {rel}" for t, rel in new})
        pytest.fail("test touched live ~/.claude: " + "; ".join(who[:5]), pytrace=False)
