#!/usr/bin/env python3
"""
Tests for the specialist matcher and the catalog it ranks over.

Two properties matter, and they pull against each other:

  recall     a prompt that clearly names a domain finds that domain's skill,
             including the ~360 specialists no routing table will ever list
  precision  a prompt that names no domain returns nothing

Precision is the one worth protecting. A wrong specialist costs more than a
missing one, because the user stops reading the announcement — and an
announcement nobody reads is the state this whole system was already in.

Run: python3 -m pytest tests/test_catalog_match.py -q
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_catalog  # type: ignore[import-not-found]
import catalog_match  # type: ignore[import-not-found]


def _fixture_catalog(entries: list[dict]) -> str:
    """Write a throwaway catalog so scoring tests don't depend on what happens
    to be installed today."""
    tmp = Path(tempfile.mkdtemp(prefix="catalog-")) / "catalog.json"
    tmp.write_text(json.dumps({
        "version": 2, "total": len(entries), "entries": entries, "agents": [],
    }))
    return str(tmp)


class TestTokenize(unittest.TestCase):

    def test_splits_hyphenated_names(self) -> None:
        tokens = catalog_match.tokenize("test-driven-development")
        self.assertIn("driven", tokens)
        self.assertIn("development", tokens)

    def test_drops_stopwords_and_short_tokens(self) -> None:
        """Generic action verbs are noise. They appear in every prompt and in
        many skill names, so scoring them lets `ios-fix` win 'fix the test'."""
        tokens = catalog_match.tokenize("please add the new thing to my code")
        self.assertEqual(tokens, [], f"expected all noise, got {tokens}")

    def test_keeps_domain_words(self) -> None:
        tokens = catalog_match.tokenize("run an SEO audit on checkout")
        self.assertIn("seo", tokens)
        self.assertIn("audit", tokens)
        self.assertIn("checkout", tokens)


class TestScoringRules(unittest.TestCase):
    """The scoring behaviors that were tuned against real misroutes."""

    def setUp(self) -> None:
        catalog_match.load_index.cache_clear()
        self.path = _fixture_catalog([
            {"name": "ios-fix", "description": "Fix iOS build and runtime problems",
             "body": "", "type": "skill", "invokable": True},
            {"name": "test-driven-development", "description":
             "Use when implementing a feature or bugfix, before writing code",
             "body": "", "type": "skill", "invokable": True},
            {"name": "dataviz", "description":
             "Use before writing any chart, graph, plot or dashboard visualization",
             "body": "", "type": "skill", "invokable": True},
            {"name": "scrollbook-deploy", "description":
             "Scrollbook iOS and Android deployment, TestFlight, App Store Connect",
             "body": "", "type": "skill", "invokable": True},
            {"name": "not-installed-thing", "description":
             "TestFlight deployment helper for scrollbook",
             "body": "", "type": "available", "invokable": False},
        ])

    def tearDown(self) -> None:
        catalog_match.load_index.cache_clear()

    def test_unclaimed_distinctive_name_term_is_penalized(self) -> None:
        """`ios-fix` must not win a prompt that never mentions iOS.

        Both share the word 'fix'. The difference is that `ios-fix` is *about*
        iOS and the prompt is not, so its defining term went unclaimed.
        """
        best = catalog_match.best("fix the failing test", catalog_path=self.path)
        self.assertNotEqual(getattr(best, "name", None), "ios-fix")

    def test_description_only_match_is_not_penalized(self) -> None:
        """A single-word name that never gets claimed is an ordinary match.

        `dataviz` matched on 'chart' from its description and made no claim on
        its own name. Charging that the same as a half-claimed compound name
        made every single-word skill — including most built-ins — unrankable.
        """
        results = catalog_match.rank("draw a chart of the results",
                                     catalog_path=self.path)
        names = [m.name for m in results]
        self.assertIn("dataviz", names)
        self.assertGreater(results[0].score, 0)

    def test_never_returns_uninstalled_skills_by_default(self) -> None:
        for m in catalog_match.rank("scrollbook testflight deployment",
                                    catalog_path=self.path):
            self.assertTrue(m.invokable,
                f"{m.name} cannot be loaded by the Skill tool and must not rank")

    def test_uninstalled_skills_surface_only_as_install_hints(self) -> None:
        results = catalog_match.rank("scrollbook testflight deployment",
                                     invokable_only=False, catalog_path=self.path)
        self.assertIn("not-installed-thing", [m.name for m in results])

    def test_missing_catalog_degrades_to_silence(self) -> None:
        self.assertEqual(catalog_match.rank("anything", catalog_path="/nope.json"), [])
        self.assertIsNone(catalog_match.best("anything", catalog_path="/nope.json"))


class TestFamilyCollapse(unittest.TestCase):

    def test_siblings_share_a_family(self) -> None:
        self.assertTrue(catalog_match._same_family("seo-technical", "cory-seo-audit"))
        self.assertTrue(catalog_match._same_family("ads-meta", "ads-audit"))

    def test_author_prefix_alone_is_not_a_family(self) -> None:
        self.assertFalse(catalog_match._same_family("cory-image", "cory-video"))

    def test_unrelated_skills_are_not_a_family(self) -> None:
        self.assertFalse(catalog_match._same_family("market-launch", "debugging-capacitor"))


class TestLiveCatalog(unittest.TestCase):
    """Properties of the catalog actually on this machine."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.index = catalog_match.load_index()
        if cls.index is None:
            raise unittest.SkipTest("no catalog built yet — run build_catalog.py")

    def test_every_invokable_entry_has_a_description(self) -> None:
        blank = [d.name for d in self.index.docs if d.invokable and not d.description]
        self.assertEqual(blank, [],
            "a skill with no description can never be matched, so it is "
            "installed but unreachable")

    def test_builtins_are_present_and_invokable(self) -> None:
        by_name = {d.name: d for d in self.index.docs}
        for name in ("code-review", "dataviz", "security-review"):
            with self.subTest(name=name):
                self.assertIn(name, by_name,
                    "built-in skills live nowhere on disk; without an explicit "
                    "entry nothing can ever route to them")
                self.assertTrue(by_name[name].invokable)

    def test_no_subagents_leaked_into_the_invokable_population(self) -> None:
        """A sub-agent name must never be rankable as something to invoke.

        Scoped to invokable entries on purpose: an *uninstalled* skill may
        legitimately share a name with one of this machine's agents, and that
        is harmless because the matcher can only offer it as an install hint.
        """
        invokable = {d.name for d in self.index.docs if d.invokable}
        for agent in ("test-runner", "db-expert", "security-auditor"):
            self.assertNotIn(agent, invokable,
                f"{agent} is a sub-agent; Skill(skill=...) cannot load it")

    def test_discussion_prompts_find_no_specialist(self) -> None:
        for prompt in (
            "what do you think about this design",
            "can we discuss the trade-offs here",
            "do you remember what we decided last week",
            "is there a reason this is slow",
        ):
            with self.subTest(prompt=prompt):
                self.assertIsNone(catalog_match.best(prompt))

    def test_named_domains_find_their_specialist(self) -> None:
        """When the specialist is installed it must be found. When it has been
        archived or uninstalled the case is skipped rather than failed: the
        matcher cannot be blamed for a skill that is not there, and a suite
        that goes red whenever the user tidies their skills teaches them to
        ignore the suite."""
        installed = {d.name for d in self.index.docs if d.invokable}
        for prompt, expected in (
            ("run a technical seo audit on the marketing site", "seo-technical"),
            ("submit the build to testflight", "scrollbook-deploy"),
            ("check my jobhunt pipeline status", "jobhunt-agent"),
            ("deploy to vercel production", "vercel:deploy"),
        ):
            with self.subTest(prompt=prompt):
                if expected not in installed:
                    self.skipTest(f"{expected} is not installed on this machine")
                match = catalog_match.best(prompt)
                self.assertIsNotNone(match, f"{prompt!r} found no specialist")
                self.assertEqual(match.name, expected)

    def test_ranking_is_fast_enough_for_a_hook(self) -> None:
        import time
        start = time.perf_counter()
        for _ in range(20):
            catalog_match.rank("run a technical seo audit and fix the crawl errors")
        elapsed = (time.perf_counter() - start) / 20
        self.assertLess(elapsed, 0.05,
            f"{elapsed*1000:.0f}ms per query is too slow for a prompt hook")


