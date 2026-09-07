#!/usr/bin/env python3
"""
task_brief.py — carry the parent's route into every dispatched sub-agent.

Wired as a PreToolUse hook on `Task` / `Agent`. When the parent session has a
current route (written by router.py to ~/.claude/skill_router_session/), this
appends one short block to the sub-agent's prompt through `updatedInput`, so
the agent that actually edits files knows which skill the turn is running
under and which completion gates apply.

Why updatedInput and not SubagentStart alone: SubagentStart's context is
documented as informational and has been unreliable across Claude Code builds;
a prompt edit at dispatch is honoured by every build that has updatedInput,
and on one that lacks it the payload is simply ignored — nothing breaks.

Stands down when:
  - the call comes from inside a sub-agent (`agent_id` present),
  - no route exists for this session (or latest.json is older than 2 h),
  - the prompt already carries a [skill-router] block,
  - the tool input has no string `prompt`.

Always exits 0. Emits nothing when it has nothing to add.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import os

HOME = Path.home()
SESSION_DIR = Path(os.environ.get("SKILL_ROUTER_SESSION_DIR")
                   or HOME / ".claude" / "skill_router_session")
MAX_AGE_S = 2 * 3600
MARK = "[skill-router]"


def read_payload() -> dict:
    try:
        data = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def load_route(session_id: str) -> dict:
    session_dir = Path(os.environ.get("SKILL_ROUTER_SESSION_DIR") or SESSION_DIR)
    cands = []
    if session_id:
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", session_id)[:80]
        cands.append(session_dir / f"{safe}.json")
    cands.append(session_dir / "latest.json")
    for p in cands:
        if not p.is_file():
            continue
        try:
            if time.time() - p.stat().st_mtime > MAX_AGE_S:
                continue
            data = json.loads(p.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("primary"):
            return data
    return {}


def brief_lines(route: dict) -> list[str]:
    skills = [s for s in route.get("skills", []) if isinstance(s, str)]
    if not skills:
        return []
    lines = [f"{MARK} Parent route ({route.get('path', '?')}, {route.get('tier', 'soft')}): "
             f"load Skill(skill=\"{skills[0]}\") before editing"
             + (f"; also relevant: {', '.join(skills[1:3])}" if len(skills) > 1 else "") + "."]
    gates = [g for g in route.get("gates", []) if isinstance(g, str)][:3]
    if gates:
        lines.append(f"{MARK} Completion gates: " + " · ".join(gates))
    mem = [m for m in route.get("memory", []) if isinstance(m, str)][:2]
    if mem:
        lines.append(f"{MARK} Memory to read first: " + ", ".join(mem)
                     + " (in ~/.claude/projects/-Users-airbook/memory/)")
    return lines


def main() -> int:
    data = read_payload()
    if data.get("agent_id"):
        return 0
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str) or MARK in prompt:
        return 0
    route = load_route(str(data.get("session_id") or ""))
    if not route:
        return 0
    lines = brief_lines(route)
    if not lines:
        return 0
    updated = dict(tool_input)
    updated["prompt"] = prompt.rstrip() + "\n\n" + "\n".join(lines) + "\n"
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated,
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
