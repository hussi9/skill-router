#!/usr/bin/env python3
"""Tests for `skill-router stats` and outcomes.lift/median_chars on a fixture log.

Run: python3 -m pytest tests/test_cli.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import cli  # type: ignore[import-not-found]  # noqa: E402
import outcomes  # type: ignore[import-not-found]  # noqa: E402


class StatsCase(unittest.TestCase):
    def setUp(self) -> None:
        self.log = Path(tempfile.mkdtemp(prefix="cli-")) / "o.jsonl"
        self._old = os.environ.get("SKILL_ROUTER_OUTCOMES_FILE")
        os.environ["SKILL_ROUTER_OUTCOMES_FILE"] = str(self.log)
        os.environ.pop("SKILL_ROUTER_NO_LEARN", None)

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("SKILL_ROUTER_OUTCOMES_FILE", None)
        else:
            os.environ["SKILL_ROUTER_OUTCOMES_FILE"] = self._old

    def write(self, events: list[dict]) -> None:
        now = time.time()
        with self.log.open("w") as f:
            for e in events:
                f.write(json.dumps({"ts": now, **e}) + "\n")

    def fixture(self) -> None:
        def shown(p, src="jev", held=False, chars=400):
            e = {"type": "shown", "session_id": "s", "prompt_id": p, "card": ["a"], "shortlist": ["b"],
                 "source": src, "chars": chars}
            if held:
                e["held_out"] = True
            return e

        def sig(p, kind, skill=None):
            e = {"type": "signal", "session_id": "s", "prompt_id": p, "kind": kind}
            if skill:
                e["skill"] = skill
            return e
        self.write([
            shown("r1"), sig("r1", "loaded", "a"), sig("r1", "tests_green"),
            shown("r2", "keyless", chars=200), sig("r2", "correction"),
            shown("r3"),                                   # unknown
            shown("h1", held=True), sig("h1", "tests_green"),
            shown("h2", held=True), sig("h2", "tests_green"), sig("h2", "correction"),
            shown("h3", held=True),                        # unknown
        ])

    def test_lift_counts_and_value(self) -> None:
        self.fixture()
        lf = outcomes.lift(30)
        self.assertEqual(lf["routed"], {"success": 1, "known": 2})
        self.assertEqual(lf["held_out"], {"success": 1, "known": 2})
        self.assertEqual(lf["lift"], 0.0)

    def test_red_only_turn_is_a_known_failure(self) -> None:
        def shown(p, held=False):
            e = {"type": "shown", "session_id": "s", "prompt_id": p, "card": ["a"], "shortlist": [],
                 "source": "jev"}
            return {**e, "held_out": True} if held else e

        def sig(p, kind):
            return {"type": "signal", "session_id": "s", "prompt_id": p, "kind": kind}
        self.write([shown("r1"), sig("r1", "tests_red"),
                    shown("r2"), sig("r2", "tests_green"),
                    shown("r3"), sig("r3", "tests_red"), sig("r3", "tests_green"),
                    shown("h1", True), sig("h1", "tests_green")])
        lf = outcomes.lift(30)
        self.assertEqual(lf["routed"], {"success": 1, "known": 3})
        self.assertEqual(lf["held_out"], {"success": 1, "known": 1})

    def test_lift_skips_empty_prompt_id(self) -> None:
        self.write([{"type": "shown", "session_id": "s", "prompt_id": "", "card": ["a"], "shortlist": [],
                     "source": "jev"},
                    {"type": "signal", "session_id": "s", "prompt_id": "", "kind": "tests_green"},
                    {"type": "shown", "session_id": "s", "prompt_id": "", "card": ["a"], "shortlist": [],
                     "source": "jev", "held_out": True}])
        lf = outcomes.lift(30)
        self.assertEqual(lf["routed"], {"success": 0, "known": 0})
        self.assertEqual(lf["held_out"], {"success": 0, "known": 0})
        self.assertIsNone(lf["lift"])

    def test_lift_none_without_held_out(self) -> None:
        self.write([{"type": "shown", "session_id": "s", "prompt_id": "p", "card": ["a"],
                     "shortlist": [], "source": "jev"},
                    {"type": "signal", "session_id": "s", "prompt_id": "p", "kind": "tests_green"}])
        lf = outcomes.lift(30)
        self.assertIsNone(lf["lift"])
        self.assertEqual(lf["held_out"]["known"], 0)

    def test_median_chars_skips_missing_and_held_out(self) -> None:
        self.fixture()
        self.assertEqual(outcomes.median_chars(30), 400.0)
        self.write([{"type": "shown", "session_id": "s", "prompt_id": "p", "card": ["a"],
                     "shortlist": [], "source": "jev"}])
        self.assertIsNone(outcomes.median_chars(30))

    def test_record_shown_chars(self) -> None:
        outcomes.record_shown("s", "p", ["a"], [], "jev", chars=123)
        outcomes.record_shown("s", "q", ["a"], [], "jev")
        ev = [json.loads(x) for x in self.log.read_text().splitlines()]
        self.assertEqual(ev[0]["chars"], 123)
        self.assertNotIn("chars", ev[1])

    def test_stats_text(self) -> None:
        self.fixture()
        out = cli.stats(30)
        lines = out.splitlines()
        self.assertLessEqual(len(lines), 20)
        self.assertIn("Lines shown: 3 (held out: 3)", out)
        self.assertIn("jev", out)
        self.assertIn("keyless", out)
        self.assertIn("routed: 1/2; held out: 1/2", out)
        self.assertIn("+0 points", out)
        self.assertIn("~100", out)  # median 400 chars / 4
        self.assertIn("per shown line", out)

    def test_stats_empty(self) -> None:
        out = cli.stats(30)
        self.assertIn("No outcomes recorded yet", out)
        self.assertIn("not enough data", out)
        self.assertLessEqual(len(out.splitlines()), 20)

    def test_cli_entry(self) -> None:
        self.fixture()
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "cli.py"), "stats", "--days", "7"],
                           capture_output=True, text=True, env=os.environ.copy())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("last 7 days", r.stdout)


# ---- audit renderers (synthetic reports only) ----------------------------------

import re as _re  # noqa: E402
from skill_audit import AuditReport  # type: ignore[import-not-found]  # noqa: E402


def _report(**kw) -> AuditReport:
    base = dict(skills=40, listing_tokens=9000, unused_90d=["alpha"], history_days=60,
                shown_never_loaded=["beta"], overlaps=[("one", "two", 0.9, "keyword")],
                flagged=["gamma"], score=71, top_actions=["Review alpha"], age_unknown=0,
                plugin_usage=[("plug", 1, 4)])
    base.update(kw)
    return AuditReport(**base)


class AuditRenderCase(unittest.TestCase):
    def test_listing_and_thin_lines_render(self) -> None:
        import cli
        rep = _report(listing_tokens=23500, listing_sent_tokens=2000, listing_budget_tokens=2000,
                      descriptions_dropped=7, thin_descriptions=4)
        for fmt in ("text", "md", "html"):
            out = cli.render(rep, fmt)
            self.assertIn("2000 of ~23500 uncapped listing tokens fit; 7 descriptions dropped", out)
            self.assertIn("uncapped", out)
            self.assertIn("4 skills have descriptions too thin to route", out)

    def test_text_fits_30_lines(self) -> None:
        names = [f"skill-{i}" for i in range(200)]
        r = _report(unused_90d=names, shown_never_loaded=names,
                    overlaps=[(n, n + "x", 0.9, "jev") for n in names], flagged=names,
                    plugin_usage=[(f"p{i}", 0, 5) for i in range(50)],
                    top_actions=["a", "b", "c"])
        out = cli.render(r, "text")
        self.assertLessEqual(len(out.splitlines()), 30)
        self.assertIn("Score: 71/100", out)
        self.assertIn("judged by Jev", out)
        self.assertIn("review before archiving", out)
        self.assertNotRegex(out.lower(), r"\b(remove|delete)\b")

    def test_report_escapes_descriptions(self) -> None:
        r = _report(top_actions=["<script>x</script> a|b *c* _d_ `e`\x1b[31m"])
        md = cli.render(r, "md")
        self.assertNotIn("<script>", md)
        self.assertIn("a\\|b", md)
        self.assertIn("\\[x\\]", cli.render(_report(top_actions=["[x](y) #!"]), "md"))
        self.assertNotIn("\u2028", cli.render(_report(top_actions=["a\u2028b"]), "text"))
        self.assertNotIn("\x1b", md)
        h = cli.render(r, "html")
        self.assertNotIn("<script>x", h)
        self.assertIn("&lt;script&gt;", h)
        self.assertNotIn("\x1b", cli.render(r, "text"))

    def test_html_has_no_external_urls(self) -> None:
        h = cli.render(_report(), "html")
        self.assertNotRegex(h, r"https?://|//cdn|<script|<link|src=")
        self.assertIn("prefers-color-scheme", h)
        self.assertIn("<style>", h)

    def test_worst_case_text_keeps_actions_and_caveats(self) -> None:
        names = [f"s{i}" for i in range(60)]
        r = _report(unused_90d=names, shown_never_loaded=names, flagged=names, age_unknown=3,
                    overlaps=[(n, n + "x", 0.9, "jev") for n in names],
                    plugin_usage=[(f"p{i}", 0, 5) for i in range(60)],
                    top_actions=["Trim things", "Merge pairs", "check its source: a, b"])
        out = cli.render(r, "text")
        self.assertLessEqual(len(out.splitlines()), 30)
        self.assertIn("Trim things", out)
        self.assertIn("review before archiving", out)
        self.assertIn("same name as a flagged public skill", out)
        self.assertIn("more", out)

    def test_unsafe_name_in_action_is_dropped(self) -> None:
        act = "1 skills have the same name as a flagged public skill — check its source: evil<b>x, okname."
        r = _report(top_actions=[act, "Keep this"])
        for fmt in ("text", "md", "html"):
            out = cli.render(r, fmt)
            self.assertNotIn("evil", out)
            self.assertIn("Keep this", out)

    def test_audit_filters_unsafe_flagged_from_actions(self) -> None:
        import skill_audit as sa  # type: ignore[import-not-found]
        cat = [{"name": "okname", "description": "x"}, {"name": "bad name", "description": "y"}]
        sh = {"catalogs": {"skills.sh": [{"name": "okname", "audit": "flagged"},
                                         {"name": "bad name", "audit": "flagged"}]}}
        rep = sa.audit(cat, {}, 60, None, sh)
        self.assertEqual(rep.flagged, ["okname"])
        self.assertTrue(all("bad name" not in a for a in rep.top_actions))
        self.assertNotIn("bad name", rep.flagged)

    def test_report_drops_unsafe_names(self) -> None:
        bad = ["bad name", "x<b>", "a|b", "-lead", "n\nl"]
        r = _report(unused_90d=bad + ["okname"], shown_never_loaded=bad, flagged=bad,
                    overlaps=[("okname", b, 0.9, "keyword") for b in bad])
        for fmt in ("text", "md", "html"):
            out = cli.render(r, fmt)
            for b in bad:
                self.assertNotIn(b, out)
            self.assertIn("okname", out)

    def test_no_home_paths_in_output(self) -> None:
        home = str(Path.home())
        r = _report(top_actions=[f"see {home}/.claude/skills/x"])
        for fmt in ("text", "md", "html"):
            out = cli.render(r, fmt)
            self.assertNotIn(home, out)
            self.assertIn("~/.claude", out)

    def test_short_history_says_no_usage_history(self) -> None:
        out = cli.render(_report(history_days=5, unused_90d=[]), "text")
        self.assertIn("no usage history yet", out)
        self.assertNotIn("Unused", out)
        self.assertNotIn("Plugin usage", out)

    def test_mode_and_flagged_wording(self) -> None:
        out = cli.render(_report(), "text")
        self.assertIn("judged by keyword", out)
        self.assertIn("same name as a flagged public skill — check its source", out)

    def test_cli_out_file_mode_600(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aud-") as td:
            self._out_mode(Path(td))

    def _out_mode(self, d: Path) -> None:
        env = os.environ.copy()
        for k in ("INDEX", "CATALOG", "LEARNED", "USAGE_LOG", "SETTINGS"):
            env[f"SKILL_AUDIT_{k}"] = str(d / "missing")
        env["SKILL_ROUTER_SKILLS_SH_FILE"] = str(d / "missing")
        env["SKILL_ROUTER_OUTCOMES_FILE"] = str(d / "missing.jsonl")
        out = d / "r.md"
        r = subprocess.run([sys.executable, str(ROOT / "scripts" / "cli.py"), "audit",
                            "--no-jev", "--format", "md", "--out", str(out)],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(oct(out.stat().st_mode & 0o777), "0o600")
        self.assertIn("# Skill doctor", out.read_text())


if __name__ == "__main__":
    unittest.main()
