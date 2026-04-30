#!/usr/bin/env python3
"""
Unit tests for scripts/router.py — fast, deterministic, no Claude needed.

Covers:
  1. The 20 ground-truth cases from run_routing_test.sh
  2. The SKIP-by-default precision regression (the bug this refactor fixes)
  3. Output format checks ('Invoke now:' wording, no 'Dispatching now...')

Run:  python3 -m unittest discover tests
"""
from __future__ import annotations
import json
import os
import sys
import unittest
from pathlib import Path

# Make scripts/ importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import router  # type: ignore[import-not-found]


# ---- Ground truth from run_routing_test.sh ---------------------------------
# (id, prompt, expected_path, expected_skill_substring)
GROUND_TRUTH: list[tuple[int, str, str, str]] = [
    (1,  "TypeError: Cannot read property map of undefined in ProductList.tsx line 42",
         "BROKEN", "systematic-debugging"),
    (2,  "My test suite is failing after the refactor — 12 tests red",
         "BROKEN", "test-runner"),
    (3,  "Production is down. 500 errors on /api/checkout for the last 10 minutes",
         "BROKEN", "systematic-debugging"),
    (4,  "TypeScript is throwing 47 type errors after I updated the auth types",
         "BROKEN", "typescript-expert"),
    (5,  "The deploy failed — Vercel build error in CI pipeline",
         "BROKEN", "systematic-debugging"),
    (6,  "CRITICAL: database corrupted in production, users losing data right now",
         "BROKEN", "systematic-debugging"),
    (7,  "Add a dark mode toggle to the settings page",
         "BUILD", "frontend-design"),
    (8,  "Build a new REST API endpoint for user analytics",
         "BUILD", "system-design"),
    (9,  "I need to integrate Stripe payments into checkout",
         "BUILD", "stripe"),
    (10, "Create a new database schema for the notifications system",
         "BUILD", "db-expert"),
    (11, "Write a new Claude skill file for ML model routing",
         "BUILD", "writing-skills"),
    (12, "The auth service has grown to 800 lines. Clean it up.",
         "OPERATE", "refactor"),
    (13, "Add test coverage to the payment module — it has 0% tests",
         "OPERATE", "test-driven-development"),
    (14, "Deploy the current branch to production",
         "OPERATE", "verification-before-completion"),
    (15, "Review my PR before I merge",
         "OPERATE", "requesting-code-review"),
    (16, "Fix the login bug AND add OAuth support while you are at it",
         "BUILD", "brainstorming"),
    (17, "Refactor the auth module AND add tests to it",
         "BUILD", "brainstorming"),
    (18, "What does this function do?",                           "SKIP", ""),
    (19, "What is the difference between map and flatMap?",       "SKIP", ""),
    (20, "Show me line 42 of auth.ts",                            "SKIP", ""),
]


class TestGroundTruth(unittest.TestCase):
    """The 20 cases that score_results.py grades against."""

    def test_all(self) -> None:
        for tid, prompt, want_path, want_skill in GROUND_TRUTH:
            with self.subTest(id=tid, prompt=prompt[:50]):
                path, chain, _, _ = router.route(prompt)
                self.assertEqual(path, want_path,
                    f"#{tid}: expected {want_path}, got {path}")
                if want_skill:
                    skills = " ".join(s.skill for s in chain)
                    self.assertIn(want_skill, skills,
                        f"#{tid}: expected skill containing '{want_skill}', got '{skills}'")
                else:
                    self.assertEqual(chain, [],
                        f"#{tid}: SKIP must yield empty chain")


