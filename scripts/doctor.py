#!/usr/bin/env python3
"""
doctor.py — is the router actually running, and is it routing to real things?

Written after an outage that lasted months in plain sight. The engine was
healthy, the unit tests were green, the docs described a working system, and
routing did nothing at all — because the hooks were missing from settings.json
and, underneath that, five core skills had been permanently demoted by a
tally with no expiry. Every existing check looked at the parts. Nothing looked
at whether the parts were connected.

Six checks, ordered by how badly each one silences routing:

  1. hooks installed      nothing runs without these
  2. hook scripts present a hook pointing at a missing file fails on every turn
  3. no ghost targets     an announced skill that can't load deadlocks the rule
  4. catalog fresh        a stale catalog cannot route to newly installed skills
  5. deferrals sane       demotions must expire; permanent ones kill routing
  6. it actually routes   end-to-end, through the real entry point

Exit 0 when everything passes, 1 when any check fails. Safe to run any time.

Usage:
    python3 scripts/doctor.py
    python3 scripts/doctor.py --quiet    # exit code only
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

HOME = Path.home()
SETTINGS = HOME / ".claude" / "settings.json"
CATALOG = HOME / ".claude" / "skill_router_catalog.json"

REQUIRED_EVENTS = ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop",
                   "SubagentStart", "SessionStart")
CATALOG_STALE_DAYS = 7

# Prompts that must produce an announcement. If routing is dead, this is the
# check that says so in one line.
SMOKE_PROMPTS = (
    "TypeError: cannot read property map of undefined",
    "add a dark mode toggle to the settings page",
    "deploy to production",
)


class Report:
    def __init__(self, quiet: bool) -> None:
        self.quiet = quiet
        self.failed = 0

    def check(self, name: str, ok: bool, detail: str = "", fix: str = "") -> bool:
        if not ok:
            self.failed += 1
        if not self.quiet:
            mark = "ok  " if ok else "FAIL"
            print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
            if not ok and fix:
                print(f"         fix: {fix}")
        return ok


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def check_hooks(r: Report) -> None:
    hooks = load_settings().get("hooks", {})
    installed = {
        event for event in REQUIRED_EVENTS
        for group in hooks.get(event, [])
        if isinstance(group, dict) and group.get("_skill_router")
    }
    missing = [e for e in REQUIRED_EVENTS if e not in installed]
    r.check(
        "hooks installed in settings.json",
        not missing,
        f"{len(installed)}/{len(REQUIRED_EVENTS)} events wired"
        + (f"; missing {', '.join(missing)}" if missing else ""),
        "python3 scripts/install_hooks.py",
    )


def check_hook_scripts(r: Report) -> None:
    hooks = load_settings().get("hooks", {})
    missing: list[str] = []
    for event, groups in hooks.items():
        for group in groups if isinstance(groups, list) else []:
            if not (isinstance(group, dict) and group.get("_skill_router")):
                continue
            for hook in group.get("hooks", []):
                for token in str(hook.get("command", "")).split():
                    if token.endswith((".py", ".sh")) and "/" in token:
                        if not Path(token).is_file():
                            missing.append(f"{event}:{token}")
    r.check("hook scripts exist on disk", not missing,
            "; ".join(missing) if missing else "all referenced files present",
            "reinstall or restore the missing script")


def check_no_ghosts(r: Report) -> None:
    import router  # type: ignore[import-not-found]

    prompts = list(SMOKE_PROMPTS) + [
        "my tests are failing", "production is down", "refactor the auth module",
        "review my PR", "integrate Stripe payments", "ship this branch",
        "add tests for the payment service",
        "create a new database schema for notifications",
    ]
    ghost_skills: list[str] = []
    ghost_agents: list[str] = []
    for prompt in prompts:
        _, chain, _, _ = router.route(prompt)
        for step in chain:
            if not router.valid_skill(step.skill):
                ghost_skills.append(step.skill)
            if not router.valid_agent(step.agent):
                ghost_agents.append(step.agent)
    for route in router.load_personal_routes():
        if not router.valid_skill(route.skill):
            ghost_skills.append(f"{route.name}→{route.skill}")

    problems = sorted(set(ghost_skills)) + sorted(set(ghost_agents))
    r.check("every routable target is loadable", not problems,
            ", ".join(problems) if problems else
            "no announced skill or agent is missing",
            "install the skill, or point the route at one you have")


def check_catalog(r: Report) -> None:
    if not CATALOG.is_file():
        r.check("catalog present and fresh", False, "no catalog file",
                "python3 scripts/build_catalog.py")
        return
    try:
        data = json.loads(CATALOG.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        r.check("catalog present and fresh", False, f"unreadable: {exc}",
                "python3 scripts/build_catalog.py")
        return
    age_days = (time.time() - CATALOG.stat().st_mtime) / 86400
    invokable = data.get("invokable", 0)
    version = data.get("version", 1)
    ok = age_days <= CATALOG_STALE_DAYS and invokable > 0 and version >= 2
    detail = f"{invokable} invokable skills, {age_days:.1f} days old, v{version}"
    if version < 2:
        detail += " — pre-v2 catalogs conflate agents with skills"
    r.check("catalog present and fresh", ok, detail,
            "python3 scripts/build_catalog.py")


def check_deferrals(r: Report) -> None:
    import router  # type: ignore[import-not-found]

    stuck: list[str] = []
    for path in (router.STRIKES, router.OVERRIDES_COUNT):
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text() or "{}")
        except (json.JSONDecodeError, OSError):
            continue
        for skill, value in (raw or {}).items():
            if not isinstance(value, dict):
                stuck.append(f"{skill} (no expiry, in {path.name})")

    live = sorted({s for s in list(router._load_strikes()) +
                   list(router._load_overrides_count())
                   if router.is_deferred(s)})
    r.check("demotions expire", not stuck,
            "; ".join(stuck) if stuck else
            (f"currently deferred: {', '.join(live)}" if live else
             "nothing demoted"),
            "python3 scripts/doctor.py --reset-deferrals")


def check_end_to_end(r: Report) -> None:
    silent: list[str] = []
    for prompt in SMOKE_PROMPTS:
        proc = subprocess.run(
            [sys.executable, str(HERE / "router.py")],
            input=prompt, capture_output=True, text=True, timeout=30,
        )
        if "[skill-router]" not in proc.stdout:
            silent.append(prompt[:40])
    r.check("routing produces announcements", not silent,
            f"{len(SMOKE_PROMPTS) - len(silent)}/{len(SMOKE_PROMPTS)} smoke prompts routed"
            + (f"; silent on: {', '.join(silent)}" if silent else ""),
            "check the deferral and ghost-target checks above")


def reset_deferrals() -> int:
    import router  # type: ignore[import-not-found]
    for path in (router.STRIKES, router.OVERRIDES_COUNT):
        try:
            path.write_text("{}\n")
            print(f"cleared {path}")
        except OSError as exc:
            print(f"could not clear {path}: {exc}", file=sys.stderr)
            return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Check that skill routing is alive.")
    ap.add_argument("--quiet", action="store_true", help="exit code only")
    ap.add_argument("--reset-deferrals", action="store_true",
                    help="clear every strike and override tally")
    args = ap.parse_args()

    if args.reset_deferrals:
        return reset_deferrals()

    if not args.quiet:
        print("\nskill-router doctor\n")

    r = Report(args.quiet)
    check_hooks(r)
    check_hook_scripts(r)
    check_no_ghosts(r)
    check_catalog(r)
    check_deferrals(r)
    check_end_to_end(r)

    if not args.quiet:
        if r.failed:
            print(f"\n  {r.failed} check(s) failed — routing is degraded.\n")
        else:
            print("\n  All checks passed. Routing is live.\n")
    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(main())
