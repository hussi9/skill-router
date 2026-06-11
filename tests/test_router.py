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
import types
import unittest
from pathlib import Path
from typing import Optional

# Make scripts/ importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

# Plain route() tests must stay deterministic even when a local embedder
# daemon is running for dogfood.
os.environ.setdefault("SKILL_ROUTER_NO_EMBED", "1")

import router  # type: ignore[import-not-found]
import embedder_daemon  # type: ignore[import-not-found]

# Tests exercise routing logic, not strike-based soft-mode state. Clean the
# strikes file at module load so a refactor route (or anything seeded from
# the user's real 30-day history) doesn't get silently dropped during tests.
router.STRIKES.write_text("{}\n")


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
         "BROKEN", "systematic-debugging"),
    (5,  "The deploy failed — Vercel build error in CI pipeline",
         "BROKEN", "systematic-debugging"),
    (6,  "CRITICAL: database corrupted in production, users losing data right now",
         "BROKEN", "systematic-debugging"),
    (7,  "Add a dark mode toggle to the settings page",
         "BUILD", "frontend-design"),
    (8,  "Build a new REST API endpoint for user analytics",
         "BUILD", "feature-dev"),
    (9,  "I need to integrate Stripe payments into checkout",
         "BUILD", "connect-apps"),
    (10, "Create a new database schema for the notifications system",
         "BUILD", "writing-plans"),
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
        cls._real_strikes = router.STRIKES
        router.PENDING = Path(cls._tmpdir) / "skill_router_pending.json"
        # Also redirect the strikes file — clear_pending() bumps strikes for
        # unsatisfied announcements, and we don't want test runs to poison
        # the user's real strike map (would demote skills they actually use).
        router.STRIKES = Path(cls._tmpdir) / "skill_router_strikes.json"

    @classmethod
    def tearDownClass(cls) -> None:
        import shutil
        router.PENDING = cls._real_pending
        router.STRIKES = cls._real_strikes
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self) -> None:
        # Reset both state files per-test so we exercise routing logic without
        # cross-test strike accumulation. Several tests in this class pre-seed
        # PENDING with refactor to test clear_pending() / hook-mode mechanics —
        # without this reset, the cumulative bumps push refactor past
        # STRIKE_THRESHOLD by mid-suite and the route silently drops it.
        router.PENDING.write_text("{}\n")
        router.STRIKES.write_text("{}\n")

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
        hook_env_backup = os.environ.get("SKILL_ROUTER_HOOK_MODE")
        os.environ["CLAUDE_USER_INPUT"] = "refactor the auth module"
        os.environ["SKILL_ROUTER_HOOK_MODE"] = "1"
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
            if hook_env_backup is None:
                os.environ.pop("SKILL_ROUTER_HOOK_MODE", None)
            else:
                os.environ["SKILL_ROUTER_HOOK_MODE"] = hook_env_backup

    def test_pending_state_cleared_when_skip(self) -> None:
        import io
        from contextlib import redirect_stdout
        hook_env_backup = os.environ.get("SKILL_ROUTER_HOOK_MODE")
        # First seed a pending state from a real announcement
        router.PENDING.parent.mkdir(parents=True, exist_ok=True)
        router.PENDING.write_text(json.dumps({"primary": "refactor", "remaining": ["refactor"], "all": ["refactor"]}))
        # Now a SKIP prompt should wipe it
        os.environ["CLAUDE_USER_INPUT"] = "what do you think about this?"
        os.environ["SKILL_ROUTER_HOOK_MODE"] = "1"
        try:
            with redirect_stdout(io.StringIO()):
                router.main()
            data = json.loads(router.PENDING.read_text())
            self.assertEqual(data, {}, "SKIP turns must clear stale pending state")
        finally:
            os.environ.pop("CLAUDE_USER_INPUT", None)
            if hook_env_backup is None:
                os.environ.pop("SKILL_ROUTER_HOOK_MODE", None)
            else:
                os.environ["SKILL_ROUTER_HOOK_MODE"] = hook_env_backup

    def test_cli_mode_does_not_mutate_pending_state(self) -> None:
        import io
        from contextlib import redirect_stdout
        os.environ["CLAUDE_USER_INPUT"] = "what do you think about this?"
        os.environ.pop("SKILL_ROUTER_HOOK_MODE", None)
        router.PENDING.parent.mkdir(parents=True, exist_ok=True)
        router.PENDING.write_text(json.dumps({"primary": "refactor", "remaining": ["refactor"], "all": ["refactor"]}))
        try:
            with redirect_stdout(io.StringIO()):
                router.main()
            data = json.loads(router.PENDING.read_text())
            self.assertEqual(data.get("remaining"), ["refactor"])
        finally:
            os.environ.pop("CLAUDE_USER_INPUT", None)

    def test_escape_hatch_disables_routing_and_pending(self) -> None:
        import io
        from contextlib import redirect_stdout
        hook_env_backup = os.environ.get("SKILL_ROUTER_HOOK_MODE")
        os.environ["CLAUDE_USER_INPUT"] = "[no-router] refactor the auth module"
        os.environ["SKILL_ROUTER_HOOK_MODE"] = "1"
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
            if hook_env_backup is None:
                os.environ.pop("SKILL_ROUTER_HOOK_MODE", None)
            else:
                os.environ["SKILL_ROUTER_HOOK_MODE"] = hook_env_backup


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
        self.assertEqual(chain[0].skill, "connect-apps")


