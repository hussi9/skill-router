#!/usr/bin/env python3
"""skills.sh leaderboard ingest: parsing, audit screening, categories, bounds. Offline."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import skills_sh_catalog as sh  # type: ignore[import-not-found]


def page(key: str, rows: list[dict]) -> str:
    """A page whose flight payload carries `rows` under `key`, escaped like Next.js does."""
    payload = '53:["$","$L61",null,{"' + key + '":' + json.dumps(rows) + "}]\n"
    return '<script>self.__next_f.push([1,' + json.dumps(payload) + "])</script>"


def row(source: str, skill: str, installs: int, **extra) -> dict:
    return {"source": source, "skillId": skill, "name": skill, "installs": installs, **extra}


class TestParsing(unittest.TestCase):
    def test_rows_come_back_in_page_order_without_duplicates(self) -> None:
        html = page("initialSkills", [row("a/b", "x", 5), row("a/b", "y", 3), row("a/b", "x", 5)])
        self.assertEqual([r["skillId"] for r in sh.board_rows(html)], ["x", "y"])

    def test_garbage_page_yields_nothing_not_an_exception(self) -> None:
        self.assertEqual(sh.board_rows("<html>no payload</html>"), [])
        self.assertEqual(sh.board_rows('<script>self.__next_f.push([1,"\\u0000 {bad"])</script>'), [])

    def test_trend_is_last_four_weeks_over_previous_four(self) -> None:
        self.assertEqual(sh.trend([10, 10, 10, 10, 20, 20, 20, 20]), 2.0)
        self.assertEqual(sh.trend([1, 2]), 1.0)
        self.assertEqual(sh.trend(None), 1.0)
        self.assertEqual(sh.trend([0, 0, 0, 0, 5, 5, 5, 5]), 1.0)

    def test_audit_one_objection_flags_the_skill(self) -> None:
        clean = {"partners": [{"verdict": "SAFE"}, {"status": "clean"}]}
        self.assertEqual(sh.audit_verdict(clean), "clean")
        bad = {"partners": [{"verdict": "SAFE"}, {"overall_risk_level": "HIGH"}]}
        self.assertEqual(sh.audit_verdict(bad), "flagged")
        self.assertEqual(sh.audit_verdict({"malicious": 2}), "flagged")
        self.assertEqual(sh.audit_verdict({"verdict": "unknown"}), "unaudited")


class TestCategories(unittest.TestCase):
    def test_whole_word_starts_only(self) -> None:
        self.assertEqual(sh.categorize("latest-news", "the latest headlines"), sh.OTHER)
        self.assertEqual(sh.categorize("e2e-runner", "run playwright tests"), "testing")

    def test_name_outweighs_prose(self) -> None:
        self.assertEqual(sh.categorize("write-swift", "value types and data-race safety"), "mobile")

    def test_unknown_is_other(self) -> None:
        self.assertEqual(sh.categorize("zzz", ""), sh.OTHER)


class TestFetchBounds(unittest.TestCase):
    def test_api_and_foreign_hosts_are_refused(self) -> None:
        for url in ("https://skills.sh/api/search?q=x", "https://www.skills.sh/search", "http://skills.sh/",
                    "https://evil.example/", "https://skills.sh.evil.example/"):
            with self.assertRaises(sh.FetchError, msg=url):
                sh.fetch(url)
        self.assertTrue(sh._allowed("https://www.skills.sh/trending"))


class TestBuild(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="shcat-"))
        self.old = sh.CACHE_DIR
        sh.CACHE_DIR = self.tmp / "cache"
        self.calls: list[str] = []
        sh.DELAY_S = 0

    def tearDown(self) -> None:
        sh.CACHE_DIR = self.old

    def fetcher(self, url: str) -> str:
        self.calls.append(url)
        if url.endswith("/audits"):
            return page("initialRows", [
                {"rank": 1, "source": "o/r", "skillId": "ok", "p": {"verdict": "SAFE"}},
                {"rank": 2, "source": "o/r", "skillId": "evil", "p": {"verdict": "malicious"}}])
        if url.rstrip("/").endswith(("skills.sh", "trending", "hot")):
            return page("initialSkills", [
                row("o/r", "ok", 900, weeklyInstalls=[1, 1, 1, 1, 2, 2, 2, 2], isOfficial=True),
                row("o/r", "evil", 5000), row("o/r", "playwright-e2e", 300)])
        name = url.rsplit("/", 1)[1]
        return f'<meta name="description" content="{name} &amp; friends: run playwright tests">'

    def test_flagged_skills_never_reach_the_cache(self) -> None:
        cat = sh.build(self.fetcher, now=1000.0)
        names = [e["name"] for e in cat["catalogs"]["skills.sh"]]
        self.assertIn("ok", names)
        self.assertNotIn("evil", names)
        ok = next(e for e in cat["catalogs"]["skills.sh"] if e["name"] == "ok")
        self.assertEqual((ok["audit"], ok["official"], ok["trend"]), ("clean", True, 2.0))
        self.assertEqual(ok["install_command"], "npx skills add o/r --skill ok")
        self.assertIn("&", ok["description"])           # entities decoded

    def test_second_run_inside_ttl_makes_no_skill_page_requests(self) -> None:
        sh.build(self.fetcher, now=1000.0)
        self.calls.clear()
        sh.build(self.fetcher, now=1000.0 + 60)
        self.assertFalse([c for c in self.calls if c.count("/") > 4],
                         "descriptions are cached; only the four boards are refetched")

    def test_page_budget_is_respected(self) -> None:
        sh.build(self.fetcher, max_pages=1, now=1000.0)
        self.assertLessEqual(len([c for c in self.calls if c.count("/") > 4]), 1)

    def test_all_boards_failing_gives_an_empty_catalog_not_a_crash(self) -> None:
        def boom(url: str) -> str:
            raise sh.FetchError("down")
        cat = sh.build(boom, now=1000.0)
        self.assertEqual(cat["catalogs"]["skills.sh"], [])
        self.assertTrue(cat["metadata"]["errors"])

    def test_report_groups_by_category(self) -> None:
        text = sh.render_report(sh.build(self.fetcher, now=1000.0))
        self.assertIn("## testing", text)
        self.assertIn("[ok]", text)

    def test_atomic_write_is_private(self) -> None:
        out = self.tmp / "o.json"
        sh.write_atomic(out, {"a": 1})
        self.assertEqual(oct(out.stat().st_mode & 0o777), "0o600")
        self.assertTrue(sh.fresh(out, now=None) is False)   # no metadata -> not fresh


class TestRouterUsesTheCache(unittest.TestCase):
    def test_suggestion_prefers_the_more_installed_skill_and_skips_flagged(self) -> None:
        import router  # type: ignore[import-not-found]
        tmp = Path(tempfile.mkdtemp(prefix="shr-"))
        entries = [
            {"name": "pg-tuning", "description": "tune postgres query performance indexes",
             "source": "online:skills.sh", "installs": 10, "audit": "clean", "installed": False,
             "install_command": "npx skills add a/b --skill pg-tuning"},
            {"name": "postgres-perf", "description": "tune postgres query performance indexes",
             "source": "online:skills.sh", "installs": 9000, "audit": "unaudited", "installed": False,
             "install_command": "npx skills add c/d --skill postgres-perf"},
            {"name": "pg-evil", "description": "tune postgres query performance indexes",
             "source": "online:skills.sh", "installs": 99999, "audit": "flagged", "installed": False},
            {"name": "no-desc", "description": "", "source": "online:skills.sh", "installs": 99999,
             "audit": "clean", "installed": False},
        ]
        f = tmp / "sh.json"
        f.write_text(json.dumps({"catalogs": {"skills.sh": entries}}))
        saved = (router.SKILLS_SH_FILE, router.ONLINE_CATALOG_FILE, router.LOCAL_CATALOG_FILE)
        router.SKILLS_SH_FILE, router.ONLINE_CATALOG_FILE, router.LOCAL_CATALOG_FILE = \
            f, tmp / "none.json", tmp / "none2.json"
        try:
            got = router.suggest_online_skill("tune postgres query performance indexes please")
            self.assertEqual(got["name"], "postgres-perf")
            self.assertIn("9,000 installs", router.render_online_suggestion(got))
        finally:
            router.SKILLS_SH_FILE, router.ONLINE_CATALOG_FILE, router.LOCAL_CATALOG_FILE = saved


if __name__ == "__main__":
    unittest.main()


class TestJevRefine(unittest.TestCase):
    def test_refine_refiles_other_and_caches_the_answer(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="shj-"))
        old = sh.CACHE_DIR
        sh.CACHE_DIR = tmp
        try:
            entries = [{"name": "zed", "description": "pg things", "category": sh.OTHER,
                        "tags": [sh.OTHER], "source_url": "https://www.skills.sh/o/r/zed"},
                       {"name": "kept", "description": "x", "category": "testing",
                        "tags": ["testing"], "source_url": "https://www.skills.sh/o/r/kept"}]
            calls: list[str] = []

            def fake(name: str, desc: str):
                calls.append(name)
                return "database"
            self.assertEqual(sh.refine_with_jev(entries, call=fake), 1)
            self.assertEqual((entries[0]["category"], entries[1]["category"]), ("database", "testing"))
            entries[0]["category"] = sh.OTHER
            sh.refine_with_jev(entries, call=fake)
            self.assertEqual(calls, ["zed"], "second pass is served from the cache")
        finally:
            sh.CACHE_DIR = old

    def test_no_key_means_keywords_stand(self) -> None:
        saved = os.environ.pop("TYPESAFE_API_KEY", None)
        try:
            import jev_choose  # type: ignore[import-not-found]
            old = jev_choose.ENV_FILE
            jev_choose.ENV_FILE = Path(tempfile.mkdtemp()) / "none.json"
            self.assertIsNone(sh.jev_category("a", "b"))
            jev_choose.ENV_FILE = old
        finally:
            if saved:
                os.environ["TYPESAFE_API_KEY"] = saved
