#!/usr/bin/env python3
"""
test_jev_choose.py — the Jev (TypeSafe System One) chooser, with the network
replaced by a recorded-shape transport. Nothing here reaches api.typesafe.ai.

What is pinned:
  - the two Choice questions are built from the index, split on `kind`
  - option keys (d0.., p0..) map back to index names; a key or name the index
    does not hold is never returned
  - confidence tiers: >= 0.8 route, 0.5-0.8 suggest, below that silent
  - any failure (no key, HTTP error, timeout, junk body) returns None so the
    router falls back to the lexical + Gemini path
  - answers are cached by prompt + context + index fingerprint
  - an index larger than one Choice (255 options) is split, not truncated
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import jev_choose  # type: ignore[import-not-found]  # noqa: E402

ENTRIES = [
    {"name": "mac-doctor", "kind": "domain", "type": "skill",
     "description": "Diagnose kernel panics and disk bloat on this Mac",
     "use_when": ["mac keeps restarting", "free disk space"]},
    {"name": "design-review", "kind": "design", "type": "skill",
     "description": "Designer's eye QA of a live UI", "use_when": ["audit the ui"]},
    {"name": "superpowers:systematic-debugging", "kind": "process", "type": "plugin-skill",
     "description": "Root-cause before fixing", "use_when": ["a test fails"]},
    {"name": "superpowers:brainstorming", "kind": "process", "type": "plugin-skill",
     "description": "Explore intent before building", "use_when": ["build a feature"]},
    {"name": "product-designer", "kind": "design", "type": "agent",
     "description": "an agent, never a Skill option", "use_when": []},
    {"name": "debug", "kind": "process", "type": "command",
     "description": "a slash command, never a Skill option", "use_when": []},
    {"name": "artifact-design", "kind": "process", "type": "builtin",
     "description": "a harness builtin, never a Skill option", "use_when": []},
]


def answer(domain: str, dconf: float, process: str, pconf: float,
           path: str = "OPERATE", pathconf: float = 0.9) -> dict:
    def one(choice: str, conf: float) -> dict:
        return {"type": "choice", "choice": choice, "confidence": conf,
                "probabilities": {choice: conf}}
    return {"answers": {"domain_0": one(domain, dconf), "process": one(process, pconf),
                        "path": one(path, pathconf)},
            "usage": {"input_tokens": 1234, "output_tokens": 30}}


class JevCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="jev-test-"))
        self._cache, self._post = jev_choose.CACHE, jev_choose._post
        self._key = jev_choose._key
        jev_choose.CACHE = self.tmp / "jev"
        jev_choose._key = lambda: "test-key"
        self.calls: list[dict] = []
        self._saved_env = os.environ.pop("SKILL_ROUTER_JEV", None)

    def tearDown(self) -> None:
        jev_choose.CACHE, jev_choose._post = self._cache, self._post
        jev_choose._key = self._key
        if self._saved_env is not None:
            os.environ["SKILL_ROUTER_JEV"] = self._saved_env
        else:
            os.environ.pop("SKILL_ROUTER_JEV", None)

    def transport(self, reply):
        def _post(body: dict, key: str, timeout: float):
            self.calls.append(body)
            return reply(body) if callable(reply) else reply
        jev_choose._post = _post


class TestQuestions(JevCase):
    def test_split_on_kind_and_agents_excluded(self) -> None:
        questions, keymap = jev_choose.build_questions(ENTRIES)
        self.assertEqual(sorted(questions), ["domain_0", "path", "process", "tier"])
        self.assertEqual(sorted(keymap["domain_0"].values()), ["design-review", "mac-doctor"])
        self.assertEqual(sorted(keymap["process"].values()),
                         ["superpowers:brainstorming", "superpowers:systematic-debugging"])
        flat = json.dumps(questions)
        self.assertNotIn("an agent, never", flat)
        self.assertNotIn("a slash command, never", flat)
        self.assertNotIn("a harness builtin, never", flat)

    def test_the_router_is_never_offered_its_own_skill(self) -> None:
        # It IS in the index once installed; omission from the index is not the guard.
        own = {"name": "skill-router", "kind": "meta", "type": "skill",
               "description": "routes prompts", "use_when": ["routing"]}
        _, keymap = jev_choose.build_questions([*ENTRIES, own])
        self.assertNotIn("skill-router", {n for m in keymap.values() for n in m.values()})

    def test_every_question_has_a_none_option(self) -> None:
        questions, _ = jev_choose.build_questions(ENTRIES)
        for qid in ("domain_0", "process"):
            self.assertIn("none", questions[qid]["criteria"])
            self.assertEqual(questions[qid]["type"], "choice")

    def test_large_index_is_split_not_truncated(self) -> None:
        many = [{"name": f"skill-{i}", "kind": "domain", "type": "skill",
                 "description": f"does thing {i}", "use_when": []} for i in range(600)]
        questions, keymap = jev_choose.build_questions(many)
        domain_qs = [q for q in questions if q.startswith("domain_")]
        self.assertEqual(len(domain_qs), 3)
        for q in domain_qs:
            self.assertLessEqual(len(questions[q]["criteria"]), jev_choose.MAX_OPTIONS)
        named = {n for q in domain_qs for n in keymap[q].values()}
        self.assertEqual(len(named), 600)


class TestTiers(unittest.TestCase):
    def test_thresholds(self) -> None:
        self.assertEqual(jev_choose.tier(0.8), "route")
        self.assertEqual(jev_choose.tier(0.95), "route")
        self.assertEqual(jev_choose.tier(0.79), "suggest")
        self.assertEqual(jev_choose.tier(0.5), "suggest")
        self.assertEqual(jev_choose.tier(0.49), "silent")


class TestChoose(JevCase):
    def test_keys_map_back_to_index_names(self) -> None:
        _, keymap = jev_choose.build_questions(ENTRIES)
        dkey = next(k for k, n in keymap["domain_0"].items() if n == "mac-doctor")
        pkey = next(k for k, n in keymap["process"].items()
                    if n == "superpowers:systematic-debugging")
        self.transport(answer(dkey, 0.91, pkey, 0.62, "BROKEN", 0.88))
        got = jev_choose.choose("my mac keeps restaring randomly", entries=ENTRIES)
        self.assertIsNotNone(got)
        self.assertEqual((got.domain.name, got.domain.tier), ("mac-doctor", "route"))
        self.assertEqual((got.process.name, got.process.tier),
                         ("superpowers:systematic-debugging", "suggest"))
        self.assertEqual(got.path, "BROKEN")
        self.assertEqual(got.tokens, 1234)
        self.assertEqual(self.calls[0]["model"], "jev-1.13.0")

    def test_none_is_a_real_answer(self) -> None:
        self.transport(answer("none", 0.97, "none", 0.9))
        got = jev_choose.choose("thanks, that looks great to me", entries=ENTRIES)
        self.assertIsNone(got.domain.name)
        self.assertEqual(got.domain.tier, "route")      # confidently nothing
        self.assertIsNone(got.process.name)

    def test_unknown_key_is_never_trusted(self) -> None:
        self.transport(answer("d999", 0.99, "mac-doctor", 0.99))
        got = jev_choose.choose("something about a mac please", entries=ENTRIES)
        self.assertIsNone(got.domain.name)
        self.assertEqual(got.domain.tier, "silent")
        self.assertIsNone(got.process.name)             # a name is not a key
        self.assertEqual(got.process.tier, "silent")

    def test_context_travels_in_state(self) -> None:
        self.transport(answer("none", 0.9, "none", 0.9))
        jev_choose.choose("yes please continue", context="Shall I run the design review?",
                          entries=ENTRIES)
        state = self.calls[0]["state"]
        self.assertEqual(state["request"], "yes please continue")
        self.assertEqual(state["previous_assistant_message"], "Shall I run the design review?")
        jev_choose.choose("a different prompt entirely here", entries=ENTRIES)
        self.assertNotIn("previous_assistant_message", self.calls[1]["state"])

    def test_failures_return_none(self) -> None:
        for reply in (None, {}, {"answers": {}}, {"answers": {"domain_0": "junk"}}):
            self.transport(reply)
            self.assertIsNone(jev_choose.choose(f"prompt for {reply!r} case", entries=ENTRIES))

    def test_no_key_or_disabled_makes_no_call(self) -> None:
        self.transport(answer("none", 0.9, "none", 0.9))
        jev_choose._key = lambda: ""
        self.assertIsNone(jev_choose.choose("no key for this prompt", entries=ENTRIES))
        jev_choose._key = lambda: "test-key"
        os.environ["SKILL_ROUTER_JEV"] = "0"
        self.assertIsNone(jev_choose.choose("disabled for this prompt", entries=ENTRIES))
        self.assertEqual(self.calls, [])

    def test_cached_by_prompt_context_and_index(self) -> None:
        _, keymap = jev_choose.build_questions(ENTRIES)
        dkey = next(k for k, n in keymap["domain_0"].items() if n == "design-review")
        self.transport(answer(dkey, 0.85, "none", 0.9))
        a = jev_choose.choose("audit the ui of the settings page", entries=ENTRIES)
        b = jev_choose.choose("audit the ui of the settings page", entries=ENTRIES)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(a.cached)
        self.assertTrue(b.cached)
        self.assertEqual(b.domain.name, "design-review")
        jev_choose.choose("audit the ui of the settings page", context="x", entries=ENTRIES)
        jev_choose.choose("audit the ui of the settings page", entries=ENTRIES[:3])   # a different index
        self.assertEqual(len(self.calls), 3)

    def test_failures_are_not_cached(self) -> None:
        self.transport(None)
        self.assertIsNone(jev_choose.choose("flaky network on this one", entries=ENTRIES))
        self.transport(answer("none", 0.9, "none", 0.9))
        self.assertIsNotNone(jev_choose.choose("flaky network on this one", entries=ENTRIES))

    def test_hard_deadline(self) -> None:
        def slow(body: dict):
            time.sleep(1.5)
            return answer("none", 0.9, "none", 0.9)
        self.transport(slow)
        t = time.time()
        got = jev_choose.choose("this one hangs upstream", entries=ENTRIES, timeout=0.2)
        self.assertIsNone(got)
        self.assertLess(time.time() - t, 1.0)
        self.assertEqual(jev_choose.LAST_FAILURE, "timeout")
        self.transport(None)                               # a fast failure is an error, not a timeout
        self.assertIsNone(jev_choose.choose("this one errors at once", entries=ENTRIES, timeout=5))
        self.assertEqual(jev_choose.LAST_FAILURE, "error")

    def test_split_index_takes_the_strongest_non_none(self) -> None:
        many = [{"name": f"skill-{i}", "kind": "domain", "type": "skill",
                 "description": f"does thing {i}", "use_when": []} for i in range(300)]
        _, keymap = jev_choose.build_questions(many)
        k1 = next(iter(keymap["domain_1"]))

        def reply(body: dict) -> dict:
            out = answer("none", 0.9, "none", 0.9)
            out["answers"]["domain_1"] = {"type": "choice", "choice": k1, "confidence": 0.88,
                                          "probabilities": {k1: 0.9}}
            return out
        self.transport(reply)
        got = jev_choose.choose("please do thing two hundred sixty", entries=many)
        self.assertEqual(got.domain.name, keymap["domain_1"][k1])
        self.assertEqual(got.domain.tier, "route")

    def test_two_chunks_disagreeing_is_only_a_suggestion(self) -> None:
        many = [{"name": f"skill-{i}", "kind": "domain", "type": "skill",
                 "description": f"does thing {i}", "use_when": []} for i in range(300)]
        _, keymap = jev_choose.build_questions(many)
        k0, k1 = next(iter(keymap["domain_0"])), next(iter(keymap["domain_1"]))

        def reply(body: dict) -> dict:
            out = answer(k0, 0.86, "none", 0.9)
            out["answers"]["domain_1"] = {"type": "choice", "choice": k1, "confidence": 0.9,
                                          "probabilities": {k1: 0.9}}
            return out
        self.transport(reply)
        got = jev_choose.choose("ambiguous across the two halves", entries=many)
        self.assertEqual(got.domain.name, keymap["domain_1"][k1])
        self.assertEqual(got.domain.tier, "suggest")


def with_tier(reply: dict, choice: str, conf: float) -> dict:
    reply["answers"]["tier"] = {"type": "choice", "choice": choice, "confidence": conf,
                                "probabilities": {choice: conf}}
    return reply


class TestWorkTier(JevCase):
    """tier → model. Conservative: only a >= 0.8 light/standard leaves inherit."""

    def test_tier_question_is_asked_with_the_others(self) -> None:
        questions, _ = jev_choose.build_questions(ENTRIES)
        self.assertEqual(sorted(questions["tier"]["criteria"]), ["heavy", "light", "standard"])

    def test_confident_light_is_haiku(self) -> None:
        self.transport(with_tier(answer("none", 0.9, "none", 0.9), "light", 0.93))
        got = jev_choose.choose("list every file that imports requests", entries=ENTRIES)
        self.assertEqual((got.work.name, got.work.tier, got.model), ("light", "route", "haiku"))

    def test_confident_standard_is_sonnet(self) -> None:
        self.transport(with_tier(answer("none", 0.9, "none", 0.9), "standard", 0.84))
        got = jev_choose.choose("add a unit test for parse_date", entries=ENTRIES)
        self.assertEqual(got.model, "sonnet")

    def test_heavy_and_unsure_both_inherit(self) -> None:
        self.transport(with_tier(answer("none", 0.9, "none", 0.9), "heavy", 0.95))
        self.assertEqual(jev_choose.choose("redesign the auth flow", entries=ENTRIES).model, "inherit")
        self.calls.clear()
        self.transport(with_tier(answer("none", 0.9, "none", 0.9), "light", 0.71))
        got = jev_choose.choose("tidy this up a bit", entries=ENTRIES)
        self.assertEqual((got.work.tier, got.model), ("suggest", "inherit"))

    def test_missing_or_junk_tier_answer_inherits(self) -> None:
        self.transport(answer("none", 0.9, "none", 0.9))                   # no tier at all
        self.assertEqual(jev_choose.choose("whatever", entries=ENTRIES).model, "inherit")
        self.calls.clear()
        self.transport(with_tier(answer("none", 0.9, "none", 0.9), "gigantic", 0.99))
        self.assertEqual(jev_choose.choose("whatever else", entries=ENTRIES).model, "inherit")

    def test_old_cache_entries_without_work_still_load(self) -> None:
        self.transport(with_tier(answer("none", 0.9, "none", 0.9), "light", 0.9))
        jev_choose.choose("grep for TODO", entries=ENTRIES)
        for p in jev_choose.CACHE.glob("*.json"):
            d = json.loads(p.read_text())
            d.pop("work", None)
            p.write_text(json.dumps(d))
        got = jev_choose.choose("grep for TODO", entries=ENTRIES)
        self.assertTrue(got.cached)
        self.assertEqual(got.model, "inherit")

    def test_tier_only_is_one_small_cached_call(self) -> None:
        self.transport(with_tier({"answers": {}}, "light", 0.88))
        pick = jev_choose.tier_only("find all call sites of fetchUser")
        self.assertEqual((pick.name, pick.tier), ("light", "route"))
        self.assertEqual(jev_choose.model_for(pick), "haiku")
        self.assertEqual(sorted(self.calls[0]["questions"]), ["tier"])
        self.assertEqual(self.calls[0]["state"], {"request": "find all call sites of fetchUser"})
        again = jev_choose.tier_only("find all call sites of fetchUser")
        self.assertEqual(len(self.calls), 1)                                # cache hit
        self.assertEqual(again.name, "light")

    def test_tier_only_failures_are_none_and_uncached(self) -> None:
        self.transport(lambda body: None)
        self.assertIsNone(jev_choose.tier_only("anything"))
        self.assertEqual(list(jev_choose.CACHE.glob("*.json")), [])
        self.assertEqual(jev_choose.model_for(None), "inherit")


if __name__ == "__main__":
    unittest.main()