class TestEmbeddingFallback(unittest.TestCase):
    """Embedding rescue is opt-in for tests and logs both hits and misses."""

    def setUp(self) -> None:
        import tempfile
        self._tmpdir = tempfile.mkdtemp(prefix="router-embed-test-")
        self._real_log = router.LOG
        self._real_embedder = sys.modules.get("embedder_client")
        self._real_no_embed = os.environ.get("SKILL_ROUTER_NO_EMBED")
        self._real_embed = os.environ.get("SKILL_ROUTER_EMBED")
        self._real_prompt_logging = os.environ.get("SKILL_ROUTER_LOG_EMBED_PROMPTS")
        router.LOG = Path(self._tmpdir) / "skill_router_log.jsonl"
        os.environ.pop("SKILL_ROUTER_NO_EMBED", None)
        os.environ["SKILL_ROUTER_EMBED"] = "1"
        os.environ.pop("SKILL_ROUTER_LOG_EMBED_PROMPTS", None)

    def tearDown(self) -> None:
        import shutil
        router.LOG = self._real_log
        if self._real_embedder is None:
            sys.modules.pop("embedder_client", None)
        else:
            sys.modules["embedder_client"] = self._real_embedder
        if self._real_no_embed is None:
            os.environ.pop("SKILL_ROUTER_NO_EMBED", None)
        else:
            os.environ["SKILL_ROUTER_NO_EMBED"] = self._real_no_embed
        if self._real_embed is None:
            os.environ.pop("SKILL_ROUTER_EMBED", None)
        else:
            os.environ["SKILL_ROUTER_EMBED"] = self._real_embed
        if self._real_prompt_logging is None:
            os.environ.pop("SKILL_ROUTER_LOG_EMBED_PROMPTS", None)
        else:
            os.environ["SKILL_ROUTER_LOG_EMBED_PROMPTS"] = self._real_prompt_logging
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _install_fake_embedder(self, result: dict) -> None:
        sys.modules["embedder_client"] = types.SimpleNamespace(classify=lambda _prompt: result)

    def test_low_confidence_skip_is_logged_without_prompt_text(self) -> None:
        self._install_fake_embedder({
            "path": "SKIP",
            "reason": "low_confidence",
            "winner_count": 3,
            "avg_sim": 0.68,
            "neighbors": [{"prompt": "fix checkout", "path": "BROKEN", "skill": "systematic-debugging", "sim": 0.7}],
            "_ms": 4.2,
        })
        path, chain, _, ann = router.route("the checkout is borked")
        self.assertEqual(path, "SKIP")
        self.assertEqual(chain, [])
        self.assertEqual(ann, "")
        rows = [json.loads(line) for line in router.LOG.read_text().splitlines()]
        self.assertEqual(rows[0]["type"], "embedding-skip")
        self.assertEqual(rows[0]["reason"], "low_confidence")
        self.assertIn("prompt_hash", rows[0])
        self.assertNotIn("prompt", rows[0])

    def test_confident_fallback_routes_and_logs(self) -> None:
        self._install_fake_embedder({
            "path": "BROKEN",
            "skill": "superpowers:systematic-debugging",
            "confidence": 0.81,
            "agreement": "4/5",
            "_ms": 5.1,
        })
        path, chain, _, ann = router.route("the checkout is borked")
        self.assertEqual(path, "BROKEN")
        self.assertEqual(chain[0].skill, "superpowers:systematic-debugging")
        self.assertIn("systematic-debugging", ann)
        rows = [json.loads(line) for line in router.LOG.read_text().splitlines()]
        self.assertEqual(rows[0]["type"], "embedding-route")


