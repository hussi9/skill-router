#!/usr/bin/env python3
"""
iron_rule_hook.py — enforce the announced route. PreToolUse and Stop.

Replaces the two 900-character shell one-liners that used to live inline in
settings.json. Those were unreviewable, untestable, and quoting-fragile, and
they could not read stdin — which is how the subagent deadlock below went
unnoticed.

Modes:

  pre    PreToolUse on Edit/Write/Task/NotebookEdit/MultiEdit. Denies the call
         while an announced skill has not been invoked. Read/Glob/Grep/Bash/
         TodoWrite/Skill are never matched, so context-gathering, shell work,
         and the override path all keep working.

  stop   Stop. Blocks turn end when the announced skill was never invoked —
         the case where the model reasoned to a conclusion using only allowed
         tools and tried to finish without ever loading the skill.

Three guards keep enforcement from turning into a trap:

  sub-agents      A sub-agent inherits settings.json hooks, so the parent's
                  pending state was blocking edits inside every dispatched
                  agent — and the sub-agent has no way to satisfy or clear it,
                  because the pending skill belongs to the parent's turn. When
                  `agent_id` is present, this hook stands down entirely.

  ghost skills    If the pending skill is not installed, the state is cleared
                  and the call allowed. Enforcing a name Claude cannot invoke
                  is an unbreakable deadlock.

  stop recursion  `stop_hook_active` means we already blocked once this turn;
                  blocking again would loop forever.

Exit code is always 0. A decision is expressed in the JSON payload, never by
crashing: an exception here would surface as a hook error on every single edit.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HOME = Path.home()
PENDING = HOME / ".claude" / "skill_router_pending.json"
SKILLS_DIR = HOME / ".claude" / "skills"
COMMANDS_DIR = HOME / ".claude" / "commands"
PLUGINS_DIR = HOME / ".claude" / "plugins" / "cache"

OVERRIDE_HINT = (
    'Wrong call? Run: python3 ~/.claude/skills/skill-router/scripts/'
    'router_override.py "<reason>" — that clears the rule and teaches the '
    "router. Or the user can type [no-router]."
)


def read_payload() -> dict:
    try:
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_pending_state() -> dict:
    if not PENDING.is_file():
        return {}
    try:
        data = json.loads(PENDING.read_text() or "{}")
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def read_pending() -> list[str]:
    remaining = read_pending_state().get("remaining")
    return [s for s in remaining if isinstance(s, str) and s] if isinstance(remaining, list) else []


def pending_tier() -> str:
    """hard | soft. Missing → hard, so an old-format state file keeps the
    behaviour every existing test asserts."""
    tier = str(read_pending_state().get("tier") or "hard").lower()
    return "soft" if tier == "soft" else "hard"


def record_soft_skip(skills: list[str]) -> None:
    """A soft route reached turn end without its skill: log it so the learner
    can demote a skill the model keeps declining, and mark the state so the
    second Stop is not blocked again."""
    try:
        # Next to the pending file, so a test that redirects PENDING never
        # writes fake skips into the live log the learner reads.
        LOG = PENDING.parent / "skill_router_log.jsonl"
        state = read_pending_state()
        with LOG.open("a") as f:
            import time
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "type": "soft-skip",
                "skills": skills, "session_id": state.get("session_id"),
                "prompt_id": state.get("prompt_id"),
            }) + "\n")
        state["soft_asked"] = True
        PENDING.write_text(json.dumps(state) + "\n")
    except OSError:
        pass


def clear_pending() -> None:
    try:
        PENDING.write_text("{}\n")
    except OSError:
        pass


def skill_installed(name: str) -> bool:
    """Is `name` something the Skill tool can actually load?

    Deliberately permissive: this decides whether to *enforce*, and a false
    negative merely skips enforcement for one turn while a false positive
    deadlocks the session. Built-in skills (code-review, dataviz, ...) live
    nowhere on disk, so an unresolvable name that contains no path-ish
    characters is given the benefit of the doubt.
    """
    if not name:
        return False
    if (SKILLS_DIR / name).is_dir():
        return True
    if (COMMANDS_DIR / f"{name}.md").is_file():
        return True
    if ":" in name:
        plugin, _, skill = name.partition(":")
        try:
            for match in PLUGINS_DIR.glob(f"*/{plugin}/*/skills/{skill}/SKILL.md"):
                return True
            for match in PLUGINS_DIR.glob(f"*/{plugin}/*/.claude/skills/{skill}/SKILL.md"):
                return True
            for match in PLUGINS_DIR.glob(f"*/{plugin}/*/commands/{skill}.md"):
                return True
        except OSError:
            return True
        return False
    try:
        from build_catalog import BUILTIN_SKILLS  # type: ignore[import-not-found]
        return name in BUILTIN_SKILLS
    except ImportError:
        return True


def emit(payload: dict) -> None:
    print(json.dumps(payload))


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "pre"
    data = read_payload()

    # Guard 1 — never enforce inside a sub-agent.
    if data.get("agent_id"):
        return 0

    # Guard 3 — do not re-block a turn we already blocked.
    if mode == "stop" and data.get("stop_hook_active"):
        return 0

    remaining = read_pending()
    if not remaining:
        return 0

    expected = remaining[0]

    # Guard 2 — an uninstallable skill cannot be satisfied, so stop asking.
    if not skill_installed(expected):
        clear_pending()
        return 0

    # Tier — soft routes never block edits. At turn end they ask once: load
    # the skill, or say in one line why it was the wrong call. Either way
    # the learner hears it, and the second Stop is never blocked.
    if pending_tier() == "soft":
        if mode != "stop":
            return 0
        if read_pending_state().get("soft_asked"):
            return 0
        record_soft_skip(remaining)
        emit({
            "decision": "block",
            "reason": (
                f"[skill-router] Soft route not used: {', '.join(remaining)}. "
                f"Either call Skill(skill=\"{expected}\") now and finish, or finish with one "
                f"line starting '[skill-router] skipped {expected}:' and the reason. "
                f"Not asked again this turn."
            ),
        })
        return 0

    if mode == "stop":
        emit({
            "decision": "block",
            "reason": (
                f"[skill-router] You announced a route but never invoked: "
                f"{', '.join(remaining)}. Invoke it now. {OVERRIDE_HINT}"
            ),
        })
        return 0

    emit({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f'[skill-router] IRON RULE: your next tool call must be '
                f'Skill(skill="{expected}"). Edit/Write/Task are blocked until it '
                f"runs; Read/Glob/Grep/Bash/TodoWrite/Skill stay allowed. "
                f"{OVERRIDE_HINT}"
            ),
        }
    })
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        sys.exit(main())
    except Exception as exc:  # enforcement must never break the session
        print(f"[skill-router-warn] iron_rule_hook: {exc}", file=sys.stderr)
        sys.exit(0)
