#!/usr/bin/env python3
"""
skill_invoked.py — PostToolUse bookkeeping for a Skill call.

One process replacing four shell hooks that each re-read stdin, shelled out to
jq, and raced on the same state files through mktemp+mv. Only the first of the
four ever actually saw stdin: a PostToolUse matcher runs its hooks with the
same payload, but each `cat` in the chain consumed it, so the strike- and
override-reset hooks were reading empty input and silently doing nothing. That
is why demotions never got cleared by a successful invoke, and why the
override tally could only ever grow.

Four jobs, in order, each independently fail-soft:

  1. Append to ~/.claude/skill_usage.log      (the learning loop's ground truth)
  2. Remove the skill from pending state      (satisfies the IRON RULE)
  3. Clear its strike tally                   (re-arm after silent misses)
  4. Clear its reasoned-override tally        (re-arm after corrections)

Always exits 0.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HOME = Path.home()
USAGE_LOG = HOME / ".claude" / "skill_usage.log"
PENDING = HOME / ".claude" / "skill_router_pending.json"


def invoked_skill() -> str:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError, OSError):
        return ""
    if not isinstance(payload, dict):
        return ""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    return str(tool_input.get("skill") or "").strip()


def log_usage(skill: str) -> None:
    try:
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with USAGE_LOG.open("a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}\t{skill}\n")
    except OSError:
        pass


def satisfy_pending(skill: str) -> None:
    if not PENDING.is_file():
        return
    try:
        data = json.loads(PENDING.read_text() or "{}")
    except (json.JSONDecodeError, OSError):
        return
    if not isinstance(data, dict):
        return
    remaining = data.get("remaining")
    if not isinstance(remaining, list) or skill not in remaining:
        return
    data["remaining"] = [s for s in remaining if s != skill]
    try:
        PENDING.write_text(json.dumps(data) + "\n")
    except OSError:
        pass


def rearm(skill: str) -> None:
    """Clear both demotion tallies — the skill just proved it was the right call."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import router  # type: ignore[import-not-found]
    except ImportError:
        return
    try:
        router.reset_strikes(skill)
        router.reset_override_count(skill)
    except Exception:
        pass


def main() -> int:
    skill = invoked_skill()
    if not skill:
        return 0
    log_usage(skill)
    satisfy_pending(skill)
    rearm(skill)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[skill-router-warn] skill_invoked: {exc}", file=sys.stderr)
        sys.exit(0)