class TestEmbedderScoring(unittest.TestCase):
    """k-NN scoring should rescue strong paraphrases without stale labels."""

    def _entry(self, prompt: str, path: str, skill: str, vec: tuple[float, float]) -> embedder_daemon.CorpusEntry:
        import numpy as np

        arr = np.array(vec, dtype=float)
        arr = arr / (np.linalg.norm(arr) + 1e-12)
        return embedder_daemon.CorpusEntry(prompt, path, skill, arr)

    def test_uses_winner_path_average_not_all_neighbor_average(self) -> None:
        import numpy as np

        corpus = [
            self._entry("broken checkout", "BROKEN", "superpowers:systematic-debugging", (1.00, 0.00)),
            self._entry("checkout error", "BROKEN", "superpowers:systematic-debugging", (0.99, 0.01)),
            self._entry("cart bug", "BROKEN", "superpowers:systematic-debugging", (0.98, 0.02)),
            self._entry("stripe checkout", "BUILD", "connect-apps", (0.20, 0.98)),
            self._entry("add coverage", "OPERATE", "superpowers:test-driven-development", (0.10, 0.99)),
        ]
        result = embedder_daemon.classify(np.array((1.0, 0.0), dtype=float), corpus)
        self.assertEqual(result["path"], "BROKEN")
        self.assertEqual(result["skill"], "superpowers:systematic-debugging")
        self.assertGreaterEqual(result["confidence"], embedder_daemon.CONFIDENCE_THRESHOLD)
        self.assertGreaterEqual(result["margin"], embedder_daemon.MARGIN_THRESHOLD)

    def test_rejects_weak_margin_between_paths(self) -> None:
        import numpy as np

        corpus = [
            self._entry("build thing", "BUILD", "connect-apps", (1.00, 0.00)),
            self._entry("new endpoint", "BUILD", "feature-dev:feature-dev", (0.99, 0.01)),
            self._entry("new dashboard", "BUILD", "frontend-design:frontend-design", (0.98, 0.02)),
            self._entry("fix thing", "BROKEN", "superpowers:systematic-debugging", (1.00, 0.00)),
            self._entry("failing thing", "BROKEN", "superpowers:systematic-debugging", (0.99, 0.01)),
        ]
        result = embedder_daemon.classify(np.array((1.0, 0.0), dtype=float), corpus)
        self.assertEqual(result["path"], "SKIP")
        self.assertLess(result["margin"], embedder_daemon.MARGIN_THRESHOLD)


