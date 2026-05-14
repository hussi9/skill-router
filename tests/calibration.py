#!/usr/bin/env python3
"""
calibration.py — large-scale accuracy harness for the deterministic router.

Runs ~100 curated prompts through router.route() and reports precision /
recall / F1 by triage path, plus overall accuracy. Designed for fast
iteration: add a new pattern, re-run, see exactly which prompts moved.

Run:
    python3 tests/calibration.py                    # report
    python3 tests/calibration.py --min-accuracy 90  # CI gate (fails <90%)
    python3 tests/calibration.py --verbose          # show every miss
    python3 tests/calibration.py --json             # emit JSON report

This complements tests/test_router.py (which is the unit-test gate).
Calibration is a *quality* gate — it runs the same code, but at scale,
to catch precision/recall regressions across the realistic distribution
of prompts.
"""
from __future__ import annotations
import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

# Calibration measures deterministic regex/table routing, not the live
# dogfood embedder daemon.
os.environ.setdefault("SKILL_ROUTER_NO_EMBED", "1")

import router  # type: ignore[import-not-found]


class Case(NamedTuple):
    prompt: str
    path: str           # BROKEN | BUILD | OPERATE | SKIP
    skill: str          # expected primary skill (substring match), or "" for SKIP


# Curated calibration set — ~100 prompts spanning every triage path,
# every domain, edge cases, and the kind of conversational/discussion
# messages that used to mis-route to "OPERATE → refactor".
CASES: list[Case] = [
    # ─────────────────────────────────────────────────────────────
    # BROKEN — errors, crashes, failing tests, prod incidents
    Case("TypeError: cannot read property map of undefined", "BROKEN", "systematic-debugging"),
    Case("ReferenceError in checkout flow", "BROKEN", "systematic-debugging"),
    Case("uncaught exception in production logs", "BROKEN", "systematic-debugging"),
    Case("the app crashes on launch", "BROKEN", "systematic-debugging"),
    Case("crashing every time I open the settings page", "BROKEN", "systematic-debugging"),
    Case("test suite is failing — 12 tests red", "BROKEN", "test-runner"),
    Case("our tests are broken after the merge", "BROKEN", "test-runner"),
    Case("failing tests in the auth module", "BROKEN", "test-runner"),
    Case("typescript is throwing 47 type errors", "BROKEN", "systematic-debugging"),
    Case("type errors in the new auth types file", "BROKEN", "systematic-debugging"),
    Case("deploy failed in CI", "BROKEN", "systematic-debugging"),
    Case("build failed on Vercel", "BROKEN", "systematic-debugging"),
    Case("CI is failing on main", "BROKEN", "systematic-debugging"),
    Case("production is down, 500 errors on /api/checkout", "BROKEN", "systematic-debugging"),
    Case("CRITICAL: database corrupted in production", "BROKEN", "systematic-debugging"),
    Case("users are losing data right now", "BROKEN", "systematic-debugging"),
    Case("there's a bug in the cart total calculation", "BROKEN", "systematic-debugging"),
    Case("regression in the search ranking", "BROKEN", "systematic-debugging"),
    Case("this doesn't work — the form keeps resetting", "BROKEN", "systematic-debugging"),
    Case("you are wrong, the migration broke staging", "BROKEN", "systematic-debugging"),

    # ─────────────────────────────────────────────────────────────
    # BUILD — new feature, file, integration, schema
    Case("Add a dark mode toggle to the settings page", "BUILD", "frontend-design"),
    Case("add a new login button to the homepage", "BUILD", "writing-plans"),
    Case("build a new dashboard component", "BUILD", "frontend-design"),
    Case("create a new profile page with tabs", "BUILD", "frontend-design"),
    Case("Build a new REST API endpoint for user analytics", "BUILD", "feature-dev"),
    Case("create a new endpoint for fetching invoices", "BUILD", "feature-dev"),
    Case("new graphql schema for the chat product", "BUILD", "writing-plans"),
    Case("integrate Stripe payments into checkout", "BUILD", "connect-apps"),
    Case("integrate Slack notifications", "BUILD", "connect-apps"),
    Case("add Twilio SMS to the order flow", "BUILD", "connect-apps"),
    Case("integrate Plaid bank linking", "BUILD", "connect-apps"),
    Case("connect Resend for transactional emails", "BUILD", "connect-apps"),
    Case("Create a new database schema for notifications", "BUILD", "db-expert"),
    Case("build a new migration to add a status column", "BUILD", "db-expert"),
    Case("new schema for the audit log table", "BUILD", "db-expert"),
    Case("Write a new Claude skill file for ML model routing", "BUILD", "writing-skills"),
    Case("write a new skill for domain validation", "BUILD", "writing-skills"),
    Case("add a new edge function that sends email on save", "BUILD", "vercel:vercel-functions"),
    Case("create a new lambda for image processing", "BUILD", "vercel:vercel-functions"),
    Case("build a new ios screen for onboarding", "BUILD", "frontend-design"),
    Case("add a new mobile screen with biometrics", "BUILD", "writing-plans"),
    Case("create a new RAG pipeline for the docs", "BUILD", "brainstorming"),
    Case("build an embedding-based search", "BUILD", "brainstorming"),

    # Multi-domain BUILD → writing-plans
    Case("build a new dashboard page that writes to the supabase database and sends emails on save", "BUILD", "writing-plans"),
    Case("create a settings page that updates user permissions and triggers an audit webhook", "BUILD", "writing-plans"),

    # Ambiguity (X AND Y) → BUILD via brainstorming
    Case("Fix the login bug AND add OAuth support", "BUILD", "brainstorming"),
    Case("Refactor the auth module AND add tests", "BUILD", "brainstorming"),
    Case("fix the cart bug AND add wishlist feature", "BUILD", "brainstorming"),

    # ─────────────────────────────────────────────────────────────
    # OPERATE — refactor / clean / tests / deploy / review / ship
    Case("the auth service has grown to 800 lines, clean it up", "OPERATE", "refactor"),
    Case("refactor the user controller", "OPERATE", "refactor"),
    Case("tidy up the helpers folder", "OPERATE", "refactor"),
    Case("simplify the order state machine", "OPERATE", "refactor"),
    Case("clean up the dead code in api/", "OPERATE", "refactor"),
    Case("add test coverage to the payment module", "OPERATE", "test-driven-development"),
    Case("add tests for the cart service", "OPERATE", "test-driven-development"),
    Case("add coverage for the auth flow", "OPERATE", "test-driven-development"),
    Case("deploy the current branch to production", "OPERATE", "verification-before-completion"),
    Case("deploy main to prod", "OPERATE", "verification-before-completion"),
    Case("Review my PR before I merge", "OPERATE", "requesting-code-review"),
    Case("can you do a code review on this branch", "OPERATE", "requesting-code-review"),
    Case("pr review for the auth module", "OPERATE", "requesting-code-review"),
    Case("merge this branch to main", "OPERATE", "finishing-a-development-branch"),
    Case("ship this feature", "OPERATE", "finishing-a-development-branch"),

    # ─────────────────────────────────────────────────────────────
    # SKIP — discussion, opinion, clarification, factual lookup
    Case("what does this function do?", "SKIP", ""),
    Case("what is the difference between map and flatMap?", "SKIP", ""),
    Case("how does the cache layer work", "SKIP", ""),
    Case("how do I run the tests locally", "SKIP", ""),
    Case("explain the auth flow", "SKIP", ""),
    Case("show me line 42 of auth.ts", "SKIP", ""),
    Case("where is the api router defined", "SKIP", ""),
    Case("is there a dark mode setting somewhere", "SKIP", ""),
    Case("can you tell me more about the recent migration", "SKIP", ""),
    Case("what do you think about this approach?", "SKIP", ""),
    Case("do you agree with the way I structured the components?", "SKIP", ""),
    Case("do you have any better ideas?", "SKIP", ""),
    Case("what's your take on this design?", "SKIP", ""),
    Case("what's your opinion on the trade-off here?", "SKIP", ""),
    Case("let me know your thoughts on the routing", "SKIP", ""),
    Case("any concerns with merging this?", "SKIP", ""),
    Case("any thoughts on the architecture?", "SKIP", ""),
    Case("any feedback on this proposal?", "SKIP", ""),
    Case("can we brainstorm the new pricing model", "SKIP", ""),
    Case("let's discuss the API surface area", "SKIP", ""),
    Case("do you remember the cache invalidation issue from last week", "SKIP", ""),
    Case("your initial answer was confusing — can you clarify", "SKIP", ""),
    Case("your prior assumption about the schema was wrong", "SKIP", ""),
    Case("alternative ideas for the onboarding flow?", "SKIP", ""),
    # The one that triggered this whole refactor:
    Case("does this approach have the same chat style discovery to deep dive flow? "
         "do you agree? let me know if you have questions or better ideas, "
         "i'm open to brainstorm", "SKIP", ""),
    # Long technical question (the old code wrongly fired because of length gate)
    Case("what is the difference between react server components and client components "
         "in next.js apps and how do they affect bundle size and rendering", "SKIP", ""),

    # ─────────────────────────────────────────────────────────────
    # Edge cases — combinations the router gets wrong easily
    Case("[no-router] refactor the auth module", "SKIP", ""),  # escape hatch
    Case("[skip-router] add a new feature", "SKIP", ""),
    Case("write a function that returns the user's full name", "SKIP", ""),  # no clear signal
    Case("the new design needs review", "SKIP", ""),  # ambiguous "review" w/o "code review"
    Case("let me think about the architecture more", "SKIP", ""),

    # ─────────────────────────────────────────────────────────────
    # Regression guards — tightened `refactor` pattern. These are real-world
    # phrasings that USED to fire OPERATE→refactor and shouldn't anymore.
    # Past-tense "broke" alone reads as discussion — router stays quiet until
    # the user explicitly asks for help. (Existing BROKEN_RE catches 'failed',
    # 'crashing', 'errors', 'bug', etc.; bare 'broke' is too ambiguous.)
    Case("the recent refactor broke the auth flow", "SKIP", ""),
    Case("after that refactor we lost the cache invalidation", "SKIP", ""),
    Case("can you walk me through the refactor you proposed", "SKIP", ""),
    Case("the refactor is taking longer than expected", "SKIP", ""),
    Case("review my refactor PR for the auth module", "OPERATE", "requesting-code-review"),
    # Imperative refactor still routes correctly (positive guards)
    Case("please refactor the order state machine", "OPERATE", "refactor"),
    Case("can you refactor auth.ts to drop the legacy branch", "OPERATE", "refactor"),
    Case("let's refactor this controller", "OPERATE", "refactor"),

    # ─────────────────────────────────────────────────────────────
    # Sub-agent / plugin bootstrap prompts that leak into transcripts. None of
    # these are user prompts; all must SKIP regardless of contained keywords.
    Case("Hello memory agent, you are continuing to observe the primary Claude session", "SKIP", ""),
    Case("You are a Claude-Mem, a specialized observer tool for creating searchable memory", "SKIP", ""),
    Case("--- MODE SWITCH: PROGRESS SUMMARY ---\nCRITICAL TAG REQUIREMENT — READ CAREFULLY", "SKIP", ""),
    Case("<observed_from_primary_session>\n  <user_request>fix the bug</user_request>", "SKIP", ""),
    # Negative guard: the sub-agent SKIP filters must NOT swallow real bug
    # reports that happen to use words like 'CRITICAL' or 'error'. These are
    # genuine BROKEN signals.
    Case("I got a CRITICAL error in production, users can't sign in", "BROKEN", ""),
    Case("CRITICAL: payment processing is failing for all users", "BROKEN", ""),

    # ─────────────────────────────────────────────────────────────
    # Negative guard for the broadened _REVIEW_RE pattern. "review my notes"
    # is conversational, not a code-review request — must NOT route OPERATE.
    Case("can you review my notes from the pr discussion", "SKIP", ""),
]