class TestSkipByDefault(unittest.TestCase):
    """The bug this refactor fixes: don't classify random discussion as refactor."""

    SHOULD_SKIP = [
        # The exact kind of message that triggered this fix:
        "does this approach have the same chat style discovery to deep dive flow? "
        "do you agree? let me know if you have any questions or better ideas, "
        "i am open to brainstorm.",
        # Pure feedback / opinion solicitation
        "what do you think about this approach?",
        "do you have any better ideas?",
        "what's your take on the design?",
        "let me know your thoughts",
        # Discussion / brainstorm requests
        "can we brainstorm the architecture together",
        "let's discuss the trade-offs here",
        # Long factual questions (the old code skipped these only if < 14 words)
        "what is the difference between react server components and client components "
        "and how do they affect bundle size and rendering performance in next.js apps",
        # Recall / continuity
        "do you remember what we discussed about the cache layer last week",
    ]

    SHOULD_NOT_SKIP = [
        # Real action prompts that must still route
        "refactor the auth module",
        "build a new dashboard",
        "fix the typescript errors",
        "deploy to production",
        "add tests for the payment service",
    ]

    def test_skip(self) -> None:
        for prompt in self.SHOULD_SKIP:
            with self.subTest(prompt=prompt[:60]):
                path, chain, _, ann = router.route(prompt)
                self.assertEqual(path, "SKIP",
                    f"discussion prompt was misrouted to {path}: {prompt[:60]!r}")
                self.assertEqual(ann, "")
                self.assertEqual(chain, [])

    def test_no_skip(self) -> None:
        for prompt in self.SHOULD_NOT_SKIP:
            with self.subTest(prompt=prompt):
                path, chain, _, _ = router.route(prompt)
                self.assertNotEqual(path, "SKIP",
                    f"action prompt was incorrectly skipped: {prompt!r}")
                self.assertGreater(len(chain), 0)


class TestAnnouncementWording(unittest.TestCase):
    """Wording must be honest. The hook can't dispatch — only the model can."""

    def test_no_dispatching_now_lie(self) -> None:
        prompt = "refactor the auth module"
        _, _, _, ann = router.route(prompt)
        self.assertNotIn("Dispatching now", ann,
            "old wording 'Dispatching now...' is misleading — hook only injects text")

    def test_uses_invoke_now(self) -> None:
        prompt = "refactor the auth module"
        _, _, _, ann = router.route(prompt)
        self.assertIn("Invoke now:", ann,
            "announcement should imperatively tell the model to invoke")

    def test_announcement_has_skill_router_prefix(self) -> None:
        prompt = "refactor the auth module"
        _, _, _, ann = router.route(prompt)
        self.assertTrue(ann.startswith("[skill-router]"),
            "announcement must start with [skill-router] for grep-based audit")

    def test_announcement_has_arrow_marker(self) -> None:
        prompt = "refactor the auth module"
        _, _, _, ann = router.route(prompt)
        self.assertIn("▶", ann, "▶ marker tells the model what to invoke")

    def test_skip_emits_nothing(self) -> None:
        _, _, _, ann = router.route("what do you think about this design?")
        self.assertEqual(ann, "")


