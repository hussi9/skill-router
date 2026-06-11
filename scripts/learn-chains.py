#!/usr/bin/env python3
"""
learn-chains.py — surface chains the router keeps re-deriving so they can
be saved as named chains.

Reads ~/.claude/skill_router_log.jsonl, groups computed chains by canonical
step sequence, counts repetitions, and proposes named-chain entries for
sequences that fired N+ times.

Usage:
  python3 scripts/learn-chains.py            # report only
  python3 scripts/learn-chains.py --min 3    # require 3+ repeats (default)
  python3 scripts/learn-chains.py --apply    # append proposals to SKILL.personal.md
  python3 scripts/learn-chains.py --days 30  # window (default: 30 days)

The script is purely additive — it never overwrites existing chains and
only proposes new ones.
"""
import argparse, json, os, re, sys, time
from collections import Counter, defaultdict
from pathlib import Path

HOME = Path.home()
LOG = HOME / ".claude" / "skill_router_log.jsonl"
PERSONAL = HOME / ".claude" / "skills" / "skill-router" / "SKILL.personal.md"


def parse_log(path: Path, since: float) -> list[dict]:
    """Parse JSONL, drop entries older than `since` (epoch seconds)."""
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(errors="ignore").splitlines():
        try:
            e = json.loads(line)
        except Exception:
            continue
        try:
            t = time.mktime(time.strptime(e.get("ts", ""), "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            continue
        if t >= since:
            e["_t"] = t
            out.append(e)
    return out


def group_chains(entries: list[dict]) -> dict[tuple, list[dict]]:
    """Group chain-start entries by canonical step sequence."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for e in entries:
        if e.get("type") != "chain-start":
            continue
        if e.get("saved"):
            continue  # already named
        steps = tuple(e.get("steps", []))
        if not steps or len(steps) < 2:
            continue
        groups[steps].append(e)
    return groups


def existing_chains(personal_md: Path) -> set[str]:
    """Return set of canonical step-sequences already in SKILL.personal.md."""
    if not personal_md.is_file():
        return set()
    text = personal_md.read_text(errors="ignore")
    found = set()
    for m in re.finditer(r"chain:\s*([^\n]+)", text):
        chain = re.sub(r"\s+", " ", m.group(1).strip())
        found.add(chain)
    return found


def propose_name(steps: tuple) -> str:
    """Generate a short label from the step sequence."""
    head = steps[0].split(":")[-1].split("-")[0]
    tail = steps[-1].split(":")[-1].split("-")[0]
    return f"auto-{head}-to-{tail}"[:40]


def render_chain(steps: tuple) -> str:
    return " → ".join(steps)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=3, help="min repetitions to propose (default 3)")
    ap.add_argument("--days", type=int, default=30, help="window in days (default 30)")
    ap.add_argument("--apply", action="store_true", help="append proposals to SKILL.personal.md")
    args = ap.parse_args()

    since = time.time() - args.days * 86400
    entries = parse_log(LOG, since)
    if not entries:
        print(f"No log entries in the last {args.days} days at {LOG}.", file=sys.stderr)
        sys.exit(1)

    groups = group_chains(entries)
    existing = existing_chains(PERSONAL)
    proposals = []
    for steps, fires in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(fires) < args.min:
            continue
        rendered = render_chain(steps)
        if rendered in existing:
            continue
        proposals.append({"steps": steps, "fires": len(fires), "rendered": rendered})

    if not proposals:
        print(f"No chains fired {args.min}+ times in the last {args.days} days.")
        return

    print(f"Found {len(proposals)} chain(s) repeated {args.min}+ times in {args.days} days:\n")
    yaml_block = "\nchains:\n"
    for p in proposals:
        name = propose_name(p["steps"])
        print(f"  [{p['fires']}× repeats]  {p['rendered']}")
        yaml_block += f"  - name: {name}\n"
        yaml_block += f"    when: [\"<add a keyword that triggers this>\"]\n"
        yaml_block += f"    chain: {p['rendered']}\n"
    print()

    if args.apply:
        if not PERSONAL.is_file():
            print(f"No {PERSONAL} to append to. Create one first.", file=sys.stderr)
            sys.exit(1)
        with PERSONAL.open("a") as f:
            f.write("\n# --- Auto-suggested chains (edit `when:` keywords before they fire) ---")
            f.write(yaml_block)
        print(f"Appended {len(proposals)} proposal(s) to {PERSONAL}.")
        print("EDIT each `when:` keyword to match how you'd phrase the task.")
    else:
        print("Add to SKILL.personal.md (or run with --apply):")
        print(yaml_block)


if __name__ == "__main__":
    main()
