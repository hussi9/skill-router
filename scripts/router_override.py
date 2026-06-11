#!/usr/bin/env python3
"""Record a reasoned override of the current skill-router IRON RULE.

The collaborative escape hatch (see docs/self-improvement.md). When the router
announces a skill the model judges wrong for the actual task, the model runs:

    python3 ~/.claude/skills/skill-router/scripts/router_override.py "<reason>"

This clears the pending IRON RULE for the current turn AND records the reason so
the router learns to defer that skill on similar prompts. A reason is required —
the friction is intentional: every override leaves an auditable trail in
~/.claude/skill_router_overrides.jsonl that the weekly analysis can mine.

This is how the router "asks and learns from" the model instead of fighting it:
the model doesn't silently ignore the rule (which the Stop hook would block) and
the user doesn't have to type [no-router] — the model states *why* and proceeds.

Usage:
    router_override.py "<reason>" [--prompt "<original prompt>"]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make scripts/ importable so we reuse the router's state paths + logic.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import router  # type: ignore[import-not-found]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Record a reasoned skill-router override and clear the IRON RULE."
    )
    ap.add_argument("reason", help="Why the announced route is wrong for this task.")
    ap.add_argument(
        "--prompt",
        default=None,
        help="Original user prompt (stored as a hash only, for audit correlation).",
    )
    args = ap.parse_args()

    reason = (args.reason or "").strip()
    if not reason:
        print("[skill-router] override needs a reason — nothing recorded.", file=sys.stderr)
        return 2

    result = router.record_override(reason, prompt=args.prompt)
    skill = result.get("skill")
    if skill:
        print(f"[skill-router] override recorded for {skill}: {reason}")
        print(
            f"[skill-router] {skill} override count = {result.get('count')} "
            f"(defers at {router.OVERRIDE_THRESHOLD}). Pending rule cleared — proceed."
        )
    else:
        print(f"[skill-router] no pending rule found; reason logged anyway: {reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
