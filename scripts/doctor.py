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
  6. agents follow you    a pinned sub-agent model downgrades every dispatch
  7. agents register      a file without frontmatter is invisible, not broken
  8. overlay fresh        learning that stopped is discovery that stopped
  9. it actually routes   end-to-end, through the real entry point

Exit 0 when everything passes, 1 when any check fails. Safe to run any time.

Usage:
    python3 scripts/doctor.py
    python3 scripts/doctor.py --quiet    # exit code only
"""
from __future__ import annotations

import argparse
import json
import os
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
# Wired by the installer but not required: an older Claude Code that does not
# know the event never fires it, and routing works without it.
OPTIONAL_EVENTS = ("PostToolUseFailure",)
CATALOG_STALE_DAYS = 7

# Prompts that must produce an announcement. If routing is dead, this is the
# check that says so in one line.
SMOKE_PROMPTS = (
    "TypeError: cannot read property map of undefined",
    "add a dark mode toggle to the settings page",
    "deploy to production",
)


class Report:
    def __init__(self, quiet: bool, gate: bool = False) -> None:
        self.quiet = quiet
        self.failed = 0
        self.gate = gate

    def check(self, name: str, ok: bool, detail: str = "", fix: str = "",
              machine: bool = False) -> bool:
        """`machine=True` marks a check of this machine's state, not the repo.

        In gate mode (`--gate` / SKILL_ROUTER_DOCTOR_GATE=1) such a check that
        fails prints `[warn]` and does not count: the gate tests the repo.
        """
        warn_only = (not ok) and machine and self.gate
        if not ok and not warn_only:
            self.failed += 1
        if not self.quiet:
            mark = "ok  " if ok else ("warn" if warn_only else "FAIL")
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
    """Are the router's hooks wired into settings.json?

    Detection is by the script each hook runs, not by our `_skill_router`
    marker key. A config normalizer on this machine strips unknown
    underscore-prefixed keys from settings.json — caught live, it removed the
    markers while leaving the hook commands working. Checking for the marker
    would have reported routing as dead when it was fine, which is the same
    class of false signal this whole tool exists to eliminate.
    """
    try:
        sys.path.insert(0, str(HERE))
        from install_hooks import _is_ours  # type: ignore[import-not-found]
    except ImportError:
        def _is_ours(hook: dict) -> bool:  # type: ignore[misc]
            return bool(hook.get("_skill_router"))

    hooks = load_settings().get("hooks", {})
    installed = {
        event for event in REQUIRED_EVENTS
        for group in hooks.get(event, []) if isinstance(group, dict)
        for hook in group.get("hooks", []) if isinstance(hook, dict) and _is_ours(hook)
    }
    missing = [e for e in REQUIRED_EVENTS if e not in installed]
    optional = [
        event for event in OPTIONAL_EVENTS
        if any(isinstance(g, dict) and any(isinstance(h, dict) and _is_ours(h)
                                           for h in g.get("hooks", []))
               for g in hooks.get(event, []) if isinstance(hooks.get(event), list))
    ]
    r.check(
        "hooks installed in settings.json",
        not missing,
        f"{len(installed)}/{len(REQUIRED_EVENTS)} events wired"
        + (f"; missing {', '.join(missing)}" if missing else "")
        + f" · optional {len(optional)}/{len(OPTIONAL_EVENTS)}",
        "python3 scripts/install_hooks.py",
    )


def _hook_is_ours(hook: dict) -> bool:
    try:
        sys.path.insert(0, str(HERE))
        from install_hooks import _is_ours  # type: ignore[import-not-found]
        return _is_ours(hook)
    except ImportError:
        return bool(hook.get("_skill_router"))


def check_skill_audit(r: Report) -> None:
    """Advisory only: one line of `skill doctor` numbers. Keyless, never a FAIL."""
    name, fix = "skill audit", "python3 scripts/cli.py audit"
    try:
        import skill_audit  # type: ignore[import-not-found]
        catalog, usage, days, outcomes, skills_sh = skill_audit.load_inputs()
        rep = skill_audit.audit(catalog, usage, days, outcomes, skills_sh, jev=None, budget_s=1.5,
                                settings=skill_audit.load_settings())
        top = (rep.top_actions[0] if rep.top_actions else "nothing to do")[:90]
        detail = (f"score {rep.score}/100 · ~{rep.listing_sent_tokens / 1000:.1f}k of "
                  f"~{rep.listing_tokens / 1000:.1f}k (uncapped) listing tokens fit; "
                  f"{rep.descriptions_dropped} descriptions dropped · top: {top}")
    except Exception:  # noqa: BLE001 — advice must never break the doctor
        detail = "unavailable"
    r.check(name, True, f"{detail} (run: {fix})")


def check_visibility(r: Report) -> None:
    """Advisory only. Reads the cache `visibility plan` writes; never scans transcripts (those take ~10 s)."""
    name, fix = "skill visibility", "python3 scripts/cli.py visibility plan"
    try:
        import visibility  # type: ignore[import-not-found]
        projects = (visibility.read_summary().get("projects") or {})
        if not projects:
            detail = "no plan measured yet"
        else:
            data = visibility.read_summary()
            counted = sum(1 for v in projects.values() if (v.get("names_count") or {}).get("worthwhile"))
            free = sum(1 for v in projects.values() if (v.get("names_free") or {}).get("worthwhile"))
            levers = []
            for label, k in (("names count", "names_count"), ("names free", "names_free")):
                ents = [v[k] for v in projects.values() if isinstance(v.get(k), dict)]
                reach = [(e["budget_fraction"], e.get("budget_extra_tokens", 0)) for e in ents
                         if e.get("budget_fraction")]
                if reach:
                    f, t = max(reach)
                    levers.append(f"{label}: skillListingBudgetFraction {f:.3f} ({t:+d} tokens/session), "
                                  f"reachable in {len(reach)} of {len(ents)}")
                else:
                    levers.append(f"{label}: not reachable (<= 0.10) in any of {len(ents)}")
            lever = "budget lever " + "; ".join(levers)
            age = max(0, int((time.time() - float(data.get("updated") or 0)) // 86400))
            fresh = f"measured {age} days ago" + (", stale: re-run plan" if age > 30 else "")
            detail = (f"archive the {data.get('unused_skills', 0)} unused skills (python3 scripts/cli.py audit); "
                      f"{lever}; plan worthwhile in {counted} (names count) / {free} (names free) of "
                      f"{len(projects)} projects; {fresh}")
    except Exception:  # noqa: BLE001 — advice must never break the doctor
        detail = "unavailable"
    r.check(name, True, f"{detail} (run: {fix})")


def check_listing(r: Report) -> None:
    """Advisory only. Reads the cache `listing measure` writes; the doctor never runs the probe."""
    fix = "python3 scripts/cli.py listing measure"
    try:
        import listing_probe  # type: ignore[import-not-found]
        import project_usage  # type: ignore[import-not-found]
        m = listing_probe.load(project_usage.project_key(Path.cwd()))
        detail = listing_probe.line(m) if m else f"not measured — {fix}"
    except Exception:  # noqa: BLE001 — advice must never break the doctor
        detail = "unavailable"
    r.check("skill listing", True, detail)


def check_hook_scripts(r: Report) -> None:
    hooks = load_settings().get("hooks", {})
    missing: list[str] = []
    for event, groups in hooks.items():
        for group in groups if isinstance(groups, list) else []:
            if not isinstance(group, dict):
                continue
            for hook in group.get("hooks", []):
                if not (isinstance(hook, dict) and _hook_is_ours(hook)):
                    continue
                for token in str(hook.get("command", "")).split():
                    if token.endswith((".py", ".sh")) and "/" in token:
                        if not Path(token).is_file():
                            missing.append(f"{event}:{token}")
    r.check("hook scripts exist on disk", not missing,
            "; ".join(missing) if missing else "all referenced files present",
            "reinstall or restore the missing script")


def _expand_home(token: str) -> str:
    home = str(HOME)
    for var in ("${HOME}", "$HOME", "~"):
        if token.startswith(var) and token[len(var):len(var) + 1] in ("", "/"):
            return home + token[len(var):]
    return token


def live_install_dir(settings: dict) -> "Path | None":
    """Skill-router root of the router.py the UserPromptSubmit hook runs, or None."""
    groups = (settings.get("hooks") or {}).get("UserPromptSubmit") or []
    for group in groups if isinstance(groups, list) else []:
        for hook in group.get("hooks", []) if isinstance(group, dict) else []:
            if not isinstance(hook, dict):
                continue
            for raw in str(hook.get("command", "")).split():
                token = _expand_home(raw.strip("\"'"))
                if token.endswith("scripts/router.py"):
                    return Path(token).parent.parent
    return None


def hook_modules(repo: Path) -> list[str]:
    """Filenames (scripts/<name>) the installed hooks execute, plus the sibling
    modules they import, found by following `import x` / `from x import` lines
    transitively. Starts from install_hooks.OWNED_SCRIPTS (the hook commands)."""
    import re
    from install_hooks import OWNED_SCRIPTS  # type: ignore[import-not-found]
    scripts = Path(repo) / "scripts"
    present = {p.stem for p in scripts.glob("*.py")}
    todo = [Path(s).stem for s in OWNED_SCRIPTS if s.endswith(".py")]
    seen: set[str] = set()
    pat = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)", re.M)
    while todo:
        name = todo.pop()
        if name in seen or name not in present:
            continue
        seen.add(name)
        try:
            todo.extend(pat.findall((scripts / f"{name}.py").read_text(encoding="utf-8")))
        except OSError:
            pass
    return sorted(f"{n}.py" for n in seen)


def drift(repo: Path, live: Path) -> list[str]:
    """Relative paths of hook-relevant scripts that differ from, or are missing in, the live copy.

    Only the scripts the hooks run and the modules they import are compared;
    tooling the hooks never load (benchmarks, this doctor) is not drift.
    """
    import hashlib
    try:
        if Path(live).resolve() == Path(repo).resolve():
            return []
    except OSError:
        pass

    def digest(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    out: list[str] = []
    for src in (Path(repo) / "scripts" / n for n in hook_modules(repo)):
        dst = Path(live) / "scripts" / src.name
        try:
            if not dst.is_file() or digest(src) != digest(dst):
                out.append(f"scripts/{src.name}")
        except OSError:
            out.append(f"scripts/{src.name}")
    return out


def _tilde(path: Path) -> str:
    s = str(path)
    return "~" + s[len(str(HOME)):] if s.startswith(str(HOME)) else s


def check_live_drift(r: Report) -> None:
    live = live_install_dir(load_settings())
    if live is None:
        return  # no hook to compare; check_hooks already reports that
    stale = drift(ROOT, live)
    # No --delete: the live dir is shared with other agents.
    fix = (f"preview: rsync -avn {_tilde(ROOT)}/scripts/ {_tilde(live)}/scripts/\n"
           f"         apply:   rsync -a {_tilde(ROOT)}/scripts/ {_tilde(live)}/scripts/ "
           f"&& python3 {_tilde(live)}/scripts/install_hooks.py")
    shown = ", ".join(stale[:5]) + (f" (+{len(stale) - 5} more)" if len(stale) > 5 else "")
    r.check("live install in sync", not stale,
            f"hooks run {_tilde(live)}" + (f"; differs: {shown}" if stale else ""), fix,
            machine=True)


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


INDEX = Path.home() / ".claude" / "skill_index.json"
INDEX_STALE_DAYS = 2


def check_index(r: Report) -> None:
    """v4: the enriched index is what the ranker reads. Missing → v3 table only."""
    if not INDEX.is_file():
        r.check("skill index present and fresh", False, "no index file — routing is table-only",
                "python3 scripts/build_index.py --enrich")
        return
    try:
        data = json.loads(INDEX.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        r.check("skill index present and fresh", False, f"unreadable: {exc}",
                "python3 scripts/build_index.py --enrich")
        return
    age_days = (time.time() - INDEX.stat().st_mtime) / 86400
    total = int(data.get("total") or 0)
    enriched = int(data.get("enriched") or 0)
    projects = len(data.get("projects") or {})
    ok = age_days <= INDEX_STALE_DAYS and total > 0
    detail = f"{total} skills, {enriched} enriched, {projects} projects, {age_days:.1f} days old"
    if total and enriched < total * 0.8:
        detail += " — enrichment incomplete"
    r.check("skill index present and fresh", ok, detail, "python3 scripts/build_index.py --enrich")


def check_llm_stage(r: Report) -> None:
    """Advisory: the small-model stage is optional; report which provider can answer."""
    try:
        import llm_classify  # type: ignore[import-not-found]
        st = llm_classify.status()
    except Exception as exc:
        r.check("small-model stage", True, f"module unavailable ({exc}); lexical only", "")
        return
    if not st.get("enabled"):
        r.check("small-model stage", True, "disabled by SKILL_ROUTER_LLM=0; lexical only", "")
        return
    have = [k for k in ("anthropic_key", "gemini_key") if st.get(k)]
    detail = (f"keys: {', '.join(have) or 'none'} · {st.get('cache_entries', 0)} cached answers"
              if have else "no model key cached — lexical only")
    r.check("small-model stage", True, detail,
            "python3 scripts/refresh_env.py --verbose (needs Doppler)")


def check_skills_sh(r: Report) -> None:
    """Advisory: the opt-in skills.sh suggestion cache."""
    import json as _json, os, time as _time
    f = Path(os.environ.get("SKILL_ROUTER_SKILLS_SH_FILE") or Path.home() / ".claude" / "skill_router_skills_sh.json")
    try:
        meta = _json.loads(f.read_text(encoding="utf-8"))["metadata"]
    except (OSError, ValueError, KeyError):
        r.check("skills.sh cache", True, "not built (optional) — suggestions use the local online catalog",
                "python3 scripts/skills_sh_catalog.py")
        return
    age = (_time.time() - meta.get("generated_at", 0)) / 86400
    r.check("skills.sh cache", age < 14, f"{meta.get('total', 0)} skills, {meta.get('audited', 0)} audited clean, {age:.1f} days old",
            "python3 scripts/skills_sh_catalog.py --force")


def check_jev_stage(r: Report) -> None:
    """Advisory: Jev is the first chooser; without its key the lexical + Gemini path runs."""
    try:
        import jev_choose  # type: ignore[import-not-found]
        st = jev_choose.status()
    except Exception as exc:
        r.check("jev chooser", True, f"module unavailable ({exc}); lexical + small model", "")
        return
    if not st.get("enabled"):
        r.check("jev chooser", True, "disabled by SKILL_ROUTER_JEV=0", "")
        return
    detail = (f"{st['model']} · {st['index_entries']} index entries in "
              f"{st['domain_questions']} domain question(s) · {st['cache_entries']} cached answers"
              if st.get("key") else "no TYPESAFE_API_KEY cached — lexical + small model only")
    r.check("jev chooser", True, detail, "python3 scripts/refresh_env.py --verbose (needs Doppler)")


def check_task_hook(r: Report) -> None:
    """v4: the Task/Agent PreToolUse hook is how the parent's route reaches sub-agents."""
    try:
        settings = json.loads(SETTINGS.read_text())
    except (OSError, json.JSONDecodeError):
        r.check("sub-agent hand-off hook wired", False, "settings.json unreadable",
                "python3 scripts/install_hooks.py")
        return
    groups = (settings.get("hooks") or {}).get("PreToolUse") or []
    wired = any("task_brief.py" in h.get("command", "")
                for g in groups if isinstance(g, dict) for h in g.get("hooks", []))
    r.check("sub-agent hand-off hook wired", wired,
            "PreToolUse Task|Agent → task_brief.py" if wired else "task_brief.py not in settings.json",
            "python3 scripts/install_hooks.py")


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


