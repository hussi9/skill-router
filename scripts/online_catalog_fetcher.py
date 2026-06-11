#!/usr/bin/env python3
"""
Online Skill/Plugin Catalog Fetcher

Fetches publicly available skill and plugin catalogs from multiple online sources
and normalizes them into a unified format for the skill-router recommendation engine.

Sources:
1. Antigravity Awesome Skills (GitHub: sickn33/antigravity-awesome-skills)
2. Anthropic Official Plugins (claude-plugins-official marketplace)
3. Anthropic Code Plugins (claude-code plugins marketplace)
4. thedotmack Plugins (GitHub: thedotmack/claude-mem)

Output: ~/.claude/skill_router_online_catalog.json
Format: { "catalogs": { "<source>": [...], ... }, "metadata": {...} }
"""

import json
import urllib.request
import urllib.error
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Any, Optional


class CatalogFetcher:
    """Fetches and normalizes skill catalogs from multiple sources."""

    BASE_GITHUB = "https://raw.githubusercontent.com"
    OUTPUT_PATH = Path.home() / ".claude" / "skill_router_online_catalog.json"
    TIMEOUT = 10  # seconds

    def __init__(self) -> None:
        self.catalogs: Dict[str, List[Dict[str, Any]]] = {}
        self.metadata: Dict[str, Any] = {
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "sources": {},
        }

    def fetch_url(self, url: str) -> Optional[str]:
        """Fetch URL content with timeout and error handling."""
        try:
            with urllib.request.urlopen(url, timeout=self.TIMEOUT) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            print(f"  ⚠️  HTTP {e.code}: {url}", file=sys.stderr)
            return None
        except Exception as e:
            print(f"  ⚠️  Error fetching {url}: {e}", file=sys.stderr)
            return None

    def parse_json(self, content: str) -> Optional[Dict[str, Any]]:
        """Parse JSON with error handling."""
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            print(f"  ⚠️  JSON decode error: {e}", file=sys.stderr)
            return None

    def fetch_antigravity(self) -> List[Dict[str, Any]]:
        """
        Fetch from sickn33/antigravity-awesome-skills
        Status: ✅ Fetchable JSON (data/catalog.json, data/skills_index.json)
        """
        print("📡 Fetching: Antigravity Awesome Skills...")

        # Fetch main catalog
        catalog_url = f"{self.BASE_GITHUB}/sickn33/antigravity-awesome-skills/main/data/catalog.json"
        content = self.fetch_url(catalog_url)
        if not content:
            return []

        data = self.parse_json(content)
        if not data or "skills" not in data:
            print("  ⚠️  Invalid catalog structure", file=sys.stderr)
            return []

        skills = data.get("skills", [])
        if isinstance(skills, dict):
            skills = list(skills.values())

        # Normalize to standard format
        normalized: List[Dict[str, Any]] = []
        for skill in skills:
            if not isinstance(skill, dict):
                continue
            normalized.append({
                "name": skill.get("id", skill.get("name", "unknown")),
                "description": skill.get("description", ""),
                "category": skill.get("category", "general"),
                "source": "online:antigravity",
                "source_url": f"https://github.com/sickn33/antigravity-awesome-skills/blob/main/{skill.get('path', '')}",
                "installed": False,
                "install_command": f"npx antigravity-awesome-skills install {skill.get('id', '')}",
                "tags": skill.get("tags", []),
            })

        self.metadata["sources"]["antigravity"] = {
            "count": len(normalized),
            "url": catalog_url,
            "status": "success",
        }
        print(f"  ✅ Got {len(normalized)} skills")
        return normalized

    def fetch_anthropic_official(self) -> List[Dict[str, Any]]:
        """
        Fetch from Anthropic Official Plugins Marketplace
        Status: ✅ Fetchable JSON (local cache)
        Note: Official API protected by Cloudflare; using local marketplace mirror
        """
        print("📡 Fetching: Anthropic Official Plugins...")

        local_path = Path.home() / ".claude" / "plugins" / "marketplaces" / "claude-plugins-official" / ".claude-plugin" / "marketplace.json"

        if not local_path.exists():
            print(f"  ⚠️  Local marketplace not found at {local_path}", file=sys.stderr)
            return []

        try:
            with open(local_path, "r") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  ⚠️  Error reading local marketplace: {e}", file=sys.stderr)
            return []

        plugins = data.get("plugins", [])
        normalized: List[Dict[str, Any]] = []

        for plugin in plugins:
            if not isinstance(plugin, dict):
                continue
            normalized.append({
                "name": plugin.get("name", "unknown"),
                "description": plugin.get("description", ""),
                "category": plugin.get("category", "development"),
                "source": "online:anthropic-official",
                "source_url": plugin.get("homepage", ""),
                "installed": False,
                "install_command": f"# Install via Claude Code plugins marketplace: {plugin.get('name', '')}",
                "author": plugin.get("author", {}).get("name", "Anthropic"),
            })

        self.metadata["sources"]["anthropic-official"] = {
            "count": len(normalized),
            "source": "local_mirror",
            "status": "success",
        }
        print(f"  ✅ Got {len(normalized)} plugins")
        return normalized

    def fetch_anthropic_code_plugins(self) -> List[Dict[str, Any]]:
        """
        Fetch from Anthropic Code Plugins Marketplace
        Status: ✅ Fetchable JSON (local cache)
        """
        print("📡 Fetching: Anthropic Code Plugins...")

        local_path = Path.home() / ".claude" / "plugins" / "marketplaces" / "claude-code-plugins" / ".claude-plugin" / "marketplace.json"

        if not local_path.exists():
            print(f"  ⚠️  Local marketplace not found at {local_path}", file=sys.stderr)
            return []

        try:
            with open(local_path, "r") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  ⚠️  Error reading local marketplace: {e}", file=sys.stderr)
            return []

        plugins = data.get("plugins", [])
        normalized: List[Dict[str, Any]] = []

        for plugin in plugins:
            if not isinstance(plugin, dict):
                continue
            normalized.append({
                "name": plugin.get("name", "unknown"),
                "description": plugin.get("description", ""),
                "category": plugin.get("category", "development"),
                "version": plugin.get("version", "unknown"),
                "source": "online:anthropic-code",
                "source_url": plugin.get("homepage", ""),
                "installed": False,
                "install_command": f"# Included in Claude Code plugins: {plugin.get('name', '')}",
                "author": plugin.get("author", {}).get("name", "Anthropic"),
            })

        self.metadata["sources"]["anthropic-code"] = {
            "count": len(normalized),
            "source": "local_mirror",
            "status": "success",
        }
        print(f"  ✅ Got {len(normalized)} plugins")
        return normalized

    def fetch_thedotmack(self) -> List[Dict[str, Any]]:
        """
        Fetch from thedotmack/claude-mem marketplace
        Status: ✅ Fetchable JSON (local cache)
        """
        print("📡 Fetching: thedotmack Plugins...")

        local_path = Path.home() / ".claude" / "plugins" / "marketplaces" / "thedotmack" / ".claude-plugin" / "marketplace.json"

        if not local_path.exists():
            print(f"  ⚠️  Local marketplace not found at {local_path}", file=sys.stderr)
            return []

        try:
            with open(local_path, "r") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  ⚠️  Error reading local marketplace: {e}", file=sys.stderr)
            return []

        plugins = data.get("plugins", [])
        normalized: List[Dict[str, Any]] = []

        for plugin in plugins:
            if not isinstance(plugin, dict):
                continue
            normalized.append({
                "name": plugin.get("name", "unknown"),
                "description": plugin.get("description", ""),
                "version": plugin.get("version", "unknown"),
                "source": "online:thedotmack",
                "source_url": "https://github.com/thedotmack/claude-mem",
                "installed": False,
                "install_command": f"git clone https://github.com/thedotmack/claude-mem.git ~/.claude/plugins/thedotmack",
                "author": data.get("owner", {}).get("name", "Alex Newman"),
            })

        self.metadata["sources"]["thedotmack"] = {
            "count": len(normalized),
            "source": "local_mirror",
            "status": "success",
        }
        print(f"  ✅ Got {len(normalized)} plugins")
        return normalized

    def compute_stats(self) -> Dict[str, Any]:
        """Compute catalog statistics."""
        all_skills: List[Dict[str, Any]] = []
        for catalog in self.catalogs.values():
            all_skills.extend(catalog)

        unique_names = len(set(s["name"] for s in all_skills))

        return {
            "total_catalogs": len(self.catalogs),
            "total_items": len(all_skills),
            "unique_names": unique_names,
            "items_by_source": {
                source: len(items) for source, items in self.catalogs.items()
            },
        }

    def analyze_local_overlap(self) -> Dict[str, Any]:
        """
        Analyze overlap with local ~880 agent skills.
        Returns stats on how many online items are novel vs duplicates.
        """
        local_skills_path = Path.home() / ".agent" / "skills"
        local_names = set()

        if local_skills_path.exists():
            local_names = set(d.name for d in local_skills_path.iterdir() if d.is_dir())

        all_online = set()
        for catalog in self.catalogs.values():
            for item in catalog:
                all_online.add(item["name"])

        overlap = all_online & local_names
        novel = all_online - local_names

        return {
            "local_count": len(local_names),
            "online_unique_names": len(all_online),
            "overlap_count": len(overlap),
            "novel_count": len(novel),
            "overlap_percentage": round(100 * len(overlap) / len(all_online), 1) if all_online else 0,
            "novel_percentage": round(100 * len(novel) / len(all_online), 1) if all_online else 0,
            "recommendation": "HIGH uniqueness, worth shipping" if len(novel) / len(all_online) > 0.5 else "LOW uniqueness, mostly duplicates",
        }

    def fetch_all(self) -> bool:
        """Fetch from all available sources."""
        print("🔍 Starting online catalog fetch...\n")

        self.catalogs["antigravity"] = self.fetch_antigravity()
        self.catalogs["anthropic-official"] = self.fetch_anthropic_official()
        self.catalogs["anthropic-code"] = self.fetch_anthropic_code_plugins()
        self.catalogs["thedotmack"] = self.fetch_thedotmack()

        stats = self.compute_stats()
        self.metadata["stats"] = stats

        overlap = self.analyze_local_overlap()
        self.metadata["local_overlap"] = overlap

        print(f"\n📊 Summary:")
        print(f"  Total items: {stats['total_items']}")
        print(f"  Unique names: {stats['unique_names']}")
        print(f"  Sources: {', '.join(self.catalogs.keys())}")
        print(f"\n🔄 Local Overlap Analysis:")
        print(f"  Local agent skills: {overlap['local_count']}")
        print(f"  Online unique items: {overlap['online_unique_names']}")
        print(f"  Overlap: {overlap['overlap_count']} ({overlap['overlap_percentage']}%)")
        print(f"  Novel (new): {overlap['novel_count']} ({overlap['novel_percentage']}%)")
        print(f"  Recommendation: {overlap['recommendation']}\n")

        return True

    def save(self) -> bool:
        """Save catalog to output file."""
        output_data: Dict[str, Any] = {
            "catalogs": self.catalogs,
            "metadata": self.metadata,
        }

        try:
            self.OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(self.OUTPUT_PATH, "w") as f:
                json.dump(output_data, f, indent=2)
            print(f"✅ Saved to {self.OUTPUT_PATH}")
            return True
        except Exception as e:
            print(f"❌ Error saving output: {e}", file=sys.stderr)
            return False

    def run(self) -> bool:
        """Run the fetcher and save results."""
        success = self.fetch_all()
        if success:
            success = self.save()
        return success


def main() -> None:
    fetcher = CatalogFetcher()
    success = fetcher.run()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
