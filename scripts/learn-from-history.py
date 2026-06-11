#!/usr/bin/env python3
"""
learn-from-history.py — turn the router from a static rulebook into a
self-tuning system by joining what was *announced* with what was actually
*invoked*.

Reads:
  ~/.claude/skill_router_log.jsonl  — chain-start / chain-step events written
                                      by scripts/router.py
  ~/.claude/skill_usage.log         — TAB-separated log of Skill tool fires
                                      (timestamp\tskill-name)

Joins by minute-level timestamp proximity (announcements typically fire
seconds before the model invokes the skill). Reports:

  ✓ Followed:     announcement → matching Skill invocation within 2 minutes
  ✗ Ignored:      announcement with NO matching Skill invocation in window
  ?  Surprise:    Skill invocation with NO prior announcement (router missed
                  this prompt — a tuning opportunity)

Output ends with concrete tuning suggestions: which announced skills are
ignored most often (lower trust → tighten patterns), and which skills are
invoked despite SKIP (router should learn to fire on those triggers).

Run:
    python3 scripts/learn-from-history.py                 # last 7 days
    python3 scripts/learn-from-history.py --days 30
    python3 scripts/learn-from-history.py --verbose       # per-event detail
    python3 scripts/learn-from-history.py --json
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable

ROUTER_LOG = Path.home() / ".claude" / "skill_router_log.jsonl"
USAGE_LOG  = Path.home() / ".claude" / "skill_usage.log"

# How long after an announcement we still credit a Skill invocation as
# "following the route." Anything beyond this is treated as an unrelated call.
FOLLOW_WINDOW_SEC = 120


def parse_router_log(path: Path, since: float) -> list[dict]:
    """Return chain-start events (with .skills, .ts) since the cutoff."""
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(errors="ignore").splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("type") != "chain-start":
            continue
        try:
            t = time.mktime(time.strptime(e.get("ts", ""), "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            continue
        if t < since:
            continue
        out.append({
            "ts": t,
            "name": e.get("name", "?"),
            "skills": list(e.get("steps", [])),
        })
    out.sort(key=lambda x: x["ts"])
    return out


def parse_usage_log(path: Path, since: float) -> list[dict]:
    """Return Skill tool fires (with .skill, .ts) since the cutoff."""
    if not path.is_file():
        return []
    out: list[dict] = []
    for line in path.read_text(errors="ignore").splitlines():
        # Two formats coexist: TAB-separated (modern) and SPACE-separated (old).
        line = line.strip()
        if not line:
            continue
        if "\t" in line:
            ts_str, _, skill = line.partition("\t")
        else:
            # 'YYYY-MM-DD HH:MM:SS skill-name' — split on last whitespace
            parts = line.rsplit(" ", 1)
            if len(parts) != 2:
                continue
            ts_str, skill = parts
        skill = skill.strip()
        try:
            t = time.mktime(time.strptime(ts_str.strip(), "%Y-%m-%d %H:%M:%S"))
        except Exception:
            continue
        if t < since:
            continue
        out.append({"ts": t, "skill": skill})
    out.sort(key=lambda x: x["ts"])
    return out


def correlate(announcements: list[dict], invocations: list[dict]) -> dict:
    """Walk announcements in time order and try to match each one to a
    nearby Skill invocation. Each invocation can satisfy at most one
    announcement (by primary skill, within the follow window)."""
    consumed = [False] * len(invocations)
    followed: list[dict] = []
    ignored: list[dict] = []

    for ann in announcements:
        primary = ann["skills"][0] if ann["skills"] else ""
        match_idx = None
        for i, inv in enumerate(invocations):
            if consumed[i]:
                continue
            if inv["ts"] < ann["ts"]:
                continue  # before announcement; can't be the response
            if inv["ts"] - ann["ts"] > FOLLOW_WINDOW_SEC:
                break  # too late and list is sorted
            if inv["skill"] == primary:
                match_idx = i
                break
        if match_idx is not None:
            consumed[match_idx] = True
            followed.append({**ann, "invoked_at": invocations[match_idx]["ts"]})
        else:
            ignored.append(ann)

    surprise = [inv for i, inv in enumerate(invocations) if not consumed[i]]
    return {"followed": followed, "ignored": ignored, "surprise": surprise}


def tuning_suggestions(corr: dict, top: int = 5) -> list[str]:
    """Translate correlation deltas into concrete pattern tweaks."""
    suggestions: list[str] = []

    # Most-ignored announced skills → tighten their triggering patterns.
    ignored_count = Counter()
    for ann in corr["ignored"]:
        primary = ann["skills"][0] if ann["skills"] else ""
        if primary:
            ignored_count[primary] += 1
    for skill, n in ignored_count.most_common(top):
        suggestions.append(
            f"  TIGHTEN  '{skill}' was announced {n}× but never invoked. "
            f"Patterns triggering this skill may have false positives — "
            f"check OPERATE_RE / BUILD_RE / BROKEN_RE for over-broad words."
        )

    # Most-surprising invoked skills → router missed the trigger; broaden patterns.
    surprise_count = Counter(inv["skill"] for inv in corr["surprise"])
    for skill, n in surprise_count.most_common(top):
        suggestions.append(
            f"  BROADEN  '{skill}' was invoked {n}× without a prior "
            f"announcement — router missed those prompts. Consider adding "
            f"signal patterns that trigger this skill."
        )

    return suggestions


def print_report(corr: dict, days: int, verbose: bool) -> None:
    bar = "─" * 72
    n_ann = len(corr["followed"]) + len(corr["ignored"])
    n_inv = len(corr["followed"]) + len(corr["surprise"])
    follow_rate = (len(corr["followed"]) / n_ann * 100) if n_ann else 0.0

    print()
    print(bar)
    print(f"  ROUTER LEARNING — last {days} days")
    print(bar)
    print(f"  Announcements:  {n_ann}")
    print(f"  Invocations:    {n_inv}")
    print(f"  Follow rate:    {follow_rate:.1f}%  ({len(corr['followed'])}/{n_ann} announcements led to a Skill call)")
    print(f"  Surprise calls: {len(corr['surprise'])}  (Skill fired without a router announcement)")
    print()

    suggestions = tuning_suggestions(corr)
    if suggestions:
        print("  Tuning suggestions:")
        for s in suggestions:
            print(s)
    else:
        print("  No actionable signal yet — keep using the router and re-run.")
    print()

    if verbose:
        if corr["ignored"]:
            print("  Ignored announcements (most recent first):")
            for ann in sorted(corr["ignored"], key=lambda x: -x["ts"])[:20]:
                ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(ann["ts"]))
                print(f"    {ts}  {ann['name']:<30} → {','.join(ann['skills'])}")
            print()
        if corr["surprise"]:
            print("  Surprise invocations (most recent first):")
            for inv in sorted(corr["surprise"], key=lambda x: -x["ts"])[:20]:
                ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(inv["ts"]))
                print(f"    {ts}  {inv['skill']}")
            print()
    print(bar)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7,
                    help="Window size in days (default 7).")
    ap.add_argument("--verbose", action="store_true",
                    help="Show per-event detail for ignored / surprise lists.")
    ap.add_argument("--json", action="store_true",
                    help="Emit a JSON report instead of human-readable.")
    args = ap.parse_args()

    since = time.time() - args.days * 86400
    announcements = parse_router_log(ROUTER_LOG, since)
    invocations   = parse_usage_log(USAGE_LOG, since)

    if not announcements and not invocations:
        print(f"No router or usage events found in the last {args.days} days.\n"
              f"Use the router for a few real prompts, then re-run.", file=sys.stderr)
        return 1

    corr = correlate(announcements, invocations)

    if args.json:
        # Strip non-serializable timestamps to ISO before dumping
        serializable = {
            "window_days": args.days,
            "summary": {
                "announcements": len(corr["followed"]) + len(corr["ignored"]),
                "invocations":   len(corr["followed"]) + len(corr["surprise"]),
                "followed":      len(corr["followed"]),
                "ignored":       len(corr["ignored"]),
                "surprise":      len(corr["surprise"]),
            },
            "tuning_suggestions": tuning_suggestions(corr),
        }
        print(json.dumps(serializable, indent=2))
    else:
        print_report(corr, args.days, args.verbose)

    return 0


if __name__ == "__main__":
    sys.exit(main())