class TestIronRule(unittest.TestCase):
    """The IRON RULE block must appear on every announcement and the pending
    state file must be written so the PreToolUse / Stop hooks can enforce it.

    Tests redirect router.PENDING to a tempdir so they cannot leak state into
    the user's real ~/.claude/skill_router_pending.json (which would trap the
    live session in the iron rule).
    """

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile
        cls._tmpdir = tempfile.mkdtemp(prefix="router-test-")
        cls._real_pending = router.PENDING
        router.PENDING = Path(cls._tmpdir) / "skill_router_pending.json"

    @classmethod
    def tearDownClass(cls) -> None:
        import shutil
        router.PENDING = cls._real_pending
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def test_iron_rule_block_in_announcement(self) -> None:
        _, _, _, ann = router.route("refactor the auth module")
        self.assertIn("IRON RULE", ann)
        self.assertIn('Skill(skill="refactor")', ann)
        self.assertIn("[no-router]", ann, "escape hatch must be documented in the announcement")

    def test_iron_rule_names_first_skill_in_chain(self) -> None:
        # Multi-step OPERATE chain: verification → deploy. IRON RULE points at first.
        _, chain, _, ann = router.route("deploy the current branch to production")
        self.assertEqual(chain[0].skill, "superpowers:verification-before-completion")
        self.assertIn('Skill(skill="superpowers:verification-before-completion")', ann)

    def test_no_iron_rule_when_skip(self) -> None:
        _, _, _, ann = router.route("what do you think about this?")
        self.assertEqual(ann, "")

    def test_pending_state_written_on_announcement(self) -> None:
        # Run via main() entrypoint (the same path the hook uses).
        import io
        from contextlib import redirect_stdout
        os_env_backup = os.environ.get("CLAUDE_USER_INPUT")
        os.environ["CLAUDE_USER_INPUT"] = "refactor the auth module"
        try:
            with redirect_stdout(io.StringIO()):
                rc = router.main()
            self.assertEqual(rc, 0)
            self.assertTrue(router.PENDING.is_file())
            data = json.loads(router.PENDING.read_text())
            self.assertEqual(data.get("primary"), "refactor")
            self.assertEqual(data.get("remaining"), ["refactor"])
        finally:
            if os_env_backup is None:
                os.environ.pop("CLAUDE_USER_INPUT", None)
            else:
                os.environ["CLAUDE_USER_INPUT"] = os_env_backup

    def test_pending_state_cleared_when_skip(self) -> None:
        import io
        from contextlib import redirect_stdout
        # First seed a pending state from a real announcement
        router.PENDING.parent.mkdir(parents=True, exist_ok=True)
        router.PENDING.write_text(json.dumps({"primary": "refactor", "remaining": ["refactor"], "all": ["refactor"]}))
        # Now a SKIP prompt should wipe it
        os.environ["CLAUDE_USER_INPUT"] = "what do you think about this?"
        try:
            with redirect_stdout(io.StringIO()):
                router.main()
            data = json.loads(router.PENDING.read_text())
            self.assertEqual(data, {}, "SKIP turns must clear stale pending state")
        finally:
            os.environ.pop("CLAUDE_USER_INPUT", None)

    def test_escape_hatch_disables_routing_and_pending(self) -> None:
        import io
        from contextlib import redirect_stdout
        os.environ["CLAUDE_USER_INPUT"] = "[no-router] refactor the auth module"
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                router.main()
            self.assertEqual(buf.getvalue(), "", "escape hatch must produce no announcement")
            # Missing file or `{}` both satisfy "no pending state"
            if router.PENDING.is_file():
                data = json.loads(router.PENDING.read_text() or "{}")
                self.assertEqual(data.get("remaining", []), [],
                    "escape hatch must leave pending empty")
        finally:
            os.environ.pop("CLAUDE_USER_INPUT", None)


class TestEdgeCases(unittest.TestCase):

    def test_empty_prompt_does_not_crash(self) -> None:
        path, chain, _, ann = router.route("")
        self.assertEqual(path, "SKIP")
        self.assertEqual(chain, [])
        self.assertEqual(ann, "")

    def test_clean_with_intervening_words(self) -> None:
        # 'clean it up', 'clean the auth service up' should still match OPERATE
        for prompt in ["clean it up", "clean this up", "clean the auth service up"]:
            with self.subTest(prompt=prompt):
                path, _, _, _ = router.route(prompt)
                self.assertEqual(path, "OPERATE", f"OPERATE should match: {prompt!r}")

    def test_multi_domain_build_uses_writing_plans(self) -> None:
        prompt = "build a new dashboard page that writes to the database and sends emails"
        path, chain, domains, ann = router.route(prompt)
        self.assertEqual(path, "BUILD")
        self.assertGreaterEqual(len(domains), 2)
        self.assertEqual(chain[0].skill, "superpowers:writing-plans")
        self.assertIn("touches", ann)

    def test_production_incident_uses_opus_ultrathink(self) -> None:
        prompt = "Production is down right now, users are losing data"
        _, chain, _, ann = router.route(prompt)
        self.assertEqual(chain[0].model, "opus")
        self.assertEqual(chain[0].thinking, "ultrathink")
        self.assertIn("ultrathink", ann)

    def test_stripe_catalog_upgrade(self) -> None:
        prompt = "I need to integrate Stripe payments into checkout"
        _, chain, _, _ = router.route(prompt)
        self.assertEqual(chain[0].skill, "stripe")


if __name__ == "__main__":
    unittest.main()
