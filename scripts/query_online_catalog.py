#!/usr/bin/env python3
"""
Query the online catalog for skill recommendations.

Usage:
  python3 query_online_catalog.py <keyword>

Example:
  python3 query_online_catalog.py stripe
  python3 query_online_catalog.py kubernetes
"""

import json
import sys
from pathlib import Path
from typing import List, Dict, Any


def load_catalog() -> Dict[str, Any]:
    """Load the online catalog."""
    catalog_path = Path.home() / ".claude" / "skill_router_online_catalog.json"
    
    if not catalog_path.exists():
        print(f"❌ Catalog not found at {catalog_path}", file=sys.stderr)
        print(f"   Run: python3 scripts/online_catalog_fetcher.py", file=sys.stderr)
        sys.exit(1)
    
    with open(catalog_path, "r") as f:
        return json.load(f)


def search_catalog(keyword: str, catalog: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Search catalog for matching items."""
    keyword_lower = keyword.lower()
    results: List[Dict[str, Any]] = []
    
    for source_name, items in catalog.get("catalogs", {}).items():
        for item in items:
            # Match on name, description, category, or tags
            name_match = keyword_lower in item.get("name", "").lower()
            desc_match = keyword_lower in item.get("description", "").lower()
            cat_match = keyword_lower in item.get("category", "").lower()
            tag_match = any(keyword_lower in str(tag).lower() for tag in item.get("tags", []))
            
            if name_match or desc_match or cat_match or tag_match:
                results.append({**item, "_score": sum([name_match * 3, desc_match, cat_match, tag_match])})
    
    # Sort by score and then by name
    results.sort(key=lambda x: (-x["_score"], x.get("name", "")))
    return results


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 query_online_catalog.py <keyword>")
        print("Example: python3 query_online_catalog.py stripe")
        sys.exit(1)
    
    keyword = sys.argv[1]
    catalog = load_catalog()
    
    results = search_catalog(keyword, catalog)
    
    if not results:
        print(f"❌ No matches for '{keyword}'")
        sys.exit(0)
    
    print(f"📚 Found {len(results)} skills matching '{keyword}':\n")
    
    # Group by source
    by_source: Dict[str, List[Dict[str, Any]]] = {}
    for item in results:
        source = item.get("source", "unknown")
        if source not in by_source:
            by_source[source] = []
        by_source[source].append(item)
    
    for source, items in sorted(by_source.items()):
        print(f"  {source}:")
        for item in items[:5]:  # Show top 5 per source
            print(f"    • {item.get('name', 'unknown')}")
            print(f"      {item.get('description', 'No description')[:80]}...")
        if len(items) > 5:
            print(f"    ... and {len(items) - 5} more")
        print()


if __name__ == "__main__":
    main()
