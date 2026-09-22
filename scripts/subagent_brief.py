#!/usr/bin/env python3
"""
subagent_brief.py — tell a sub-agent which skills it should be using.

Wired as a `SubagentStart` hook. When the parent dispatches `Agent(
subagent_type="seo-technical", ...)`, this fires inside the new sub-agent and
injects a short brief naming the skills that fit that agent's job.

Why it is needed: routing has always stopped at the session boundary. The
parent gets a `[skill-router]` announcement on every prompt; a sub-agent gets
nothing — it starts with its own system prompt and a task, no idea that
`seo-technical` or `scrollbook-deploy` exists, and no announcement telling it.
So the expensive half of the work, the half actually editing files, ran
skill-blind. This closes that.

Two sources of skills, in order:

  1. An explicit pairing in `agent_skills.json`, for agents whose right skill
     is not guessable from their description.
  2. Otherwise, rank the agent's own `description:` against the skill catalog
     (catalog_match). A newly written agent therefore gets sensible skills
     with no registration step, which is the only way this stays true as the
     agent roster changes.

Kept to a handful of lines on purpose. A sub-agent's context is the scarcest
in the system, and a brief long enough to crowd out the task defeats itself.

Contract:
  stdin  {"hook_event_name": "SubagentStart", "agent_type": "...", "agent_id": "..."}
  stdout {"hookSpecificOutput": {"hookEventName": "SubagentStart",
                                 "additionalContext": "..."}}
  Always exits 0. A hook that fails must not take the sub-agent down with it.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAIRINGS = HERE.parent / "agent_skills.json"
LOG = Path.home() / ".claude" / "skill_router_log.jsonl"

# Generic agents get no brief. They have no domain to match on, so anything
# derived would be noise, and they are the ones dispatched most often.
GENERIC_AGENTS = {
    "general-purpose", "Explore", "Plan", "claude",
    "researcher", "statusline-setup", "claude-code-guide",
}

MAX_SKILLS = 3
# Derived (not hand-paired) skills must clear a higher bar than the parent's
# advisory line: a sub-agent cannot ask a clarifying question, so a bad
# suggestion costs a whole dispatch rather than a glance.
MIN_DERIVED_SCORE = 4.0


def _load_pairings() -> dict:
    if not PAIRINGS.is_file():
        return {}
    try:
        data = json.loads(PAIRINGS.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _agent_description(agent_type: str) -> str:
    """Find an agent's description in the catalog, for derivation."""
    try:
        sys.path.insert(0, str(HERE))
        import catalog_match  # noqa: F401  (import proves the module loads)
    except ImportError:
        return ""
    catalog = Path.home() / ".claude" / "skill_router_catalog.json"
    if not catalog.is_file():
        return ""
    try:
        data = json.loads(catalog.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return ""
    bare = agent_type.split(":")[-1]
    for agent in data.get("agents", []):
        name = agent.get("name", "")
        if name == agent_type or name.split(":")[-1] == bare:
            return agent.get("description", "") or ""
    return ""


def _derive(agent_type: str) -> list[str]:
    """Rank the catalog against the agent's own description."""
    desc = _agent_description(agent_type)
    # An empty description is not a dead end. Agent frontmatter is
    # inconsistent — several agents here carry no `description:` at all — and
    # the agent's own name is frequently the sharpest term available
    # ('ads-meta' the agent and `ads-meta` the skill are the same subject).
    # Rank on whatever we have; the score gate still decides.
    try:
        sys.path.insert(0, str(HERE))
        import catalog_match
    except ImportError:
        return []
    try:
        # The agent's own name is part of the query: `seo-technical` the agent
        # and `seo-technical` the skill are the same subject matter, and the
        # name is often the sharpest term either one has.
        matches = catalog_match.rank(f"{agent_type.replace(':', ' ')} {desc}",
                                     limit=MAX_SKILLS + 2)
    except Exception:
        return []
    out: list[str] = []
    for m in matches:
        if not m.invokable or m.score < MIN_DERIVED_SCORE:
            continue
        out.append(m.name)
        if len(out) >= MAX_SKILLS:
            break
    return out


def _installed(skill: str) -> bool:
    """Can the Skill tool load this right now? Fails open if unknown."""
    try:
        sys.path.insert(0, str(HERE))
        import router  # type: ignore[import-not-found]
        return router.valid_skill(skill)
    except Exception:
        return True


def skills_for(agent_type: str) -> tuple[list[str], str]:
    """Return (skill_names, provenance) for an agent type.

    A hand-set pairing is filtered against what is installed *now*. Skills
    get archived, uninstalled and renamed; a brief that tells a sub-agent to
    invoke one that is gone sends it chasing a name that fails, on a task
    where it cannot ask for help. Anything left after filtering is used;
    if nothing is left, fall back to deriving.
    """
    if not agent_type or agent_type in GENERIC_AGENTS:
        return [], "generic"
    pairings = _load_pairings()
    explicit = pairings.get(agent_type) or pairings.get(agent_type.split(":")[-1])
    if isinstance(explicit, list) and explicit:
        live = [s for s in explicit if isinstance(s, str) and _installed(s)][:MAX_SKILLS]
        if live:
            return live, "paired"
    return _derive(agent_type), "derived"


def parent_route_lines(session_id: str, already: list[str]) -> list[str]:
    """The parent session's route card, from task_brief's session file.
    Skips skills the agent brief already names."""
    try:
        sys.path.insert(0, str(HERE))
        import task_brief  # type: ignore[import-not-found]
    except ImportError:
        return []
    try:
        route = task_brief.load_route(session_id)
    except Exception:
        return []
    if not route:
        return []
    route = dict(route)
    route["skills"] = [s for s in route.get("skills", []) if s not in already] or route.get("skills", [])
    try:
        return task_brief.brief_lines(route)
    except Exception:
        return []


def parent_skills(lines: list[str]) -> list[str]:
    import re
    out: list[str] = []
    for ln in lines:
        out += re.findall(r'Skill\(skill="([^"]+)"\)', ln)
    return out


def render_brief(agent_type: str, skills: list[str], provenance: str) -> str:
    if not skills:
        return ""
    listed = ", ".join(f'Skill(skill="{s}")' for s in skills)
    return (
        f"[skill-router] Skills for {agent_type}: {listed}\n"
        f"[skill-router] Invoke the one that fits before you start editing. "
        f"They carry this project's conventions; working without them is how a "
        f"sub-agent produces code the parent then has to redo. "
        f"Not a match for your task? Ignore this line and proceed."
    )


def log_event(agent_type: str, skills: list[str], provenance: str) -> None:
    if os.environ.get("SKILL_ROUTER_NO_LOG") == "1":
        return
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as f:
            f.write(json.dumps({
                "ts": __import__("time").strftime("%Y-%m-%dT%H:%M:%S"),
                "type": "subagent-brief",
                "agent": agent_type,
                "skills": skills,
                "via": provenance,
            }) + "\n")
    except OSError:
        pass


def main() -> int:
    if os.environ.get("SKILL_ROUTER_OFF") == "1":   # a Kimi offload child, or the user
        return 0
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0

    agent_type = str(payload.get("agent_type") or "").strip()
    if not agent_type:
        return 0

    skills, provenance = skills_for(agent_type)
    brief = render_brief(agent_type, skills, provenance)
    # The parent's current route reaches every agent, generic ones included:
    # the skill this turn runs under, its completion gates, the memory file.
    parent = parent_route_lines(str(payload.get("session_id") or ""), skills)
    if parent:
        brief = (brief + "\n" if brief else "") + "\n".join(parent)
    if not brief:
        return 0

    log_event(agent_type, skills + [f"parent:{s}" for s in parent_skills(parent)], provenance)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": brief,
        }
    }))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # a broken hook must never break the sub-agent
        print(f"[skill-router-warn] subagent_brief: {exc}", file=sys.stderr)
        sys.exit(0)
