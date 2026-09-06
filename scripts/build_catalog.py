#!/usr/bin/env python3
"""
build_catalog.py — inventory every skill this machine can actually invoke.

The routing table in SKILL.md knows ~20 skills. This machine has ~420
invokable ones plus ~900 more available for install. The gap between those
two numbers is the entire "why doesn't Claude use my skills" problem, so the
catalog is the substrate the specialist matcher (catalog_match.py) ranks over.

Two populations, and the distinction matters:

  invokable=True   Claude Code exposes it to the `Skill` tool right now:
                     ~/.claude/skills/<name>/SKILL.md          -> "<name>"
                     ~/.claude/commands/<name>.md              -> "<name>"
                     <cwd>/.claude/skills/<name>/SKILL.md      -> "<name>"
                     plugins/cache/*/<plugin>/<ver>/skills/... -> "<plugin>:<skill>"
                     plugins/cache/*/<plugin>/<ver>/commands/. -> "<plugin>:<cmd>"

  invokable=False  Present on disk but NOT loadable by the Skill tool:
                     ~/.agent/skills/<name>/SKILL.md      (other runtimes)
                     ~/.composio-skills/<name>/SKILL.md
                   These are install candidates only. Routing to one produces
                   a ghost-skill deadlock, so the matcher must never announce
                   them as a step — it can only suggest installing them.

Sub-agents are deliberately NOT catalogued as skills. `Skill(skill="db-expert")`
fails: agents are dispatched through the Agent tool, not the Skill tool. They
get their own section (`agents`) so the router can pair a skill with the right
agent without ever announcing an agent as a skill. (The previous catalog
conflated the two, which is why the failing-test route announced the
uninvokable `test-runner`.)

Plugin versions: only the highest version directory per plugin is scanned, so
a stale 0.45.1 alongside 0.48.0 doesn't double-count.

Usage:
    python3 scripts/build_catalog.py              # write ~/.claude/skill_router_catalog.json
    python3 scripts/build_catalog.py --stdout     # print, don't write
    python3 scripts/build_catalog.py --quiet      # no summary on stderr
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

HOME = Path.home()
OUTPUT = HOME / ".claude" / "skill_router_catalog.json"

CLAUDE_SKILLS = HOME / ".claude" / "skills"
CLAUDE_COMMANDS = HOME / ".claude" / "commands"
CLAUDE_AGENTS = HOME / ".claude" / "agents"
PLUGINS_CACHE = HOME / ".claude" / "plugins" / "cache"
AGENT_SKILLS = HOME / ".agent" / "skills"
COMPOSIO_SKILLS = HOME / ".composio-skills"

# Frontmatter is YAML but we only need two scalar fields, and a real YAML
# parser is not guaranteed to be importable from a hook. Parse conservatively:
# a key at column 0 inside the leading --- block, value possibly quoted,
# possibly continued on following indented lines (folded/literal blocks).
_FM_DELIM = re.compile(r"^---\s*$")
_FM_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")


def parse_frontmatter(text: str) -> dict[str, str]:
    """Extract top-level scalar fields from a SKILL.md YAML frontmatter block.

    Returns {} when the file has no frontmatter. Multi-line values (`>`, `|`,
    or a bare wrapped string) are joined into one line — descriptions in the
    wild use all three styles and the matcher only needs the words.
    """
    lines = text.splitlines()
    if not lines or not _FM_DELIM.match(lines[0]):
        return {}
    fields: dict[str, list[str]] = {}
    key: Optional[str] = None
    for raw in lines[1:]:
        if _FM_DELIM.match(raw):
            break
        m = _FM_KEY.match(raw)
        if m:
            key = m.group(1)
            first = m.group(2).strip()
            # Block scalar indicators carry no content on the key line.
            fields[key] = [] if first in (">", "|", ">-", "|-", "") else [first]
        elif key and raw.strip():
            fields[key].append(raw.strip())
    out: dict[str, str] = {}
    for k, parts in fields.items():
        val = " ".join(p for p in parts if p).strip()
        # Strip one layer of matching quotes — many skills wrap descriptions.
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1].strip()
        out[k] = val
    return out


def synth_description(body: str) -> str:
    """Build a description for a file that has no frontmatter.

    Slash-commands (`~/.claude/commands/*.md`) and hand-written skills often
    open straight into `# Title` with no YAML block. Those are exactly the
    skills the user reaches for by name, so leaving them description-less
    would make them permanently unmatchable. Synthesize from the H1 plus the
    first real paragraph — the same two things a human skims to decide
    whether a skill is relevant.
    """
    title = ""
    paras: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            if not title:
                title = heading
            continue
        if line.startswith(("```", "|", ">", "<")):
            continue
        paras.append(line.lstrip("-* "))
        if sum(len(p) for p in paras) > 240:
            break
    parts = [p for p in (title, " ".join(paras)) if p]
    return " — ".join(parts)[:400]


def read_skill_md(path: Path) -> tuple[str, str]:
    """Return (description, body_excerpt) for a SKILL.md.

    Only the head of the file is read: descriptions live in frontmatter and
    the matcher's body signal comes from the overview, so slurping a 90KB
    skill would cost 200x the bytes for no extra ranking signal.
    """
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
    except OSError:
        return "", ""
    fm = parse_frontmatter(head)
    desc = fm.get("description", "")
    body = head.split("---", 2)[-1] if head.startswith("---") else head
    body = " ".join(body.split())[:600]
    if not desc:
        desc = synth_description(head)
    return desc, body


def _newest_version_dir(plugin_dir: Path) -> Optional[Path]:
    """Pick the highest version directory under a plugin, numerically when the
    names look like semver and lexically otherwise ('latest' sorts last)."""
    versions = [d for d in plugin_dir.iterdir() if d.is_dir()]
    if not versions:
        return None

    def key(d: Path):
        parts = d.name.split(".")
        if all(p.isdigit() for p in parts) and parts != [""]:
            return (1, [int(p) for p in parts])
        return (0, [], d.name)  # non-semver ('latest') ranks below numbers

    numeric = [v for v in versions if all(p.isdigit() for p in v.name.split("."))]
    if numeric:
        return max(numeric, key=lambda d: [int(p) for p in d.name.split(".")])
    return max(versions, key=lambda d: d.name)


# Skills the Claude Code binary ships with. They are fully invokable but exist
# nowhere on disk, so a filesystem scan cannot see them — which is why nothing
# ever routed to `dataviz` before writing a chart or `artifact-design` before
# publishing a page, despite both being available in every session. Listed here
# with hand-written trigger-shaped descriptions so the matcher can rank them
# alongside everything else.
#
# Maintenance: this is a snapshot of Claude Code 2.1.x. If a release adds or
# renames a built-in, add it here — `scripts/router.py --doctor` reports names
# the router references but cannot verify.
BUILTIN_SKILLS: dict[str, str] = {
    "code-review": "Review a diff, PR, branch, or path for correctness bugs and cleanup opportunities at a chosen effort level; can post inline PR comments or apply fixes.",
    "security-review": "Review code for security vulnerabilities — injection, authorization gaps, secret handling, unsafe deserialization — before merging or shipping.",
    "simplify": "Review changed code for reuse, simplification, and efficiency, then apply the cleanups. Quality only, not bug hunting.",
    "dataviz": "Use before writing any chart, graph, plot, bar chart, line chart, dashboard, or data visualization in any medium or library — chart type choice, categorical and sequential color palettes, axes, legends, tooltips, sparklines, heatmaps, stat tiles, KPI rows, accessibility in light and dark.",
    "design": "Create a multi-artboard visual design canvas — UI mockups, screen flows, landing pages, posters, one-pagers — published as an editable artifact.",
    "artifact-design": "Load before writing any artifact or published HTML page: design investment calibration, layout, typography, theming.",
    "artifact-diagramming": "Diagramming guidance for artifacts — when a diagram earns its place and how to draw one that stays legible in both themes.",
    "artifact-capabilities": "Runtime capabilities a published artifact can declare — persistence, shared state, viewer identity, file storage, asking Claude.",
    "run": "Launch and drive this project's app to confirm a change works in the real app rather than only in tests.",
    "init": "Bootstrap a CLAUDE.md for a repository by analyzing its structure and conventions.",
    "update-config": "Configure the Claude Code harness via settings.json — hooks, permissions, environment variables, automated behaviors triggered on tool use.",
    "keybindings-help": "Customize keyboard shortcuts and chord bindings in keybindings.json.",
    "fewer-permission-prompts": "Scan transcripts for common read-only tool calls and add a prioritized allowlist to project settings to reduce permission prompts.",
    "loop": "Run a prompt or slash command on a recurring interval, or let the model self-pace, for polling and repeated tasks.",
    "schedule": "Create, update, list, or run scheduled cloud agents on a cron schedule, including one-time future runs.",
    "claude-api": "Reference for the Claude API and Anthropic SDK — model ids, pricing, parameters, streaming, tool use, MCP, prompt caching, token counting.",
    "workflow-authoring": "Reference for writing a Workflow tool script — script API, resume, quality patterns, worked examples.",
}


def scan_builtins() -> Iterable[dict]:
    for name, desc in BUILTIN_SKILLS.items():
        yield {
            "name": name,
            "description": desc,
            "body": "",
            "type": "builtin",
            "invokable": True,
            "source": "builtin",
            "source_path": "(built into Claude Code)",
        }


def entry(name: str, desc: str, body: str, typ: str, path: Path,
          invokable: bool, source: str) -> dict:
    return {
        "name": name,
        "description": desc,
        "body": body,
        "type": typ,
        "invokable": invokable,
        "source": source,
        "source_path": str(path),
    }


def scan_dir_skills(root: Path, typ: str, invokable: bool, source: str,
                    prefix: str = "") -> Iterable[dict]:
    """Scan a <root>/<name>/SKILL.md layout."""
    if not root.is_dir():
        return
    try:
        children = sorted(root.iterdir())
    except OSError:
        return
    for d in children:
        if not d.is_dir() or d.name.startswith("."):
            continue
        md = d / "SKILL.md"
        if not md.is_file():
            continue
        desc, body = read_skill_md(md)
        yield entry(f"{prefix}{d.name}", desc, body, typ, md, invokable, source)


def scan_commands(root: Path, typ: str, invokable: bool, source: str,
                  prefix: str = "") -> Iterable[dict]:
    """Scan a <root>/<name>.md slash-command layout."""
    if not root.is_dir():
        return
    try:
        children = sorted(root.iterdir())
    except OSError:
        return
    for f in children:
        if not f.is_file() or f.suffix != ".md" or f.name.startswith("."):
            continue
        desc, body = read_skill_md(f)
        yield entry(f"{prefix}{f.stem}", desc, body, typ, f, invokable, source)


def scan_agents(root: Path, source: str, prefix: str = "") -> Iterable[dict]:
    """Scan sub-agent definitions. Agents are dispatch targets, never Skill targets."""
    if not root.is_dir():
        return
    try:
        children = sorted(root.iterdir())
    except OSError:
        return
    for f in children:
        if not f.is_file() or f.suffix != ".md" or f.name.startswith("_"):
            continue
        try:
            head = f.read_text(encoding="utf-8", errors="replace")[:4096]
        except OSError:
            continue
        fm = parse_frontmatter(head)
        name = fm.get("name") or f.stem
        yield {
            "name": f"{prefix}{name}",
            "description": fm.get("description", ""),
            "model": fm.get("model", "inherit") or "inherit",
            "tools": fm.get("tools", ""),
            "skills": fm.get("skills", ""),
            "source": source,
            "source_path": str(f),
        }


def scan_plugins() -> tuple[list[dict], list[dict]]:
    """Return (skill_entries, agent_entries) for the newest version of each plugin."""
    skills: list[dict] = []
    agents: list[dict] = []
    if not PLUGINS_CACHE.is_dir():
        return skills, agents
    try:
        owners = sorted(PLUGINS_CACHE.iterdir())
    except OSError:
        return skills, agents
    for owner in owners:
        if not owner.is_dir():
            continue
        for plugin in sorted(owner.iterdir()):
            if not plugin.is_dir():
                continue
            vdir = _newest_version_dir(plugin)
            if vdir is None:
                continue
            ns = plugin.name
            # A plugin may ship skills at <ver>/skills or <ver>/.claude/skills.
            for skills_root in (vdir / "skills", vdir / ".claude" / "skills"):
                skills.extend(scan_dir_skills(
                    skills_root, "plugin-skill", True, f"plugin:{ns}", prefix=f"{ns}:"))
            for cmd_root in (vdir / "commands", vdir / ".claude" / "commands"):
                skills.extend(scan_commands(
                    cmd_root, "plugin-command", True, f"plugin:{ns}", prefix=f"{ns}:"))
            for agent_root in (vdir / "agents", vdir / ".claude" / "agents"):
                agents.extend(scan_agents(agent_root, f"plugin:{ns}", prefix=f"{ns}:"))
    return skills, agents


def scan_project(cwd: Path) -> list[dict]:
    """Project-local skills under <cwd>/.claude/skills — invokable, highest priority."""
    out: list[dict] = []
    root = cwd / ".claude" / "skills"
    out.extend(scan_dir_skills(root, "project-skill", True, "project"))
    out.extend(scan_commands(cwd / ".claude" / "commands", "project-command", True, "project"))
    return out


def build(cwd: Optional[Path] = None) -> dict:
    skills: list[dict] = []
    # Project-local first, then user, then builtins, then plugins: dedupe below
    # keeps the first writer, so scan order is priority order.
    if cwd is not None:
        skills.extend(scan_project(cwd))
    skills.extend(scan_dir_skills(CLAUDE_SKILLS, "skill", True, "user"))
    skills.extend(scan_commands(CLAUDE_COMMANDS, "command", True, "user"))

    skills.extend(scan_builtins())

    plugin_skills, plugin_agents = scan_plugins()
    skills.extend(plugin_skills)

    # Not invokable by the Skill tool — install candidates only.
    skills.extend(scan_dir_skills(AGENT_SKILLS, "available", False, "agent-skills"))
    skills.extend(scan_dir_skills(COMPOSIO_SKILLS, "available", False, "composio"))

    agents = list(scan_agents(CLAUDE_AGENTS, "user")) + plugin_agents

    # Dedupe by name; first writer wins, and the scan order above is the
    # priority order (project > user > plugin > available).
    seen: set[str] = set()
    deduped: list[dict] = []
    for s in skills:
        if s["name"] in seen:
            continue
        seen.add(s["name"])
        deduped.append(s)

    by_type: dict[str, int] = {}
    for s in deduped:
        by_type[s["type"]] = by_type.get(s["type"], 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "version": 2,
        "cwd": str(cwd) if cwd else None,
        "total": len(deduped),
        "invokable": sum(1 for s in deduped if s["invokable"]),
        "by_type": by_type,
        "entries": deduped,
        "agents": agents,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the skill-router catalog.")
    ap.add_argument("--stdout", action="store_true", help="print JSON instead of writing")
    ap.add_argument("--quiet", action="store_true", help="suppress the stderr summary")
    ap.add_argument("--cwd", default=os.getcwd(), help="project root to scan for local skills")
    args = ap.parse_args()

    catalog = build(Path(args.cwd) if args.cwd else None)
    payload = json.dumps(catalog, indent=1)

    if args.stdout:
        print(payload)
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        tmp = OUTPUT.with_suffix(".json.tmp")
        tmp.write_text(payload)
        tmp.replace(OUTPUT)

    if not args.quiet:
        missing = sum(1 for s in catalog["entries"] if s["invokable"] and not s["description"])
        print(
            f"[build_catalog] {catalog['total']} entries "
            f"({catalog['invokable']} invokable, {len(catalog['agents'])} agents) "
            f"-> {OUTPUT if not args.stdout else 'stdout'}",
            file=sys.stderr,
        )
        print(f"[build_catalog] by type: {catalog['by_type']}", file=sys.stderr)
        if missing:
            print(f"[build_catalog] warning: {missing} invokable skills have no "
                  f"description — they can never be matched. Run --stdout | "
                  f"jq to find them.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