class TestGhostSkillGuard(unittest.TestCase):
    """Verify the router refuses to announce skills that aren't installed.

    Each test patches `_skill_catalog` to a controlled value so we can
    simulate uninstalled skills without touching the real ~/.claude tree.
    Cache is cleared via `cache_clear()` between tests to keep them isolated.
    """

    def setUp(self) -> None:
        # Snapshot the real cached loader so we can restore it after each test.
        self._real_loader = router._skill_catalog
        try:
            self._real_loader.cache_clear()
        except AttributeError:
            pass

    def tearDown(self) -> None:
        router._skill_catalog = self._real_loader  # type: ignore[assignment]
        try:
            router._skill_catalog.cache_clear()
        except AttributeError:
            pass

    def _patch_catalog(self, catalog: Optional[set[str]]) -> None:
        # Replace the cached function with a stub returning our fixture.
        router._skill_catalog = lambda: catalog  # type: ignore[assignment]

    def test_ghost_skill_downgrades_to_skip(self) -> None:
        # Catalog deliberately omits 'refactor' — pretend it's not installed.
        self._patch_catalog({"system-design", "test-runner"})
        path, chain, _, ann = router.route("refactor the auth module")
        self.assertEqual(path, "SKIP",
            "ghost skill must downgrade chain to SKIP, not announce")
        self.assertEqual(chain, [])
        self.assertEqual(ann, "")

    def test_valid_skill_announces_normally(self) -> None:
        # Catalog includes 'refactor' — should announce as usual.
        self._patch_catalog({"refactor"})
        path, chain, _, ann = router.route("refactor the auth module")
        self.assertEqual(path, "OPERATE")
        self.assertEqual(chain[0].skill, "refactor")
        self.assertIn("▶ refactor", ann)

    def test_fails_open_when_catalog_unloadable(self) -> None:
        # None signals 'catalog could not be enumerated' — must NOT suppress.
        self._patch_catalog(None)
        path, chain, _, ann = router.route("refactor the auth module")
        self.assertEqual(path, "OPERATE",
            "fail-open: unloadable catalog must not silence valid routes")
        self.assertEqual(chain[0].skill, "refactor")
        self.assertIn("[skill-router]", ann)

    def test_ghost_in_multi_step_chain_drops_whole_chain(self) -> None:
        # Deploy chain is verification-before-completion → vercel:deploy.
        # If the second step is a ghost, the whole chain is suppressed.
        self._patch_catalog({"superpowers:verification-before-completion"})
        path, chain, _, ann = router.route("deploy the current branch to production")
        self.assertEqual(path, "SKIP")
        self.assertEqual(chain, [])
        self.assertEqual(ann, "")