def check_agent_models(r: Report) -> None:
    """Sub-agents must run at the session's model, not one pinned in 2025.

    Same fault as the routing table's old model column, in a different file:
    `model: sonnet` in agent frontmatter wins over the session, so every
    dispatch on an Opus or Fable session pays sub-agent overhead for lower
    capability. Measured here on 2.1.263: a pinned dispatch reported both
    claude-opus-5 and claude-sonnet-5; an `inherit` dispatch reported only
    claude-opus-5.

    `haiku` is exempt — the deliberate downgrade for bulk read-only work.
    """
    agents_dir = HOME / ".claude" / "agents"
    if not agents_dir.is_dir():
        r.check("sub-agents follow the session model", True, "no agents directory")
        return
    try:
        sys.path.insert(0, str(HERE))
        import fix_agent_models  # type: ignore[import-not-found]
    except ImportError:
        r.check("sub-agents follow the session model", True,
                "checker unavailable — skipped")
        return

    pinned: list[str] = []
    for path in sorted(agents_dir.glob("*.md")):
        if path.name.startswith("_"):
            continue
        try:
            model = fix_agent_models.current_model(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if model and model not in fix_agent_models.KEEP and model != "inherit":
            pinned.append(f"{path.stem}={model}")

    r.check("sub-agents follow the session model", not pinned,
            f"{len(pinned)} pinned: {', '.join(pinned[:6])}"
            + ("…" if len(pinned) > 6 else "")
            if pinned else "no agent overrides the session model",
            "python3 scripts/fix_agent_models.py", machine=True)


def check_agents_register(r: Report) -> None:
    """An agent file with no frontmatter is not a broken agent — it is no agent.

    Claude Code needs `name` and `description` to register a definition, so a
    file that opens straight at a heading is skipped in silence: it never
    appears in the dispatchable list and every attempt to use it fails with
    "agent type not found". Five of this machine's agents sat that way,
    referenced by name in the /market audit skill and unreachable the whole
    time. Nothing anywhere reported it, which is exactly why it belongs here.
    """
    agents_dir = HOME / ".claude" / "agents"
    if not agents_dir.is_dir():
        r.check("agent files register as agents", True, "no agents directory")
        return
    try:
        sys.path.insert(0, str(HERE))
        from build_catalog import parse_frontmatter  # type: ignore[import-not-found]
    except ImportError:
        r.check("agent files register as agents", True, "checker unavailable")
        return

    unregistered: list[str] = []
    for path in sorted(agents_dir.glob("*.md")):
        if path.name.startswith("_"):
            continue  # shared context and scratch files, not definitions
        try:
            fm = parse_frontmatter(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        if not fm.get("name") or not fm.get("description"):
            unregistered.append(path.stem)

    r.check("agent files register as agents", not unregistered,
            f"{len(unregistered)} invisible: {', '.join(unregistered)}"
            if unregistered else "every agent file has name + description",
            "add YAML frontmatter with name and description, or prefix the "
            "filename with _ if it is not an agent", machine=True)


LEARNED = HOME / ".claude" / "skill_router_learned.json"
LEARNED_STALE_DAYS = 3


def check_overlay(r: Report) -> None:
    """Is the learned overlay being regenerated?

    The SessionStart hook relearns in the background on every startup. If the
    file is missing or days old, either sessions are not starting through the
    hook or the learner is crashing silently — and both discovery and the
    handover nudges have quietly stopped with it.
    """
    if not LEARNED.is_file():
        r.check("learned overlay fresh", False, "no overlay file yet",
                "python3 scripts/learn.py")
        return
    try:
        data = json.loads(LEARNED.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        r.check("learned overlay fresh", False, f"unreadable: {exc}",
                "python3 scripts/learn.py")
        return
    age = (time.time() - LEARNED.stat().st_mtime) / 86400
    c = data.get("counts", {})
    detail = (f"{age:.1f} days old · {c.get('invocations', 0)} invocations, "
              f"{c.get('prompts_with_tokens', 0)} prompts with keywords, "
              f"{sum(len(v) for v in data.get('handovers', {}).values())} handovers, "
              f"{len(data.get('online', []))} install candidates")
    r.check("learned overlay fresh", age <= LEARNED_STALE_DAYS, detail,
            "python3 scripts/learn.py — and check the SessionStart hook is firing")


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
    ap.add_argument("--gate", action="store_true",
                    help="repo gate: machine-state checks (live drift, pinned agents, "
                         "agent registration) warn instead of fail "
                         "(also SKILL_ROUTER_DOCTOR_GATE=1)")
    ap.add_argument("--reset-deferrals", action="store_true",
                    help="clear every strike and override tally")
    args = ap.parse_args()

    if args.reset_deferrals:
        return reset_deferrals()

    if not args.quiet:
        print("\nskill-router doctor\n")

    gate = args.gate or os.environ.get("SKILL_ROUTER_DOCTOR_GATE") == "1"
    r = Report(args.quiet, gate=gate)
    check_hooks(r)
    check_hook_scripts(r)
    check_live_drift(r)
    check_skill_audit(r)
    check_visibility(r)
    check_listing(r)
    check_no_ghosts(r)
    check_catalog(r)
    check_index(r)
    check_jev_stage(r)
    check_skills_sh(r)
    check_llm_stage(r)
    check_task_hook(r)
    check_deferrals(r)
    check_agent_models(r)
    check_agents_register(r)
    check_overlay(r)
    check_end_to_end(r)

    if not args.quiet:
        if r.failed:
            print(f"\n  {r.failed} check(s) failed — routing is degraded.\n")
        else:
            print("\n  All checks passed. Routing is live.\n")
    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(main())
