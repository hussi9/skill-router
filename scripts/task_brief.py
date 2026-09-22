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

v4.2 — the model of the dispatch. This hook is the one place a hook can
*set* a model rather than suggest one: `updatedInput.model`. Jev judges the
sub-agent's own prompt (`jev_choose.tier_only`, one small cached call) and

    light    >= 0.8  → model: haiku     (grep, list, summarise, rename …)
    standard >= 0.8  → model: sonnet    (routine work against a clear spec)
    anything else    → untouched        (inherits the session model)

Never touches a call that names its own `model`, a call whose prompt says
[no-router], or when SKILL_ROUTER_SUBAGENT_MODEL=0. Each decision is logged
to skill_router_log.jsonl as `subagent-model` so the weekly analysis can
count what went where. The brief lines and the model are independent: a
session with no route still gets its dispatches tiered.

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


LOG = HOME / ".claude" / "skill_router_log.jsonl"
ESCAPES = ("[no-router]", "[skip-router]", "[router-off]")


def tiering_on() -> bool:
    """Real hook turns only (the installed command sets SKILL_ROUTER_HOOK_MODE=1);
    tests and manual probes stay offline unless SKILL_ROUTER_JEV=1 opts in."""
    if os.environ.get("SKILL_ROUTER_SUBAGENT_MODEL", "1") in ("0", "off", "false"):
        return False
    return (os.environ.get("SKILL_ROUTER_HOOK_MODE") == "1"
            or os.environ.get("SKILL_ROUTER_JEV") == "1")


def choose_model(prompt: str) -> tuple[str, dict]:
    """(model or "", detail). "" means leave the dispatch alone."""
    if not tiering_on() or any(m in prompt.lower() for m in ESCAPES):
        return "", {}
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import jev_choose  # type: ignore[import-not-found]
        pick = jev_choose.tier_only(prompt)
        model = jev_choose.model_for(pick)
    except Exception:                                   # noqa: BLE001 — never raise into a hook
        return "", {}
    if pick is None or model == "inherit":
        return "", {"work": getattr(pick, "name", None), "confidence": getattr(pick, "confidence", 0.0)}
    return model, {"work": pick.name, "confidence": round(pick.confidence, 2)}


def log_model(session_id: str, tool_input: dict, model: str, detail: dict) -> None:
    if os.environ.get("SKILL_ROUTER_NO_LEARN") == "1":
        return
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "type": "subagent-model",
                "session_id": session_id or None,
                "agent": tool_input.get("subagent_type") or None,
                "model": model or "inherit", "work": detail.get("work"),
                "confidence": detail.get("confidence"),
                "prompt_words": len(str(tool_input.get("prompt") or "").split()),
            }) + "\n")
    except OSError:
        pass


def main() -> int:
    if os.environ.get("SKILL_ROUTER_OFF") == "1":   # a Kimi offload child, or the user
        return 0
    data = read_payload()
    if data.get("agent_id"):
        return 0
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0
    prompt = tool_input.get("prompt")
    if not isinstance(prompt, str):
        return 0
    session_id = str(data.get("session_id") or "")
    updated = dict(tool_input)
    changed = False
    # 1. The model. Only when the caller did not pick one itself.
    if not tool_input.get("model"):
        model, detail = choose_model(prompt)
        if detail:
            log_model(session_id, tool_input, model, detail)
        if model:
            updated["model"] = model
            changed = True
    # 2. The brief. Skipped when the prompt already carries one.
    if MARK not in prompt:
        route = load_route(session_id)
        lines = brief_lines(route) if route else []
        if lines:
            updated["prompt"] = prompt.rstrip() + "\n\n" + "\n".join(lines) + "\n"
            changed = True
    if not changed:
        return 0
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
