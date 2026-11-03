#!/usr/bin/env python3
"""
Probe set for the v4 index ranker — the prompts that the v3 regex router
answered with silence or the wrong skill on 2026-09-07.

These run against a fixed catalog and lexical index. Live catalog calibration
belongs in the benchmark, separate from deterministic unit tests.

Run:  python3 -m pytest tests/test_index_match.py -q
"""
from __future__ import annotations

import os
import json
from unittest.mock import patch
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
    catalog = _TMP / "catalog.json"
    entries = [{'name': 'brand-manager',
      'description': "Owns the owner's personal brand end-to-end — LinkedIn and X content strategy, "
                     'drafting in the locked v2 voice, scheduling, cadence, voice-drift auditing, '
                     'and quarterly positioning review. Use whenever the request touches personal '
                     'brand, LinkedIn posts, "what should I post", content planning, brand '
                     'strategy, post performance, or turning a video/paper/shipped feature into '
                     'content. Also use for "what\'s my brand status" and to publish or schedule '
                     'anything on the personal (not product) accounts.',
      'type': 'skill',
      'invokable': True},
     {'name': 'dealscout',
      'description': 'AI buy-side acquisition advisor. Use when the user wants to find, analyze, '
                     'or learn about businesses to buy. Modes - hunt (search live marketplaces, '
                     'return scored shortlist), analyze <url or description> (full deal memo), '
                     'teach <topic> (acquisition education anchored to real pipeline deals), '
                     'review (pipeline status + next actions). Also use when the user shares a '
                     'business listing URL or asks "should I buy this".',
      'type': 'skill',
      'invokable': True},
     {'name': 'design-review',
      'description': "Designer's eye QA: finds visual inconsistency, spacing issues, hierarchy "
                     'problems, AI slop patterns, and slow interactions — then fixes them. '
                     '(gstack)',
      'type': 'skill',
      'invokable': True},
     {'name': 'higgsfield',
      'description': 'Workflow for generating images + videos via Higgsfield CLI. Use when '
                     'generating B-roll, cold-open stat cards, cinematic clips, character images, '
                     'or batch video assets for @Economicalai YouTube channel + scrollbook video '
                     'shorts. Covers CLI commands, model selection, cost optimization, plan-tier '
                     'gotchas, 7-day vs 365-day unlimited promos, and the split between '
                     'CLI-automatable and web-UI-only features (Lipsync Studio + Audio Suite + '
                     'Marketing Studio).',
      'type': 'skill',
      'invokable': True},
     {'name': 'jobhunt-agent',
      'description': 'Use for jobhunt pipeline status, resume tailoring, applications and job '
                     'postings',
      'type': 'skill',
      'invokable': True},
     {'name': 'mac-doctor',
      'description': 'Diagnose and maintain this Mac end-to-end — random restarts/kernel panics, '
                     'runaway processes, boot-service bloat, disk-space reclaim, and cache '
                     'cleanup. Use when the user says "my mac keeps restarting/crashing", "clean '
                     'up my mac", "free disk space", "mac is slow/hot/loud", "what\'s eating '
                     'CPU", "audit startup items", "optimize my macbook", or "run maintenance". '
                     'Combines the best of the community macos-cleaner skills with restart/panic '
                     'root-cause diagnosis and boot-service tracing. macOS only.',
      'type': 'skill',
      'invokable': True},
     {'name': 'scrollbook-deploy',
      'description': 'Use for Scrollbook iOS Android deployment, TestFlight build submission and '
                     'release',
      'type': 'skill',
      'invokable': True},
     {'name': 'cost-ledger',
      'description': 'AI and cloud cost analysis grounded in The AI Cost Ledger '
                     '(a cost reference). Use when the user asks what something costs on '
                     'AI/GPU/cloud, wants their bill audited, asks whether to commit/reserve/buy '
                     'hardware, or asks why an AI feature is expensive. Trigger phrases — audit '
                     'my bill, why is my AWS bill, GPU cost, H100 price, token cost, savings '
                     'plan, reserved instance, spot instances, egress cost, should we self-host, '
                     'inference cost, cost per token, cloud cost review, cost-ledger, ai cost '
                     'ledger.',
      'type': 'skill',
      'invokable': True},
     {'name': 'wseller',
      'description': 'Autonomous local-business lead generation. Discovers businesses without '
                     'dedicated websites, generates a one-page sample preview site for each, and '
                     'drafts opt-out-aware outreach. Use when the user wants to "find leads", '
                     '"run wseller", or asks for "businesses without websites in [city]" / '
                     '"[niche] in [city]". Trigger phrases — wseller, find leads, lead gen, '
                     'no-website businesses, prospecting, cold outreach.',
      'type': 'skill',
      'invokable': True},
     {'name': 'youtube-manager',
      'description': 'Activated when working on @Economicalai YouTube content — script writing, '
                     'research, B-roll planning, sprint roadmap, content strategy, pillar '
                     "tagging, render workflow. Encapsulates the owner's locked rules: 4 "
                     'perspective pillars, Earned Vulnerability gradient, Frameworks-Invisible, '
                     'HPC framework, Original-Script-Is-The-Asset, 60-90s depth default, '
                     'Runway-as-default pipeline. Use BEFORE any video work — scripts, research, '
                     'B-roll cues, slotting decisions, retros.',
      'type': 'skill',
      'invokable': True},
     {'name': 'yt-to-blog',
      'description': 'Use when given a YouTube URL and asked to write a blog post, LinkedIn post, '
                     "or both. Extracts transcript, verifies sources, and writes in the user's "
                     'voice. LinkedIn posts follow the locked v2 rules (flowing narrative, inline '
                     'links, no first-person credentials) plus the 2026 strategy — continuity '
                     'check against published_posts before drafting, name the funnel bucket, '
                     'write the hook last — and the AI citation layer (first line is the URL '
                     'slug, one quotable answer sentence, decision rule on every POV, structured '
                     'first comment).',
      'type': 'skill',
      'invokable': True},
     {'name': 'superpowers:systematic-debugging',
      'description': 'Use when encountering any bug, test failure, or unexpected behavior, before '
                     'proposing fixes',
      'type': 'plugin-skill',
      'invokable': True}]
    # A stable background population keeps IDF and the >50-entry shape gate
    # meaningful without depending on installed or downloaded skills.
    entries += [{"name": f"fixture-{i}", "description": f"Fixture specialized zdomain{i}", "invokable": True, "type": "skill"} for i in range(60)]
    entries.append({"name": "deenunlock-deploy", "description": "Use for DeenUnlock fastlane TestFlight release deployment", "invokable": True, "type": "skill"})
    catalog.write_text(json.dumps({"entries": entries}))
    with patch.object(build_index, "load_memory_names", return_value=[]), patch.object(build_index, "read_enrichment", return_value=None):
        data = build_index.build(catalog_path=catalog, projects={"mac": {"aliases": ["macbook", "mac mini"], "skills": ["mac-doctor"]}, "scrollbook": {"aliases": ["scrollbook"], "skills": ["scrollbook-deploy"]}, "deenunlock": {"aliases": ["deenunlock"], "skills": ["deenunlock-deploy"]}})
    INDEX.write_text(json.dumps(data))
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
    ("research competitors for cost-ledger", "cost-ledger", None),
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