class TestOnlineCatalogSuggestion(unittest.TestCase):
    """Soft-suggestion path: when regex + embedding both SKIP, surface an
    uninstalled-but-fitting online skill instead of staying silent.

    Tests stub the catalogs via a controlled fixture so they're hermetic and
    don't depend on the user's real ~/.claude/skill_router_online_catalog.json
    state drifting over time.
    """

    def setUp(self) -> None:
        # Snapshot real loader + cache so we can restore in tearDown.
        self._real_index = router._online_skill_index
        try:
            self._real_index.cache_clear()
        except AttributeError:
            pass

    def tearDown(self) -> None:
        router._online_skill_index = self._real_index  # type: ignore[assignment]
        try:
            router._online_skill_index.cache_clear()
        except AttributeError:
            pass

    def _install_fixture(self, entries: list[dict]) -> None:
        """Patch the cached index loader with a fixture list. Mirrors the real
        loader's behavior: drops entries marked `installed: True` (locally
        present, never re-suggested) and pre-tokenizes the rest so token-
        overlap scoring matches what production would see."""
        pre_tokenized: list[tuple[dict, frozenset[str]]] = []
        for entry in entries:
            if entry.get("installed"):
                continue  # matches the real loader's local-shadow filter
            tags = entry.get("tags") or []
            text = " ".join([
                entry.get("name", ""),
                entry.get("description") or "",
                " ".join(str(t) for t in tags if t),
            ])
            tokens = router._tokenize(text)
            if not tokens:
                continue
            pre_tokenized.append((entry, frozenset(tokens)))
        router._online_skill_index = lambda: pre_tokenized  # type: ignore[assignment]

    def test_strong_overlap_returns_suggestion(self) -> None:
        # Skill description packed with the prompt's key tokens.
        self._install_fixture([
            {
                "name": "active-directory-attacks",
                "description": "Penetration testing techniques against active directory environments and kerberos abuse.",
                "source": "online:antigravity",
                "installed": False,
                "install_command": "npx antigravity-awesome-skills install active-directory-attacks",
                "tags": ["security", "pentest", "directory"],
            },
        ])
        sug = router.suggest_online_skill(
            "I want to use active directory attacks for penetration testing"
        )
        self.assertIsNotNone(sug, "strong-overlap prompt must return a suggestion")
        assert sug is not None  # for type-checker
        self.assertEqual(sug["name"], "active-directory-attacks")
        self.assertGreaterEqual(sug["_router_confidence"], 0.5)
        self.assertGreaterEqual(sug["_router_overlap"], 3)
        self.assertEqual(sug["source"], "online:antigravity")
        self.assertIn("install", sug["install_command"])

    def test_local_installed_skill_match_returns_none(self) -> None:
        # Even a perfect token match must be filtered out if the skill is
        # already installed locally — we never double-route to an online
        # entry the user already has on disk. We simulate this by setting
        # `installed: True` on the fixture entry; the loader excludes it.
        self._install_fixture([
            {
                "name": "refactor",
                "description": "Code refactoring and cleanup workflows.",
                "source": "online:antigravity",
                "installed": True,  # locally installed → filtered
                "install_command": "n/a",
            },
        ])
        sug = router.suggest_online_skill(
            "I want code refactoring cleanup workflows for my project"
        )
        self.assertIsNone(sug,
            "skills already installed locally must never surface as suggestions")

    def test_no_overlap_returns_none(self) -> None:
        self._install_fixture([
            {
                "name": "kubernetes-operator",
                "description": "Build and deploy custom kubernetes operators.",
                "source": "online:antigravity",
                "installed": False,
                "install_command": "npx antigravity-awesome-skills install kubernetes-operator",
            },
        ])
        sug = router.suggest_online_skill(
            "what does this javascript function actually return"
        )
        self.assertIsNone(sug, "zero-overlap prompt must not return a suggestion")

    def test_below_min_overlap_returns_none(self) -> None:
        # Only one token in common — below ONLINE_SUGGEST_MIN_OVERLAP=3.
        self._install_fixture([
            {
                "name": "kubernetes-operator",
                "description": "Build and deploy custom kubernetes operators with helm charts.",
                "source": "online:antigravity",
                "installed": False,
                "install_command": "npx install kubernetes-operator",
            },
        ])
        sug = router.suggest_online_skill(
            "I am exploring kubernetes basics for the first time"
        )
        # 'kubernetes' overlaps but the rest of the prompt is mostly
        # stopwords / short tokens. Should not pass the 3-token gate.
        self.assertIsNone(sug)

    def test_empty_prompt_returns_none(self) -> None:
        self.assertIsNone(router.suggest_online_skill(""))

    def test_render_online_suggestion_format(self) -> None:
        entry = {
            "name": "active-directory-attacks",
            "source": "online:antigravity",
            "install_command": "npx antigravity-awesome-skills install active-directory-attacks",
        }
        out = router.render_online_suggestion(entry)
        self.assertIn("[skill-router] No installed skill matches", out)
        self.assertIn("`active-directory-attacks`", out)
        self.assertIn("online:antigravity", out)
        self.assertIn("Install: npx antigravity-awesome-skills install active-directory-attacks", out)
        self.assertIn("soft suggestion only, no enforcement", out)
        # Critical: must NOT contain IRON RULE text (which would be a lie —
        # we can't enforce a skill that isn't installed).
        self.assertNotIn("IRON RULE", out)
        self.assertNotIn("▶", out)

    def test_main_fires_suggestion_when_route_skips(self) -> None:
        # End-to-end: stdin prompt with no regex/embedding match, but with
        # strong overlap to an online skill → main() should print the
        # suggestion AND must NOT write pending state.
        import io, tempfile
        from contextlib import redirect_stdout

        self._install_fixture([
            {
                "name": "active-directory-attacks",
                "description": "Penetration testing against active directory environments with kerberos abuse.",
                "source": "online:antigravity",
                "installed": False,
                "install_command": "npx antigravity-awesome-skills install active-directory-attacks",
                "tags": ["security", "pentest", "directory"],
            },
        ])

        # Redirect PENDING to a temp file so we can assert no IRON state.
        tmpdir = tempfile.mkdtemp(prefix="router-online-test-")
        real_pending = router.PENDING
        real_strikes = router.STRIKES
        router.PENDING = Path(tmpdir) / "pending.json"
        router.STRIKES = Path(tmpdir) / "strikes.json"
        router.PENDING.write_text("{}\n")
        router.STRIKES.write_text("{}\n")

        os.environ["CLAUDE_USER_INPUT"] = (
            "I want to use active directory attacks for penetration testing"
        )
        os.environ["SKILL_ROUTER_HOOK_MODE"] = "1"
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = router.main()
            out = buf.getvalue()
            self.assertEqual(rc, 0)
            self.assertIn("No installed skill matches", out)
            self.assertIn("active-directory-attacks", out)
            self.assertIn("soft suggestion only", out)
            self.assertNotIn("IRON RULE", out)
            # Pending state must remain empty — soft suggestion never enforces.
            data = json.loads(router.PENDING.read_text() or "{}")
            self.assertEqual(data.get("remaining", []), [],
                "online soft suggestion must NOT write pending state")
        finally:
            os.environ.pop("CLAUDE_USER_INPUT", None)
            os.environ.pop("SKILL_ROUTER_HOOK_MODE", None)
            import shutil
            router.PENDING = real_pending
            router.STRIKES = real_strikes
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_real_catalog_loads_under_budget(self) -> None:
        # Use the actual on-disk catalogs (no fixture) to verify the load
        # path + perf budget. Restore loader so cache_clear hits the real one.
        router._online_skill_index = self._real_index  # type: ignore[assignment]
        try:
            self._real_index.cache_clear()
        except AttributeError:
            pass
        import time as _time
        # Cold load
        t0 = _time.perf_counter()
        index = router._online_skill_index()
        cold_ms = (_time.perf_counter() - t0) * 1000
        # Warm call
        t0 = _time.perf_counter()
        router.suggest_online_skill("address all github pr comments on my pull request")
        warm_ms = (_time.perf_counter() - t0) * 1000
        # Cold load is allowed up to 50ms; warm call must be well under it.
        self.assertLess(cold_ms, 50.0,
            f"cold-load + tokenize must stay under 50ms (got {cold_ms:.1f}ms)")
        self.assertLess(warm_ms, 50.0,
            f"warm suggest_online_skill must stay under 50ms (got {warm_ms:.1f}ms)")
        # And the index should have meaningful content.
        self.assertIsNotNone(index)
        assert index is not None
        self.assertGreater(len(index), 100,
            "expected at least 100 novel online skills in catalog")


