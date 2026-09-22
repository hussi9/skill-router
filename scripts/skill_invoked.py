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

Five jobs, in order, each independently fail-soft:

  1. Append to ~/.claude/skill_usage.log      (statusline + legacy reports)
  2. Append an `invoke` event with session_id + prompt_id to the router log
                                              (the learner's join key)
  3. Remove the skill from pending state      (satisfies the IRON RULE)
  4. Clear its strike and override tallies    (re-arm the skill)
  5. Emit the handover nudge, if history has one:
       "[skill-router] After <skill> you usually run <next> (72%, n=9)."
     Delivered as PostToolUse additionalContext — verified to reach the model
     on this Claude Code version by injecting a token and having the model
     quote it back. Advisory; nothing is enforced.

Always exits 0.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

HOME = Path.home()
USAGE_LOG = HOME / ".claude" / "skill_usage.log"
ROUTER_LOG = HOME / ".claude" / "skill_router_log.jsonl"
PENDING = HOME / ".claude" / "skill_router_pending.json"
LEARNED = HOME / ".claude" / "skill_router_learned.json"

# A nudge fires only for a habit this clear. Below it, silence — a suggestion
# that is right one time in three is noise the reader learns to skip.
NUDGE_MIN_P = 0.5
NUDGE_MIN_N = 3


def read_payload() -> dict:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def invoked_skill(payload: dict) -> str:
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return ""
    return str(tool_input.get("skill") or "").strip()


def log_invoke_event(skill: str, payload: dict) -> None:
    """The structured twin of the usage-log line, with the ids the learner joins on."""
    if os.environ.get("SKILL_ROUTER_NO_LEARN") == "1":
        return
    try:
        ROUTER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ROUTER_LOG.open("a") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "type": "invoke",
                "skill": skill,
                "session_id": payload.get("session_id"),
                "prompt_id": payload.get("prompt_id"),
                "agent_id": payload.get("agent_id"),
            }) + "\n")
    except OSError:
        pass


def handover_nudge(skill: str) -> str:
    """What you usually run next, from the learned overlay. Empty if no habit."""
    if not LEARNED.is_file():
        return ""
    try:
        data = json.loads(LEARNED.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return ""
    nexts = (data.get("handovers") or {}).get(skill) or []
    for h in nexts:
        if not isinstance(h, dict):
            continue
        if float(h.get("p", 0)) >= NUDGE_MIN_P and int(h.get("n", 0)) >= NUDGE_MIN_N:
            return (f"[skill-router] After {skill} you usually run {h['to']} "
                    f"({float(h['p']):.0%} of the time, n={h['n']}). "
                    f"Advisory — invoke it when this step is done if it still applies.")
    return ""


def record_loaded(skill: str, payload: dict) -> None:
    """Append to this session's loaded-skills file; router.py reads it so it
    never cards a skill whose body is already in context. Sub-agent loads do
    not count: their context is not the parent's."""
    sid = str(payload.get("session_id") or "").strip()
    if not sid or payload.get("agent_id"):
        return
    session_dir = Path(os.environ.get("SKILL_ROUTER_SESSION_DIR")
                       or Path.home() / ".claude" / "skill_router_session")
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", sid)[:80]
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
        with (session_dir / f"{safe}.loaded").open("a") as f:
            f.write(skill + "\n")
    except OSError:
        pass


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
    if os.environ.get("SKILL_ROUTER_OFF") == "1":   # a Kimi offload child, or the user
        return 0
    payload = read_payload()
    skill = invoked_skill(payload)
    if not skill:
        return 0
    log_usage(skill)
    log_invoke_event(skill, payload)
    record_loaded(skill, payload)
    satisfy_pending(skill)
    rearm(skill)
    # Sub-agents already got their brief at SubagentStart; a second nudge
    # inside them would compete with the parent's plan.
    if payload.get("agent_id"):
        return 0
    nudge = handover_nudge(skill)
    if nudge:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PostToolUse", "additionalContext": nudge}}))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[skill-router-warn] skill_invoked: {exc}", file=sys.stderr)
        sys.exit(0)
