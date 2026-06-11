#!/usr/bin/env python3
"""Scan a local Codex installation for skills, plugins, and MCP servers."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore[assignment]


@dataclass
class SkillInfo:
    name: str
    kind: str
    path: str


@dataclass
class PluginInfo:
    name: str
    enabled: bool


@dataclass
class McpServerInfo:
    name: str
    mode: str
    target: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan local Codex skills, plugins, and MCP servers.",
    )
    parser.add_argument(
        "--codex-home",
        default=os.path.expanduser("~/.codex"),
        help="Path to Codex home (default: ~/.codex)",
    )
    parser.add_argument(
        "--query",
        default="",
        help="Optional case-insensitive filter across names.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of a human-readable summary.",
    )
    return parser.parse_args()


def list_skills(skills_dir: Path) -> list[SkillInfo]:
    if not skills_dir.exists():
        return []

    skills: list[SkillInfo] = []
    for child in sorted(skills_dir.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir():
            continue
        kind = "system" if child.name.startswith(".") else "user"
        skills.append(SkillInfo(name=child.name, kind=kind, path=str(child)))
    return skills


def load_config(config_path: Path) -> dict[str, Any]:
    if tomllib is None or not config_path.exists():
        return {}
    with config_path.open("rb") as fh:
        return tomllib.load(fh)


def list_plugins(config: dict[str, Any]) -> list[PluginInfo]:
    plugins = config.get("plugins", {})
    results: list[PluginInfo] = []
    for name, payload in sorted(plugins.items(), key=lambda item: str(item[0]).lower()):
        enabled = bool(payload.get("enabled", False)) if isinstance(payload, dict) else False
        results.append(PluginInfo(name=str(name), enabled=enabled))
    return results


def list_mcp_servers(config: dict[str, Any]) -> list[McpServerInfo]:
    servers = config.get("mcp_servers", {})
    results: list[McpServerInfo] = []
    for name, payload in sorted(servers.items(), key=lambda item: str(item[0]).lower()):
        if not isinstance(payload, dict):
            results.append(McpServerInfo(name=str(name), mode="unknown", target=""))
            continue

        if "url" in payload:
            results.append(McpServerInfo(name=str(name), mode="remote", target=str(payload["url"])))
        elif "command" in payload:
            args = payload.get("args", [])
            tail = " ".join(str(part) for part in args)
            target = str(payload["command"]) + (f" {tail}" if tail else "")
            results.append(McpServerInfo(name=str(name), mode="command", target=target))
        else:
            results.append(McpServerInfo(name=str(name), mode="unknown", target=""))
    return results


def matches_query(name: str, query: str) -> bool:
    return not query or query.lower() in name.lower()


def build_inventory(codex_home: Path, query: str) -> dict[str, Any]:
    skills_dir = codex_home / "skills"
    config_path = codex_home / "config.toml"
    config = load_config(config_path)

    skills = [skill for skill in list_skills(skills_dir) if matches_query(skill.name, query)]
    plugins = [plugin for plugin in list_plugins(config) if matches_query(plugin.name, query)]
    mcp_servers = [server for server in list_mcp_servers(config) if matches_query(server.name, query)]

    user_skills = [skill for skill in skills if skill.kind == "user"]
    system_skills = [skill for skill in skills if skill.kind == "system"]
    enabled_plugins = [plugin for plugin in plugins if plugin.enabled]

    return {
        "codex_home": str(codex_home),
        "query": query,
        "summary": {
            "user_skills": len(user_skills),
            "system_skills": len(system_skills),
            "plugins_enabled": len(enabled_plugins),
            "plugins_total": len(plugins),
            "mcp_servers": len(mcp_servers),
        },
        "skills": [asdict(skill) for skill in skills],
        "plugins": [asdict(plugin) for plugin in plugins],
        "mcp_servers": [asdict(server) for server in mcp_servers],
    }


def render_text(inventory: dict[str, Any]) -> str:
    summary = inventory["summary"]
    lines = [
        "Codex Inventory",
        f"Home: {inventory['codex_home']}",
    ]
    if inventory["query"]:
        lines.append(f"Filter: {inventory['query']}")
    lines.extend(
        [
            "",
            f"User skills:      {summary['user_skills']}",
            f"System skills:    {summary['system_skills']}",
            f"Enabled plugins:  {summary['plugins_enabled']}/{summary['plugins_total']}",
            f"MCP servers:      {summary['mcp_servers']}",
            "",
        ]
    )

    skills = inventory["skills"]
    if skills:
        lines.append("Skills")
        for skill in skills:
            prefix = "system" if skill["kind"] == "system" else "user"
            lines.append(f"  - [{prefix}] {skill['name']}")
        lines.append("")

    plugins = inventory["plugins"]
    if plugins:
        lines.append("Plugins")
        for plugin in plugins:
            state = "enabled" if plugin["enabled"] else "disabled"
            lines.append(f"  - [{state}] {plugin['name']}")
        lines.append("")

    mcp_servers = inventory["mcp_servers"]
    if mcp_servers:
        lines.append("MCP Servers")
        for server in mcp_servers:
            target = f" -> {server['target']}" if server["target"] else ""
            lines.append(f"  - [{server['mode']}] {server['name']}{target}")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    codex_home = Path(args.codex_home).expanduser()
    inventory = build_inventory(codex_home, args.query)

    if args.json:
        print(json.dumps(inventory, indent=2))
    else:
        print(render_text(inventory), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