class TestExplicitInvocation(unittest.TestCase):
    """Explicit slash-command invocations must make the router stand down — the
    user already chose the skill, so it must never be reclassified into a
    different one. This is the 'router fights the user' bug ( /gstack -> a
    forced sync-gbrain IRON RULE )."""

    def test_bare_command_matches(self) -> None:
        for p in ["/gstack", "/ship", "/sync-gbrain", "/qa"]:
            self.assertTrue(router.explicit_invocation(p), f"{p!r} is an explicit invocation")

    def test_command_with_args_matches(self) -> None:
        self.assertTrue(router.explicit_invocation("/ship prod"))
        self.assertTrue(router.explicit_invocation("  /qa exhaustive  "))

    def test_namespaced_command_matches(self) -> None:
        self.assertTrue(router.explicit_invocation("/feature-dev:feature-dev"))
        self.assertTrue(router.explicit_invocation("/code-review:code-review now"))

    def test_filesystem_path_does_not_match(self) -> None:
        # A leading absolute path is NOT a command — must route normally.
        self.assertFalse(router.explicit_invocation("/Users/me/x.py please review"))
        self.assertFalse(router.explicit_invocation("/etc/hosts"))

    def test_normal_prose_does_not_match(self) -> None:
        for p in ["fix the login bug", "add a settings page", "", "/", "//"]:
            self.assertFalse(router.explicit_invocation(p), f"{p!r} is not an explicit invocation")

    def test_route_does_not_hijack_slash_command(self) -> None:
        # Regression: with the embedder fallback ON, /gstack used to be rescued
        # into a BUILD->sync-gbrain IRON rule. main() must now stand down with
        # zero output. Run as a subprocess so we exercise the real entry point
        # without polluting live state (no HOOK_MODE => no pending write).
        import subprocess
        scripts = str(Path(__file__).resolve().parents[1] / "scripts")
        env = dict(os.environ, SKILL_ROUTER_EMBED="1")
        env.pop("SKILL_ROUTER_NO_EMBED", None)  # force embedder path on
        for cmd in ["/gstack", "/ship prod", "/feature-dev:feature-dev"]:
            out = subprocess.run(
                [sys.executable, f"{scripts}/router.py"],
                input=cmd, capture_output=True, text=True, env=env, timeout=30,
            )
            self.assertEqual(out.stdout.strip(), "",
                f"explicit invocation {cmd!r} must produce no announcement (got: {out.stdout!r})")