class TestCatalogBuilder(unittest.TestCase):

    def test_frontmatter_description_wins(self) -> None:
        fm = build_catalog.parse_frontmatter(
            "---\nname: x\ndescription: Use when testing\n---\n# Body\n")
        self.assertEqual(fm["description"], "Use when testing")

    def test_folded_multiline_description_is_joined(self) -> None:
        fm = build_catalog.parse_frontmatter(
            "---\nname: x\ndescription: >\n  first line\n  second line\n---\n")
        self.assertEqual(fm["description"], "first line second line")

    def test_quotes_are_stripped(self) -> None:
        fm = build_catalog.parse_frontmatter('---\nname: x\ndescription: "Quoted"\n---\n')
        self.assertEqual(fm["description"], "Quoted")

    def test_description_synthesized_when_frontmatter_absent(self) -> None:
        """Slash-commands open at `# Title` with no YAML block.

        They are the skills a user reaches for by name, so leaving them
        description-less would make them permanently unmatchable.
        """
        desc = build_catalog.synth_description(
            "# Code Refactoring\n\nAnalyze the codebase and suggest improvements.\n")
        self.assertIn("Code Refactoring", desc)
        self.assertIn("Analyze the codebase", desc)

    def test_no_frontmatter_returns_empty_dict(self) -> None:
        self.assertEqual(build_catalog.parse_frontmatter("# Just a heading\n"), {})


