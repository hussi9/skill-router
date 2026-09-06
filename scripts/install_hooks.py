#!/usr/bin/env python3
"""
install_hooks.py — merge skill-router's hooks into ~/.claude/settings.json.

This is the step that was missing. The router's engine, tests and docs were all
in place; the hooks that run it were not in settings.json at all — they had been
dropped from the live file at some point and only survived in a `.bak`. So the
UserPromptSubmit announcement never fired, the IRON RULE never enforced, and
routing had been silently inert for months while every other part of the system
looked healthy.

Merging, not overwriting, is the whole point: this machine's settings.json also
carries Sentigent hooks on UserPromptSubmit / PreToolUse / PostToolUse, a gstack
Stop hook, and a formatter. Claude Code runs every hook whose matcher fits, so
these coexist — but only if the installer adds entries beside them instead of
replacing the arrays.

Idempotent. Each managed hook carries a `_skill_router` marker; re-running
replaces marked entries and leaves everything else untouched.

Usage:
    python3 scripts/install_hooks.py            # install / update
    python3 scripts/install_hooks.py --dry-run  # show the diff, change nothing
    python3 scripts/install_hooks.py --remove   # take the router's hooks out
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

HOME = Path.home()
SETTINGS = HOME / ".claude" / "settings.json"
SKILL_DIR = HOME / ".claude" / "skills" / "skill-router"
SCRIPTS = SKILL_DIR / "scripts"

MARKER = "_skill_router"

# Identifying our hooks by the marker alone is not enough. Something on this
# machine normalizes settings.json and drops unknown underscore-prefixed keys:
# observed live, `_skill_router` and `_purpose` were stripped while the hook
# commands themselves survived and kept working. Ownership therefore also has
# to be recognisable from the command itself, or the next install would append
# a second copy of every hook beside the unmarked originals.
OWNED_SCRIPTS = (
    "skill-router/scripts/router.py",
    "skill-router/scripts/iron_rule_hook.py",
    "skill-router/scripts/skill_invoked.py",
    "skill-router/scripts/subagent_brief.py",
    "skill-router/scripts/build_catalog.py",
)


def _is_ours(hook_entry: dict) -> bool:
    if hook_entry.get(MARKER):
        return True
    command = hook_entry.get("command")
    return isinstance(command, str) and any(s in command for s in OWNED_SCRIPTS)


def hook(command: str, timeout: int, purpose: str) -> dict:
    return {
        MARKER: True,
        "_purpose": purpose,
        "type": "command",
        "command": command,
        "timeout": timeout,
    }


def managed_hooks() -> dict[str, list[dict]]:
    """The hook entries this installer owns, keyed by event.

    Each value is a list of *matcher groups*, in settings.json shape.
    """
    router = f"python3 {SCRIPTS}/router.py"
    iron = f"python3 {SCRIPTS}/iron_rule_hook.py"
    brief = f"python3 {SCRIPTS}/subagent_brief.py"
    catalog = f"python3 {SCRIPTS}/build_catalog.py --quiet"

    return {
        "UserPromptSubmit": [{
            MARKER: True,
            "hooks": [hook(
                # jq extracts the prompt; an empty prompt exits before python
                # starts, which keeps the no-op path off the critical path of
                # every keystroke-driven turn.
                'prompt=$(jq -r \'.prompt // empty\'); [ -z "$prompt" ] && exit 0; '
                f'out=$(SKILL_ROUTER_HOOK_MODE=1 {router} <<< "$prompt" 2>/dev/null); '
                '[ -z "$out" ] && exit 0; '
                'jq -n --arg msg "$out" \'{systemMessage: $msg, hookSpecificOutput: '
                '{hookEventName: "UserPromptSubmit", additionalContext: $msg}}\'',
                5,
                "Run the deterministic router and inject the [skill-router] "
                "announcement as context before the model's first action.",
            )],
        }],
        "PreToolUse": [{
            MARKER: True,
            "matcher": "Edit|Write|Task|NotebookEdit|MultiEdit",
            "hooks": [hook(
                f"{iron} pre", 5,
                "IRON RULE: deny source-mutating tools until the announced "
                "skill has been invoked. Stands down inside sub-agents.",
            )],
        }],
        "PostToolUse": [{
            MARKER: True,
            "matcher": "Skill",
            "hooks": [hook(
                # One python process does all four bookkeeping jobs that used
                # to be four separate jq-and-mktemp shell hooks.
                f"python3 {SCRIPTS}/skill_invoked.py", 5,
                "Record the invocation, drop it from pending state, and re-arm "
                "the skill by clearing its strike and override tallies.",
            )],
        }],
        "Stop": [{
            MARKER: True,
            "hooks": [hook(
                f"{iron} stop", 5,
                "IRON RULE final gate: block turn end if the announced skill "
                "was never invoked.",
            )],
        }],
        "SubagentStart": [{
            MARKER: True,
            "hooks": [hook(
                brief, 8,
                "Tell each dispatched sub-agent which skills fit its job — the "
                "half of the work that previously ran skill-blind.",
            )],
        }],
        "SessionStart": [{
            MARKER: True,
            "hooks": [hook(
                # Backgrounded: a session must never wait on a catalog rebuild.
                f"({catalog} >/dev/null 2>&1; bash {SCRIPTS}/ensure-plugin-deps.sh "
                ">/dev/null 2>&1) &",
                3,
                "Refresh the skill catalog so newly installed skills are "
                "routable this session, and backfill plugin node_modules.",
            )],
        }],
    }


# Shell hooks from earlier hand-installed versions of skill-router. They are
# superseded by the managed entries above and, left in place, would double-log
# every Skill call into skill_usage.log — which silently doubles every follow
# rate the learning loop computes. Matched by a fragment of their command.
LEGACY_FRAGMENTS = (
    "skill_usage.log",
    "skill_router_pending.json",
    "skill_router_strikes.json",
    "skill_router_overrides_count.json",
    "skills/skill-router/scripts/router.py",
    "ensure-plugin-deps.sh",
)


def _is_legacy(hook_entry: dict) -> bool:
    if hook_entry.get(MARKER):
        return False  # ours, handled separately
    command = hook_entry.get("command")
    if not isinstance(command, str):
        return False
    if any(s in command for s in OWNED_SCRIPTS):
        return False  # ours, even without the marker
    return any(frag in command for frag in LEGACY_FRAGMENTS)


def strip_managed(groups: list) -> list:
    """Drop entries this installer owns, plus superseded hand-installed ones."""
    out = []
    for group in groups:
        if not isinstance(group, dict):
            out.append(group)
            continue
        if group.get(MARKER) or any(
                _is_ours(h) for h in group.get("hooks", []) if isinstance(h, dict)):
            continue
        hooks = group.get("hooks")
        if isinstance(hooks, list):
            kept = [h for h in hooks
                    if not (isinstance(h, dict) and (_is_ours(h) or _is_legacy(h)))]
            if not kept:
                continue
            group = {**group, "hooks": kept}
        out.append(group)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--remove", action="store_true")
    args = ap.parse_args()

    if not SETTINGS.is_file():
        print(f"no settings file at {SETTINGS}", file=sys.stderr)
        return 1
    try:
        settings = json.loads(SETTINGS.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        print(f"cannot parse {SETTINGS}: {exc}", file=sys.stderr)
        return 1

    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        print("settings.hooks is not an object; refusing to touch it", file=sys.stderr)
        return 1

    added = removed = 0
    for event, groups in managed_hooks().items():
        existing = hooks.get(event, [])
        if not isinstance(existing, list):
            existing = []
        before = len(existing)
        cleaned = strip_managed(existing)
        removed += before - len(cleaned)
        if args.remove:
            if cleaned:
                hooks[event] = cleaned
            else:
                hooks.pop(event, None)
            continue
        hooks[event] = cleaned + groups
        added += len(groups)

    verb = "remove" if args.remove else "install"
    if args.dry_run:
        print(json.dumps(hooks, indent=1))
        print(f"\n[dry-run] would {verb}: +{added} managed / -{removed} stale",
              file=sys.stderr)
        return 0

    backup = SETTINGS.with_name(
        f"settings.json.bak.router-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(SETTINGS, backup)
    tmp = SETTINGS.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n")
    tmp.replace(SETTINGS)

    print(f"{verb}ed skill-router hooks: +{added} / -{removed} stale")
    print(f"events: {', '.join(sorted(managed_hooks()))}")
    print(f"backup: {backup}")
    print("Restart Claude Code (or start a new session) for hooks to load.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
