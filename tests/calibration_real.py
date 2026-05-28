#!/usr/bin/env python3
"""
calibration_real.py — distribution sampler for real user prompts.

Complements tests/calibration.py (which uses 94 curated prompts with
ground-truth labels). This script samples actual prompts from the local
Claude Code transcript store at ~/.claude/projects/*/*.jsonl, runs each
through router.route(), and reports the distribution by triage path
plus an over-firing heuristic.

There is no ground truth here — the goal is to surface anomalies in the
real-world distribution (e.g., "47% of prompts route to OPERATE → refactor"
suggests a pattern over-firing) so the curated calibration set can be
extended or the patterns tightened.

Run:
    python3 tests/calibration_real.py                # 200 samples, last 30 days
    python3 tests/calibration_real.py --max 500
    python3 tests/calibration_real.py --days 7
    python3 tests/calibration_real.py --json

Critical: imports router and calls router.route(prompt) directly. Does
NOT shell out to `python3 scripts/router.py` (that would write pending
state via main() and trap subsequent tool calls), and does NOT call
router.main(). route() is the side-effect-free entrypoint.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

# This sampler measures deterministic table routing over real prompts. The
# live hook can enable embedding fallback separately during dogfood.
os.environ.setdefault("SKILL_ROUTER_NO_EMBED", "1")

import router  # type: ignore[import-not-found]


PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Substrings that mark harness echo / hook noise rather than genuine user input.
HARNESS_MARKERS = (
    "[skill-router]",
    "Stop hook",
    "PreToolUse:",
    "IRON RULE",
    "hookSpecificOutput",
)


def extract_text(content: object) -> str:
    """Pull plain user text from a transcript event's `message.content`.

    Returns "" when the content is a tool_result, image-only, or otherwise
    not a real user prompt.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            # Skip tool_result blocks — those are harness payloads, not prompts.
            if block.get("type") == "tool_result":
                return ""
            if block.get("type") == "text":
                txt = block.get("text", "")
                if isinstance(txt, str):
                    parts.append(txt)
        return "\n".join(parts)
    return ""


def is_harness_echo(prompt: str) -> bool:
    """True if the prompt looks like a hook announcement or harness payload."""
    stripped = prompt.lstrip()
    if stripped.startswith("[skill-router]"):
        return True
    if stripped.startswith("Stop hook"):
        return True
    if stripped.startswith("PreToolUse:"):
        return True
    # Slash-command echoes wrapped in <command-name> tags also aren't user
    # intent in the routing sense — treat as harness noise.
    if stripped.startswith("<command-name>"):
        return True
    if stripped.startswith("<local-command-"):
        return True
    return any(marker in prompt for marker in ("IRON RULE", "hookSpecificOutput"))


def collect_prompts(max_samples: int, max_age_days: int) -> list[str]:
    """Walk transcript files newer than `max_age_days` and return up to
    `max_samples` unique user prompts, most-recent first.
    """
    if not PROJECTS_DIR.exists():
        return []

    cutoff = time.time() - (max_age_days * 86400) if max_age_days > 0 else 0.0

    # Gather candidate files newest-first so we naturally bias toward recent prompts.
    files: list[Path] = []
    for jsonl in PROJECTS_DIR.glob("*/*.jsonl"):
        try:
            mtime = jsonl.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        files.append(jsonl)
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    # Dedupe by exact prompt text; the first time we see a prompt (newest file)
    # wins, so seen set order is most-recent-first.
    seen: dict[str, None] = {}
    for jsonl in files:
        if len(seen) >= max_samples:
            break
        try:
            with jsonl.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if len(seen) >= max_samples:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") != "user":
                        continue
                    if event.get("isMeta"):
                        continue
                    msg = event.get("message") or {}
                    if not isinstance(msg, dict):
                        continue
                    text = extract_text(msg.get("content"))
                    if not text:
                        continue
                    text = text.strip()
                    if not text:
                        continue
                    if is_harness_echo(text):
                        continue
                    if text in seen:
                        continue
                    seen[text] = None
        except OSError:
            continue

    return list(seen.keys())[:max_samples]