class TestDisabledPlugins(unittest.TestCase):
    """A disabled plugin's files stay in the cache; its skills must not be indexed."""

    def setUp(self) -> None:
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="catalog-plugins-"))
        self._cache, self._settings = build_catalog.PLUGINS_CACHE, build_catalog.SETTINGS
        for plugin in ("alpha", "beta", "gamma"):
            d = self.tmp / "cache" / "market" / plugin / "1.0.0" / "skills" / f"{plugin}-skill"
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"---\nname: {plugin}-skill\ndescription: does {plugin}\n---\n")
        build_catalog.PLUGINS_CACHE = self.tmp / "cache"
        build_catalog.SETTINGS = self.tmp / "settings.json"

    def tearDown(self) -> None:
        build_catalog.PLUGINS_CACHE, build_catalog.SETTINGS = self._cache, self._settings

    def names(self) -> list[str]:
        return sorted(e["name"] for e in build_catalog.scan_plugins()[0])

    def test_false_is_skipped_true_and_unmentioned_are_kept(self) -> None:
        build_catalog.SETTINGS.write_text(json.dumps(
            {"enabledPlugins": {"alpha@market": False, "beta@market": True}}))
        self.assertEqual(self.names(), ["beta:beta-skill", "gamma:gamma-skill"])

    def test_same_plugin_name_in_another_marketplace_is_not_disabled(self) -> None:
        build_catalog.SETTINGS.write_text(json.dumps({"enabledPlugins": {"alpha@elsewhere": False}}))
        self.assertIn("alpha:alpha-skill", self.names())

    def test_unreadable_settings_disable_nothing(self) -> None:
        for body in (None, "{not json", json.dumps({"enabledPlugins": "nope"})):
            if body is None:
                build_catalog.SETTINGS.unlink(missing_ok=True)
            else:
                build_catalog.SETTINGS.write_text(body)
            self.assertEqual(len(self.names()), 3, body)


if __name__ == "__main__":
    unittest.main()