class TestReasonedOverride(unittest.TestCase):
    """The ask+learn loop: a reasoned override clears the IRON rule, logs why,
    and bumps a per-skill tally that defers the skill on similar future prompts."""

    @classmethod
    def setUpClass(cls) -> None:
        import tempfile
        cls._tmpdir = tempfile.mkdtemp()
        cls._real = (router.PENDING, router.OVERRIDES_LOG, router.OVERRIDES_COUNT, router.STRIKES)
        d = Path(cls._tmpdir)
        router.PENDING = d / "pending.json"
        router.OVERRIDES_LOG = d / "overrides.jsonl"
        router.OVERRIDES_COUNT = d / "overrides_count.json"
        router.STRIKES = d / "strikes.json"

    @classmethod
    def tearDownClass(cls) -> None:
        import shutil
        (router.PENDING, router.OVERRIDES_LOG, router.OVERRIDES_COUNT, router.STRIKES) = cls._real
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def setUp(self) -> None:
        router.PENDING.write_text("{}\n")
        router.OVERRIDES_COUNT.write_text("{}\n")
        if router.OVERRIDES_LOG.is_file():
            router.OVERRIDES_LOG.unlink()

    def _seed_pending(self, skill: str) -> None:
        router.PENDING.write_text(json.dumps(
            {"primary": skill, "remaining": [skill], "all": [skill]}) + "\n")

    def test_override_clears_pending_and_logs_reason(self) -> None:
        self._seed_pending("sync-gbrain")
        result = router.record_override("explicit /gstack — user already chose the skill",
                                        prompt="/gstack")
        self.assertEqual(result["skill"], "sync-gbrain")
        # Pending cleared so the Stop/PreToolUse hooks pass through.
        self.assertEqual(json.loads(router.PENDING.read_text()), {})
        # Audit line written with the reason.
        lines = router.OVERRIDES_LOG.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry["skill"], "sync-gbrain")
        self.assertIn("user already chose", entry["reason"])
        self.assertIn("prompt_hash", entry)  # prompt hashed, not stored raw

    def test_override_count_defers_at_threshold(self) -> None:
        self.assertFalse(router.is_overridden("connect-apps"))
        for _ in range(router.OVERRIDE_THRESHOLD):
            self._seed_pending("connect-apps")
            router.record_override("wrong integration target")
        self.assertTrue(router.is_overridden("connect-apps"))
        self.assertTrue(router.is_deferred("connect-apps"))
        # And _drop_soft removes it from a chain.
        chain = [router.Step("connect-apps", "integration-specialist")]
        self.assertEqual(router._drop_soft(chain), [])

    def test_reset_override_count_rearms_skill(self) -> None:
        for _ in range(router.OVERRIDE_THRESHOLD):
            self._seed_pending("security")
            router.record_override("not actually an auth task")
        self.assertTrue(router.is_overridden("security"))
        router.reset_override_count("security")
        self.assertFalse(router.is_overridden("security"))

    def test_override_with_no_pending_is_safe(self) -> None:
        router.PENDING.write_text("{}\n")
        result = router.record_override("nothing pending")
        self.assertEqual(result["skill"], "")
        # Reason still logged for the audit trail.
        self.assertTrue(router.OVERRIDES_LOG.is_file())


class TestCollaborativeAnnouncement(unittest.TestCase):
    """The IRON RULE block must offer the model a reasoned-override path, not
    only the user-typed [no-router] escape."""

    def test_announcement_documents_override_script(self) -> None:
        ann = router.render("BROKEN",
                            [router.Step("systematic-debugging", "general-purpose")], [])
        self.assertIn("router_override.py", ann,
            "model must be told it can overrule with a reason")
        self.assertIn("[no-router]", ann, "user escape must still be documented")


if __name__ == "__main__":
    unittest.main()
