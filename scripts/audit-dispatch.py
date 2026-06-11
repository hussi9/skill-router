#!/usr/bin/env python3
"""
audit-dispatch.py — verify the router is actually following the dispatch
protocol described in references/dispatch-protocol.md.

Reads ~/.claude/skill_router_log.jsonl and scores: of every announced chain,
how many had complete per-step dispatch logged?

A low score means the router is announcing chains but skipping the
subagent-launch protocol — falling back to in-session Skill calls and
losing per-step model enforcement.

Usage:
  python3 scripts/audit-dispatch.py
  python3 scripts/audit-dispatch.py --days 7
  python3 scripts/audit-dispatch.py --verbose  # show each chain's status
"""
import argparse, json, sys, time
from pathlib import Path

LOG = Path.home() / ".claude" / "skill_router_log.jsonl"


def parse(path: Path, since: float) -> list[dict]:
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
    out.sort(key=lambda x: x["_t"])
    return out


def audit(entries: list[dict]) -> list[dict]:
    """Walk events in order. For each chain-start, count chain-steps until
    chain-end (or next chain-start). Return per-chain scorecard."""
    chains = []
    current = None
    for e in entries:
        t = e.get("type")
        if t == "chain-start":
            if current:
                chains.append(current)
            current = {
                "name": e.get("name", "?"),
                "expected_steps": len(e.get("steps", [])),
                "logged_steps": 0,
                "models": e.get("models", []),
                "saved": e.get("saved", False),
                "thinking_seen": False,
                "ts": e.get("ts", ""),
            }
        elif t == "chain-step" and current:
            current["logged_steps"] += 1
        elif t == "thinking-active" and current:
            current["thinking_seen"] = True
        elif t == "chain-end" and current:
            chains.append(current)
            current = None
    if current:
        chains.append(current)
    return chains


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    since = time.time() - args.days * 86400
    entries = parse(LOG, since)
    if not entries:
        print(f"No log entries in the last {args.days} days at {LOG}.", file=sys.stderr)
        sys.exit(1)

    chains = audit(entries)
    if not chains:
        print("No chain-start events in window.")
        return

    full = sum(1 for c in chains if c["logged_steps"] >= c["expected_steps"])
    partial = sum(1 for c in chains if 0 < c["logged_steps"] < c["expected_steps"])
    skipped = sum(1 for c in chains if c["logged_steps"] == 0)
    total = len(chains)

    print(f"\nDispatch protocol audit — last {args.days} days, {total} chains")
    print(f"  Full per-step dispatch logged: {full}/{total} ({100*full//total}%)")
    print(f"  Partial:                       {partial}/{total}")
    print(f"  Announced but no steps logged: {skipped}/{total}")

    score = (2 * full + partial) / (2 * total) * 100
    if score >= 80:
        verdict = "healthy"
    elif score >= 50:
        verdict = "partial — router announces but often skips subagent dispatch"
    else:
        verdict = "broken — protocol is documented but not being followed"
    print(f"\n  Compliance score: {score:.0f}/100  ->  {verdict}")

    if args.verbose:
        print("\n  Per-chain detail:")
        for c in chains:
            if c["logged_steps"] >= c["expected_steps"]:
                mark = "OK"
            elif c["logged_steps"] > 0:
                mark = "PARTIAL"
            else:
                mark = "SKIPPED"
            saved = " (saved)" if c["saved"] else ""
            think = " [thinking]" if c["thinking_seen"] else ""
            print(f"    {mark:8} {c['ts']}  {c['name']}{saved}{think}  "
                  f"{c['logged_steps']}/{c['expected_steps']} steps")

    if score < 80:
        print(f"\n  Likely cause: the router announces a chain but runs steps in-session")
        print(f"  instead of dispatching via Agent. Re-check references/dispatch-protocol.md.")


if __name__ == "__main__":
    main()