def analyze(prompts: list[str]) -> dict:
    """Run prompts through router.route() and tally the distribution."""
    path_counts: Counter[str] = Counter()
    skill_counts: Counter[str] = Counter()
    domain_counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}

    for prompt in prompts:
        # Honor the documented escape hatch the same way main() does.
        if any(marker in prompt.lower() for marker in router.ESCAPE_MARKERS):
            path = "SKIP"
            primary_skill = ""
            domains: list[str] = []
        else:
            path, chain, domains, _announcement = router.route(prompt)
            primary_skill = chain[0].skill if chain else ""

        path_counts[path] += 1
        for d in domains:
            domain_counts[d] += 1
        if primary_skill:
            skill_counts[primary_skill] += 1
            bucket = examples.setdefault(primary_skill, [])
            if len(bucket) < 3:
                bucket.append(prompt[:120])

    non_skip_total = sum(c for p, c in path_counts.items() if p != "SKIP")

    # Anomaly heuristic: any single skill claiming >30% of *non-SKIP* routes
    # is likely over-firing. Threshold mirrors the curated set's expected
    # distribution (no single skill should dominate a real workload).
    anomalies: list[dict] = []
    if non_skip_total:
        for skill, count in skill_counts.most_common():
            share = count / non_skip_total
            if share > 0.30:
                anomalies.append({
                    "skill": skill,
                    "count": count,
                    "share_of_non_skip": round(share * 100, 1),
                    "examples": examples.get(skill, []),
                })

    return {
        "sample_size": len(prompts),
        "path_distribution": {
            path: {
                "count": count,
                "share": round(count / len(prompts) * 100, 1) if prompts else 0.0,
            }
            for path, count in path_counts.most_common()
        },
        "non_skip_total": non_skip_total,
        "top_skills": [
            {
                "skill": skill,
                "count": count,
                "share_of_non_skip": (
                    round(count / non_skip_total * 100, 1) if non_skip_total else 0.0
                ),
            }
            for skill, count in skill_counts.most_common(5)
        ],
        "top_domains": [
            {"domain": d, "count": c} for d, c in domain_counts.most_common(5)
        ],
        "anomalies": anomalies,
    }


def print_report(report: dict) -> None:
    bar = "─" * 72
    print()
    print(bar)
    print(f"  ROUTER REAL-PROMPT CALIBRATION — {report['sample_size']} prompts")
    print(bar)

    print("  Path distribution:")
    for path, stats in report["path_distribution"].items():
        print(f"    {path:<10}{stats['count']:>5}  ({stats['share']}%)")
    print()

    if report["top_skills"]:
        print("  Top 5 announced primary skills (of non-SKIP routes):")
        for entry in report["top_skills"]:
            print(f"    {entry['skill']:<40}{entry['count']:>5}  ({entry['share_of_non_skip']}% of non-SKIP)")
        print()

    if report["top_domains"]:
        print("  Top 5 detected domains:")
        for entry in report["top_domains"]:
            print(f"    {entry['domain']:<40}{entry['count']:>5}")
        print()

    if report["anomalies"]:
        print("  ⚠ Anomalies (>30% of non-SKIP routes — likely over-firing):")
        for a in report["anomalies"]:
            print(f"    {a['skill']}  ({a['share_of_non_skip']}%, {a['count']} prompts)")
            for ex in a["examples"]:
                print(f"      e.g. {ex!r}")
        print()
    else:
        print("  No over-firing anomalies detected (no skill > 30% of non-SKIP).")
        print()
    print(bar)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--max", type=int, default=200,
                    help="Maximum number of unique prompts to sample (default: 200).")
    ap.add_argument("--days", type=int, default=30,
                    help="Only include transcripts modified in the last N days "
                         "(default: 30; 0 = no age filter).")
    ap.add_argument("--json", action="store_true",
                    help="Emit JSON report instead of human-readable.")
    args = ap.parse_args()

    prompts = collect_prompts(max_samples=args.max, max_age_days=args.days)
    if not prompts:
        msg = {
            "sample_size": 0,
            "path_distribution": {},
            "non_skip_total": 0,
            "top_skills": [],
            "top_domains": [],
            "anomalies": [],
            "note": "No prompts found — check ~/.claude/projects/*/*.jsonl exists "
                    "and try --days 0 to disable the age filter.",
        }
        if args.json:
            print(json.dumps(msg, indent=2))
        else:
            print("\n  No real prompts found in the configured window.")
            print(f"  Looked under: {PROJECTS_DIR} (last {args.days} days)\n")
        return 0

    report = analyze(prompts)

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
