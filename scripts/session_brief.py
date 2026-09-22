#!/usr/bin/env python3
"""
session_brief.py — three lines at session start about what changed.

Discovery is worthless if it is only visible to someone who runs a report.
This is the SessionStart hook's foreground part: it reads the learned overlay
(no computation, no network, well under 100ms) and prints at most three
lines that Claude Code adds to the session context:

  [skill-router] New since last session: scrollbook-qa, seo-hreflang (+3)
  [skill-router] Fits your recent work, not installed: stripe-webhooks (antigravity) ← stripe, checkout
  [skill-router] You usually run superpowers:writing-plans → test-driven-development → verification (×7)

Silent when there is nothing new to say. A brief that repeats itself every
session is the fastest way to teach the reader to skip it, so:

  - a new skill is announced while its first_seen is within NEW_DAYS, then never
  - online suggestions are shown only while they are not installed
  - the chain line appears only once the chain has real support

Runs only on `startup` and `resume` (settings.json matcher), not on compact.
Exit code is always 0.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

LEARNED = Path.home() / ".claude" / "skill_router_learned.json"
NEW_DAYS = 3
MAX_NEW_NAMED = 4
MAX_ONLINE = 2


def _ts(value: str) -> float:
    try:
        return time.mktime(time.strptime((value or "")[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return 0.0


def load() -> dict:
    if not LEARNED.is_file():
        return {}
    try:
        data = json.loads(LEARNED.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def brief(overlay: dict) -> list[str]:
    lines: list[str] = []
    cutoff = time.time() - NEW_DAYS * 86400

    new = [d for d in overlay.get("discovered", {}).get("new", [])
           if isinstance(d, dict) and _ts(d.get("first_seen", "")) >= cutoff]
    if new:
        names = [d["name"] for d in new]
        shown = ", ".join(names[:MAX_NEW_NAMED])
        extra = f" (+{len(names) - MAX_NEW_NAMED})" if len(names) > MAX_NEW_NAMED else ""
        lines.append(f"[skill-router] New since last session: {shown}{extra} — "
                     f"the router can announce these now.")

    online = [s for s in overlay.get("online", []) if isinstance(s, dict)][:MAX_ONLINE]
    if online:
        parts = []
        for s in online:
            where = "already on disk" if s.get("on_disk") else s.get("source", "online")
            parts.append(f"{s['name']} ({where}) ← {', '.join(s.get('matched', [])[:3])}")
        lines.append("[skill-router] Fits your recent work, not installed: " + "; ".join(parts))

    chains = [c for c in overlay.get("chains", []) if isinstance(c, dict) and len(c.get("steps", [])) >= 3]
    if chains:
        top = chains[0]
        lines.append(f"[skill-router] Your usual flow: " + " → ".join(top["steps"])
                     + f" (×{top['n']})")
    return lines


def main() -> int:
    if os.environ.get("SKILL_ROUTER_OFF") == "1":   # a Kimi offload child, or the user
        return 0
    if os.environ.get("SKILL_ROUTER_NO_LEARN") == "1":
        return 0
    lines = brief(load())
    if lines:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[skill-router-warn] session_brief: {exc}", file=sys.stderr)
        sys.exit(0)