def evaluate() -> dict:
    by_path = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "total": 0})
    skill_correct = 0
    skill_eligible = 0
    misses: list[dict] = []

    for case in CASES:
        # Use route() (not main()) to avoid touching the pending-state file.
        # Bypass the escape-hatch handling in main() by routing directly.
        if any(m in case.prompt.lower() for m in router.ESCAPE_MARKERS):
            actual_path, chain = "SKIP", []
        else:
            actual_path, chain, _, _ = router.route(case.prompt)
        actual_skill = chain[0].skill if chain else ""

        # Path metrics — per-class precision/recall
        by_path[case.path]["total"] += 1
        if actual_path == case.path:
            by_path[case.path]["tp"] += 1
        else:
            by_path[case.path]["fn"] += 1
            by_path[actual_path]["fp"] += 1

        # Skill match — only when path is correct AND a skill was expected
        if case.skill:
            skill_eligible += 1
            if case.skill in actual_skill:
                skill_correct += 1

        ok = (actual_path == case.path) and (
            not case.skill or case.skill in actual_skill
        )
        if not ok:
            misses.append({
                "prompt": case.prompt[:90],
                "expected": f"{case.path}/{case.skill or '-'}",
                "actual":   f"{actual_path}/{actual_skill or '-'}",
            })

    total_path_correct = sum(d["tp"] for d in by_path.values())
    total = len(CASES)
    path_accuracy = total_path_correct / total * 100
    skill_accuracy = (skill_correct / skill_eligible * 100) if skill_eligible else 100.0

    per_path = {}
    for path, d in sorted(by_path.items()):
        tp, fp, fn, n = d["tp"], d["fp"], d["fn"], d["total"]
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall    = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per_path[path] = {
            "n": n, "tp": tp, "fp": fp, "fn": fn,
            "precision": round(precision * 100, 1),
            "recall":    round(recall * 100, 1),
            "f1":        round(f1 * 100, 1),
        }

    return {
        "total": total,
        "path_correct": total_path_correct,
        "path_accuracy": round(path_accuracy, 1),
        "skill_accuracy": round(skill_accuracy, 1),
        "per_path": per_path,
        "misses": misses,
    }


