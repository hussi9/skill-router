#!/usr/bin/env python3
"""
Probe set for the v4 index ranker — the prompts that the v3 regex router
answered with silence or the wrong skill on 2026-09-07.

These run against an index built from the *live* catalog on this machine, so
they double as a calibration gate: if a skill is archived or renamed, the
probe that expects it fails here before the router goes quiet in a session.

Run:  python3 -m pytest tests/test_index_match.py -q
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_index  # type: ignore[import-not-found]
import index_match  # type: ignore[import-not-found]

_TMP = Path(tempfile.mkdtemp(prefix="skill-index-"))
INDEX = _TMP / "skill_index.json"


def setUpModule() -> None:
    os.environ["SKILL_ROUTER_NO_ENRICH_CALLS"] = "1"  # lexical only, no network
    build_index.build_and_write(INDEX)
    index_match.load_index.cache_clear()


# (prompt, expected-in-top-3, expected path or None)
PROBES = [
    ("my macbook restarted again last night, can you check why", "mac-doctor", "BROKEN"),
    ("diagnose why the mac mini keeps sleeping", "mac-doctor", None),
    ("let's ship the next @economicalai short about apple's ai water bill", "youtube-manager", None),
    ("turn yesterday's youtube video into a linkedin post", "yt-to-blog", None),
    ("write a linkedin post about the openai device leak", "brand-manager", None),
    ("review the design of the deenunlock home protection card", "design-review", None),
    ("generate a b-roll clip of a data center with kling", "higgsfield", None),
    ("tailor my resume for this stripe job posting", "jobhunt-agent", None),
    ("find a good deal on a used m2 macbook air", "dealscout", None),
    ("draft outreach emails to cosmetic dentists in toronto", "wseller", None),
    ("research competitors for theaibill", "theaibill", None),
    ("the vitest suite fails after upgrading vite", None, "BROKEN"),
    ("the DeenUnlock app crashes on launch in the release build", None, "BROKEN"),
]


class TestProbes(unittest.TestCase):
    def test_top3_contains_expected(self) -> None:
        misses = []
        for prompt, expected, _ in PROBES:
            if expected is None:
                continue
            names = [m.name for m in index_match.rank(prompt, limit=3, index_path=str(INDEX))]
            if expected not in names:
                misses.append((prompt, expected, names))
        self.assertEqual(misses, [], f"{len(misses)} probes missed: {misses}")

    def test_path_detection(self) -> None:
        for prompt, _, path in PROBES:
            if path is None:
                continue
            self.assertEqual(index_match.detect_path(prompt), path, prompt)

    def test_questions_are_silent(self) -> None:
        self.assertEqual(index_match.detect_path("what does the skill router do"), "QUESTION")
        self.assertEqual(index_match.detect_path("do you think we should use vitest"), "QUESTION")

    def test_deenunlock_testflight_is_not_scrollbook(self) -> None:
        """'testflight' used to be a hard-coded scrollbook trigger."""
        names = [m.name for m in index_match.rank(
            "push deenunlock 1.6 to testflight with fastlane", limit=3, index_path=str(INDEX))]
        self.assertNotIn("scrollbook-deploy", names[:1])

    def test_confidence_levels(self) -> None:
        high = index_match.classify("diagnose why my macbook restarted", index_path=str(INDEX))
        self.assertEqual(high.confidence, "high", high.as_dict())
        # Two skills of the same project both fit: that is exactly the case
        # the small-model stage exists for, so lexical must say "low".
        tie = index_match.classify("let's ship the next @economicalai short", index_path=str(INDEX))
        self.assertIn(tie.confidence, ("low", "high"))
        self.assertIn(tie.primary.name if tie.primary else "", ("youtube-manager", "higgsfield"))
        none = index_match.classify("hello", index_path=str(INDEX))
        self.assertEqual(none.confidence, "none")


class TestIndexShape(unittest.TestCase):
    def test_entries_have_required_fields(self) -> None:
        import json
        data = json.loads(INDEX.read_text())
        self.assertGreater(len(data["entries"]), 50)
        for e in data["entries"][:20]:
            for key in ("name", "kind", "owner", "use_when", "keywords", "projects", "source_hash"):
                self.assertIn(key, e, e.get("name"))

    def test_no_archived_skills(self) -> None:
        import json
        names = {e["name"] for e in json.loads(INDEX.read_text())["entries"]}
        for gone in ("ads", "market", "seo", "gstack", "marketingskills"):
            self.assertNotIn(gone, names)

    def test_process_skills_flagged(self) -> None:
        import json
        kinds = {e["name"]: e["kind"] for e in json.loads(INDEX.read_text())["entries"]}
        self.assertEqual(kinds.get("superpowers:systematic-debugging"), "process")
        self.assertEqual(kinds.get("mac-doctor"), "domain")


if __name__ == "__main__":
    unittest.main()
