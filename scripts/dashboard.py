#!/usr/bin/env python3
"""
dashboard.py — combined-status dashboard for the skill-router.

One screen, plain ASCII. Tells an operator everything that matters:
  • Current pending state (anyone waiting?)
  • Activity in window (announcements + invocations + follow rate)
  • Top routes (most-announced primary skills)
  • Top deviations (announced but never invoked → tighten patterns)
  • Top surprises (invoked without announcement → broaden patterns)

Reads (read-only):
  ~/.claude/skill_router_log.jsonl   chain-start events from router.py
  ~/.claude/skill_usage.log          Skill-tool fires (TAB or SPACE separated)
  ~/.claude/skill_router_pending.json current pending-state JSON

Run:
    python3 scripts/dashboard.py             # last 7 days
    python3 scripts/dashboard.py --days 30
    python3 scripts/dashboard.py --json

Self-contained: no imports from learn-from-history.py or router.py — keeps
the dashboard safe to run without touching pending state or settings.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROUTER_LOG = Path.home() / ".claude" / "skill_router_log.jsonl"
USAGE_LOG = Path.home() / ".claude" / "skill_usage.log"
PENDING = Path.home() / ".claude" / "skill_router_pending.json"

# Same window the learner uses — keeps follow-rate numbers comparable.
FOLLOW_WINDOW_SEC = 120

BAR = "─" * 72


def _parse_iso(ts: str) -> float | None:
    try:
        return time.mktime(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, TypeError):
        return None


def _parse_log_ts(ts: str) -> float | None:
    try:
        return time.mktime(time.strptime(ts.strip(), "%Y-%m-%d %H:%M:%S"))
    except (ValueError, TypeError):
        return None


def parse_router_log(path: Path, since: float) -> list[dict]:
    """Return chain-start events with epoch ts + primary skill."""
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") != "chain-start":
            continue
        t = _parse_iso(e.get("ts", ""))
        if t is None or t < since:
            continue
        steps = list(e.get("steps", []))
        out.append({
            "ts": t,
            "name": e.get("name", "?"),
            "skills": steps,
            "primary": steps[0] if steps else "",
        })
    out.sort(key=lambda x: x["ts"])
    return out


def parse_usage_log(path: Path, since: float) -> list[dict]:
    """Return Skill tool fires; tolerant of TAB or SPACE separation."""
    if not path.is_file():
        return []
    out: list[dict] = []
    for raw in path.read_text(errors="ignore").splitlines():
        line = raw.strip()
        if not line:
            continue
        if "\t" in line:
            ts_str, _, skill = line.partition("\t")
        else:
            # Old format: 'YYYY-MM-DD HH:MM:SS skill-name' — last whitespace splits.
            parts = line.rsplit(" ", 1)
            if len(parts) != 2:
                continue
            ts_str, skill = parts
        skill = skill.strip()
        t = _parse_log_ts(ts_str)
        if t is None or t < since or not skill:
            continue
        out.append({"ts": t, "skill": skill})
    out.sort(key=lambda x: x["ts"])
    return out


def correlate(announcements: list[dict], invocations: list[dict]) -> dict:
    """Match each announcement to the soonest matching invocation within the
    follow window. Each invocation can satisfy at most one announcement."""
    consumed = [False] * len(invocations)
    followed: list[dict] = []
    ignored: list[dict] = []

    for ann in announcements:
        primary = ann["primary"]
        match_idx: int | None = None
        for i, inv in enumerate(invocations):
            if consumed[i]:
                continue
            if inv["ts"] < ann["ts"]:
                continue
            if inv["ts"] - ann["ts"] > FOLLOW_WINDOW_SEC:
                break  # invocations sorted; later ones are farther out
            if inv["skill"] == primary:
                match_idx = i
                break
        if match_idx is not None:
            consumed[match_idx] = True
            followed.append(ann)
        else:
            ignored.append(ann)

    surprise = [inv for i, inv in enumerate(invocations) if not consumed[i]]
    return {"followed": followed, "ignored": ignored, "surprise": surprise}


def read_pending(path: Path) -> dict:
    """Return {'present': bool, 'remaining': [...], 'name': str|None}."""
    if not path.is_file():
        return {"present": False, "remaining": [], "name": None}
    try:
        data = json.loads(path.read_text() or "{}")
    except (json.JSONDecodeError, OSError):
        return {"present": False, "remaining": [], "name": None}
    remaining = list(data.get("remaining", [])) if isinstance(data, dict) else []
    return {
        "present": bool(remaining),
        "remaining": remaining,
        "name": (data.get("name") if isinstance(data, dict) else None),
    }


def _truncate(s: str, n: int = 38) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def build_summary(days: int) -> dict:
    since = time.time() - days * 86400
    announcements = parse_router_log(ROUTER_LOG, since)
    invocations = parse_usage_log(USAGE_LOG, since)
    corr = correlate(announcements, invocations)
    pending = read_pending(PENDING)

    n_ann = len(announcements)
    n_inv = len(invocations)
    follow_rate = (len(corr["followed"]) / n_ann * 100) if n_ann else 0.0

    top_routes = Counter(a["primary"] for a in announcements if a["primary"])
    top_deviations = Counter(a["primary"] for a in corr["ignored"] if a["primary"])
    top_surprises = Counter(inv["skill"] for inv in corr["surprise"] if inv["skill"])

    return {
        "days": days,
        "pending": pending,
        "announcements": n_ann,
        "invocations": n_inv,
        "followed": len(corr["followed"]),
        "ignored": len(corr["ignored"]),
        "surprise": len(corr["surprise"]),
        "follow_rate": round(follow_rate, 1),
        "top_routes": top_routes.most_common(5),
        "top_deviations": top_deviations.most_common(5),
        "top_surprises": top_surprises.most_common(5),
    }


def _print_top(label: str, items: list[tuple[str, int]], total: int | None) -> None:
    print(f"  {label}")
    if not items:
        print("    (none)")
        return
    for skill, n in items:
        if total:
            pct = n / total * 100
            print(f"    {n:>4}  {pct:5.1f}%   {_truncate(skill)}")
        else:
            print(f"    {n:>4}           {_truncate(skill)}")


def print_report(s: dict) -> None:
    print()
    print(BAR)
    print(f"  ROUTER DASHBOARD — last {s['days']} days")
    print(BAR)

    # Pending state ----------------------------------------------------------
    pending = s["pending"]
    if pending["present"]:
        skills = ", ".join(_truncate(x, 30) for x in pending["remaining"])
        chain_name = pending.get("name") or "?"
        print(f"  Pending: [!] {len(pending['remaining'])} skill(s) waiting "
              f"(chain '{chain_name}') -> {skills}")
    else:
        print("  Pending: [OK] clear")
    print()

    # Activity ---------------------------------------------------------------
    print("  Activity")
    print(f"    Announcements:  {s['announcements']}")
    print(f"    Invocations:    {s['invocations']}")
    if s["announcements"]:
        print(f"    Follow rate:    {s['follow_rate']}%  "
              f"({s['followed']}/{s['announcements']} announcements followed within "
              f"{FOLLOW_WINDOW_SEC}s)")
    else:
        print("    Follow rate:    n/a  (no announcements in window)")
    print(f"    Surprise calls: {s['surprise']}  (Skill fired without prior announcement)")
    print()

    _print_top("Top routes (most-announced primary skills)",
               s["top_routes"], s["announcements"])
    print()
    _print_top("Top deviations (announced, never invoked → TIGHTEN)",
               s["top_deviations"], None)
    print()
    _print_top("Top surprises (invoked without announcement → BROADEN)",
               s["top_surprises"], None)
    print()
    print(BAR)


def main() -> int:
    ap = argparse.ArgumentParser(description="Combined-status dashboard for the skill-router.")
    ap.add_argument("--days", type=int, default=7,
                    help="Window size in days (default 7).")
    ap.add_argument("--json", action="store_true",
                    help="Emit machine-readable JSON instead of the dashboard.")
    args = ap.parse_args()

    if args.days <= 0:
        print("error: --days must be a positive integer", file=sys.stderr)
        return 2

    summary = build_summary(args.days)

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0

    print_report(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