def print_report(report: dict, verbose: bool) -> None:
    bar = "─" * 72
    print()
    print(bar)
    print(f"  ROUTER CALIBRATION — {report['total']} prompts")
    print(bar)
    print(f"  Path accuracy:  {report['path_correct']}/{report['total']}  ({report['path_accuracy']}%)")
    print(f"  Skill accuracy: {report['skill_accuracy']}%  (only counted when path is correct)")
    print()
    print(f"  {'PATH':<10}{'N':>5}{'TP':>5}{'FP':>5}{'FN':>5}{'Prec':>9}{'Recl':>9}{'F1':>9}")
    for path, m in report["per_path"].items():
        print(f"  {path:<10}{m['n']:>5}{m['tp']:>5}{m['fp']:>5}{m['fn']:>5}"
              f"{m['precision']:>8}%{m['recall']:>8}%{m['f1']:>8}%")
    print()

    misses = report["misses"]
    if misses:
        print(f"  {len(misses)} miss(es):")
        limit = None if verbose else 10
        items = misses[:limit] if limit else misses
        for m in items:
            print(f"    expected={m['expected']:<30} got={m['actual']:<30} :: {m['prompt']!r}")
        if limit and len(misses) > limit:
            print(f"    ... and {len(misses) - limit} more (use --verbose to see all)")
    else:
        print("  No misses — full accuracy.")
    print(bar)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-accuracy", type=float, default=0.0,
                    help="Fail with non-zero exit if path accuracy is below this percent.")
    ap.add_argument("--verbose", action="store_true",
                    help="Show every miss instead of capping at 10.")
    ap.add_argument("--json", action="store_true",
                    help="Emit JSON report instead of human-readable.")
    args = ap.parse_args()

    report = evaluate()

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report, args.verbose)

    if args.min_accuracy and report["path_accuracy"] < args.min_accuracy:
        print(f"\n  FAIL: accuracy {report['path_accuracy']}% < required {args.min_accuracy}%")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
