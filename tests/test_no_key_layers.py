#!/usr/bin/env python3
"""Layer 1 (typo correction) and layer 4 (shortlist) burden rules. Offline, no index needed."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import index_match as im  # type: ignore[import-not-found]
import router  # type: ignore[import-not-found]


class FakeIdx:
    def __init__(self, words: dict[str, int]):
        self.df = words
        lens: dict[int, list[str]] = {}
        for w in words:
            lens.setdefault(len(w), []).append(w)
        self.by_len = {n: tuple(v) for n, v in lens.items()}


class TestTypoCorrection(unittest.TestCase):
    idx = FakeIdx({"framework": 3, "database": 9, "integration": 2, "consulting": 1, "house": 5,
                   "use": 9, "notion": 4, "deploy": 6})

    def test_osa_counts_an_adjacent_swap_as_one(self) -> None:
        self.assertEqual(im._osa("intgeartion", "integration", 2), 2)
        self.assertEqual(im._osa("rfamework", "framework", 2), 1)
        self.assertEqual(im._osa("abc", "abcdefgh", 2), 3, "gives up past the limit")

    def test_known_and_short_words_are_left_alone(self) -> None:
        self.assertEqual(im.correct(["notion", "use", "ab"], self.idx), {})

    def test_typos_map_to_index_words(self) -> None:
        got = im.correct(["databsae", "deplyo", "framwork", "integartion", "rfamework"], self.idx)
        self.assertEqual(got, {"databsae": "database", "deplyo": "deploy", "framwork": "framework",
                               "integartion": "integration", "rfamework": "framework"})

    def test_first_letter_is_not_substituted(self) -> None:
        self.assertNotIn("rouse", im.correct(["rouse"], self.idx), "rouse must not become house")

    def test_can_be_switched_off(self) -> None:
        with patch.object(im, "FUZZY", False):
            self.assertEqual(im.correct(["databsae"], self.idx), {})


class M:
    def __init__(self, name: str, score: float, kind: str = "domain"):
        self.name, self.score, self.kind = name, score, kind


class TestShortlistBurden(unittest.TestCase):
    def setUp(self) -> None:
        router._entry_types.cache_clear()
        self.ranked = [M("skill-router", 30), M("superpowers:x", 25, "process"), M("loaded-one", 20),
                       M("a", 15), M("builtin-x", 14), M("b", 12), M("c", 9), M("d", 8), M("e", 1)]
        self.env = patch.dict(os.environ, {"SKILL_ROUTER_SHORTLIST": "1"})
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        router._entry_types.cache_clear()

    def _line(self, prompt: str = "please build the notion docs system", path: str = "BUILD", loaded=frozenset()):
        with patch.object(im, "rank", return_value=self.ranked), \
             patch.object(router, "_semantic_order", return_value=None), \
             patch.object(router, "_entry_types", return_value={"builtin-x": "builtin"}):
            return router.shortlist_line(prompt, path, loaded)

    def test_off_by_default_without_the_daemon(self) -> None:
        with patch.dict(os.environ, {"SKILL_ROUTER_SHORTLIST": ""}):
            os.environ.pop("SKILL_ROUTER_SHORTLIST")
            self.assertEqual(self._line(), "")

    def test_off_by_default_even_when_the_daemon_answers(self) -> None:
        with patch.dict(os.environ, {"SKILL_ROUTER_SHORTLIST": ""}), \
             patch.object(im, "rank", return_value=[M("a", 15)]), \
             patch.object(router, "_semantic_order", return_value=["b"]), \
             patch.object(router, "_entry_types", return_value={"a": "skill", "b": "skill"}):
            os.environ.pop("SKILL_ROUTER_SHORTLIST")
            self.assertEqual(router.shortlist_line("please build the notion docs system", "BUILD"), "")

    def test_auto_opt_in_needs_the_daemon(self) -> None:
        with patch.dict(os.environ, {"SKILL_ROUTER_SHORTLIST": "auto"}), \
             patch.object(im, "rank", return_value=[M("a", 15)]), \
             patch.object(router, "_entry_types", return_value={"a": "skill", "b": "skill"}):
            with patch.object(router, "_semantic_order", return_value=["b"]):
                self.assertTrue(router.shortlist_line("please build the notion docs system", "BUILD")
                                .endswith(": a · b"))
            with patch.object(router, "_semantic_order", return_value=None):
                self.assertEqual(router.shortlist_line("please build the notion docs system", "BUILD"), "")

    def test_three_names_skipping_self_process_loaded_and_builtins(self) -> None:
        line = self._line(loaded=frozenset({"loaded-one"}))
        self.assertTrue(line.endswith(": a · b · c"), line)
        self.assertLess(len(line), 160, "one short line")

    def test_semantic_ranks_are_fused_when_the_daemon_answers(self) -> None:
        with patch.object(im, "rank", return_value=[M("a", 15), M("b", 12)]), \
             patch.object(router, "_semantic_order", return_value=["z", "b", "skill-router"]), \
             patch.object(router, "_entry_types", return_value={"a": "skill", "b": "skill", "z": "skill"}):
            self.assertEqual([n for n, _ in router.shortlist_candidates("x")], ["b", "a", "z"])

    def test_floor_keeps_weak_matches_out(self) -> None:
        self.ranked = [M("weak", 2.0)]
        self.assertEqual(self._line(), "")

    def test_questions_and_chat_get_nothing(self) -> None:
        self.assertEqual(self._line("so multiple process make a workflow ?", "QUESTION"), "")
        self.assertEqual(self._line("hello there", "NONE"), "")

    def test_same_names_are_not_repeated_within_a_session(self) -> None:
        with tempfile.TemporaryDirectory() as d, patch.object(router, "SESSION_DIR", Path(d)):
            self.assertFalse(router._shortlist_recent("s1", ["a", "b"]))
            self.assertTrue(router._shortlist_recent("s1", ["b", "a"]))
            self.assertFalse(router._shortlist_recent("s2", ["a", "b"]), "per session")


if __name__ == "__main__":
    unittest.main()


class TestJevRanked(unittest.TestCase):
    def test_ranked_merges_chunks_by_probability_and_drops_unknown_keys(self) -> None:
        import jev_choose as jc  # type: ignore[import-not-found]
        keymap = {"domain_0": {"d0": "a", "d1": "b"}, "domain_1": {"d2": "c"}, "process": {"p0": "x"}}
        answers = {"domain_0": {"choice": "d0", "probabilities": {"d0": 0.5, "d1": 0.3, "none": 0.2, "zz": 0.9}},
                   "domain_1": {"choice": "none", "probabilities": {"d2": 0.4, "none": 0.6}},
                   "process": {"choice": "p0", "probabilities": {"p0": 1.0}}}
        self.assertEqual(jc._ranked(answers, keymap), ("a", "c", "b"))
        self.assertEqual(jc._ranked({}, keymap), ())


class FakeJev:
    def __init__(self, ranked, path="OPERATE", path_confidence=0.9):
        self.ranked, self.path, self.path_confidence = ranked, path, path_confidence


class TestJevShortlist(unittest.TestCase):
    def setUp(self) -> None:
        router._entry_types.cache_clear()
        self.p = [patch.object(router, "_entry_types", return_value={"bi": "builtin"}),
                  patch.object(router, "valid_skill", return_value=True),
                  patch.object(router, "is_deferred", return_value=False),
                  patch.object(router, "SESSION_ID", ""),
                  patch.object(router, "LOADED", frozenset({"loaded"}))]
        for x in self.p:
            x.start()

    def tearDown(self) -> None:
        for x in self.p:
            x.stop()
        router._entry_types.cache_clear()

    def test_top_three_filtered(self) -> None:
        line = router.jev_shortlist_line("clean up my notion pages", "OPERATE",
                                         FakeJev(("skill-router", "bi", "loaded", "a", "b", "c", "d")))
        self.assertTrue(line.endswith(": a · b · c"), line)

    def test_chat_gets_nothing_unless_jev_is_sure_it_is_work(self) -> None:
        unsure = FakeJev(("a", "b"), "OPERATE", 0.68)
        self.assertEqual(router.jev_shortlist_line("toggles on", "SKIP", unsure), "")
        sure = FakeJev(("a", "b"), "OPERATE", 0.9)
        self.assertNotEqual(router.jev_shortlist_line("toggles on", "SKIP", sure), "")

    def test_long_plugin_names_keep_the_line_within_200_chars(self) -> None:
        long = ("superpowers:subagent-driven-development", "superpowers:verification-before-completion",
                "superpowers:finishing-a-development-branch")
        line = router.jev_shortlist_line("clean up my notion pages", "OPERATE", FakeJev(long))
        self.assertTrue(line, "at least one name fits")
        self.assertLessEqual(len(line), router.LINE_MAX)
        self.assertIn(long[0], line)
        self.assertEqual(router._shown_from_output([], line)[1], [n for n in long if n in line])

    def test_no_line_when_not_even_one_name_fits(self) -> None:
        self.assertEqual(router.jev_shortlist_line("clean up my notion pages", "OPERATE",
                                                   FakeJev(("x" * 200,))), "")

    def test_skips_a_name_already_printed_as_possible_fit(self) -> None:
        line = router.jev_shortlist_line("clean up my notion pages", "OPERATE",
                                         FakeJev(("a", "b", "c", "d")), exclude=frozenset({"a"}))
        self.assertTrue(line.endswith(": b · c · d"), line)

    def test_can_be_switched_off(self) -> None:
        with patch.dict(os.environ, {"SKILL_ROUTER_JEV_SHORTLIST": "0"}):
            self.assertEqual(router.jev_shortlist_line("clean up notion", "OPERATE", FakeJev(("a",))), "")


class TestSemanticClient(unittest.TestCase):
    def test_absent_daemon_is_none_not_an_error(self) -> None:
        import embedder_client as ec  # type: ignore[import-not-found]
        with patch.object(ec, "SOCKET_PATH", Path(tempfile.mkdtemp()) / "none.sock"):
            self.assertIsNone(ec.rank_skills("anything"))

    def test_fusion_keeps_both_lists_wins(self) -> None:
        self.assertEqual(router._rrf(["a", "b", "c"], ["c", "a", "d"])[:2], ["a", "c"])


class TestWorkSignal(unittest.TestCase):
    """The shortlist gate: question-phrased work gets through, chat and concept questions do not."""

    def test_imperatives_are_work(self) -> None:
        for p in ("deploy the marketing site", "please add a pricing page to the app",
                  "Sure, rename the export helper to something clearer",
                  "Option 2: move the cron job into the worker, then rerun the suite",
                  "commit the staged changes with a clear message",
                  "could u bump the version in the manifest", "just do the changelog entry",
                  "keep the old endpoint for one more release",
                  "can we wire the metrics exporter into staging",
                  "That looks right. Update the README with the new flags."):
            self.assertEqual(im.work_signal(p), 1.0, p)

    def test_concept_questions_stay_silent(self) -> None:
        for p in ("what is a webhook?", "Tenants get a separate database. - mean a separate server?",
                  "ok noted", "say hello", "Reply with exactly: YES",
                  "so many small steps make a routine ?",
                  "a routine is the bigger picture and a step is the small level",
                  "what's the difference between a queue and a topic?", "", "thanks!",
                  "Plan looks fine to me.", "Use case is different here, I was just curious",
                  "I tried that. Keep in mind it was late.", "how does the router pick a skill?",
                  "is this a good plan?", "why do people like this tool so much?",
                  "how do lighthouses keep ships safe at night?",
                  "Start of the week was slow, honestly.", "what does idempotent mean?",
                  "is it raining where you are?", "why is the sky blue?",
                  "who wrote the first compiler?", "Map of the city is on the fridge."):
            self.assertLess(im.work_signal(p), 0.6, p)
            self.assertLessEqual(im.work_signal(p), 0.3, p)

    def test_broken_beats_concept_words(self) -> None:
        self.assertEqual(im.work_signal("the deploy fails on CI, which means nobody can release"), 0.8)

    def test_question_phrased_work_request_gets_shortlist_not_card(self) -> None:
        for p in ("would the billing dashboard hold up with ten thousand tenants?",
                  "does the weather api offer an integration with our scheduler?",
                  "which hosting platform suits the photo gallery better?",
                  "should the recipe website get its own database?"):
            self.assertGreaterEqual(im.work_signal(p), 0.6, p)
            self.assertNotIn(im.detect_path(p), ("BROKEN", "BUILD", "OPERATE"), "no route card: " + p)
        p = "should the recipe website get its own database?"
        with patch.dict(os.environ, {"SKILL_ROUTER_SHORTLIST": "1"}), \
             patch.object(router, "JEV_ANSWERED", False), \
             patch.object(im, "rank", return_value=[M("a", 15), M("b", 12)]), \
             patch.object(router, "_semantic_order", return_value=None), \
             patch.object(router, "_entry_types", return_value={}):
            self.assertTrue(router.shortlist_line(p, im.detect_path(p)).endswith(": a · b"))
            self.assertEqual(router.shortlist_line("what is a webhook?", "QUESTION"), "")

    def test_jev_list_follows_the_work_signal_or_a_sure_jev(self) -> None:
        unsure = FakeJev(("a", "b"), "OPERATE", 0.6)
        with patch.object(router, "_entry_types", return_value={}), \
             patch.object(router, "valid_skill", return_value=True), \
             patch.object(router, "is_deferred", return_value=False), \
             patch.object(router, "SESSION_ID", ""), patch.object(router, "LOADED", frozenset()):
            self.assertNotEqual(router.jev_shortlist_line(
                "should the recipe website get its own database?", "NONE", unsure), "")
            self.assertEqual(router.jev_shortlist_line("what is a webhook?", "QUESTION", unsure), "")
            self.assertNotEqual(router.jev_shortlist_line("what is a webhook?", "QUESTION",
                                                          FakeJev(("a",), "BUILD", 0.85)), "")


class TestKeylessLineLength(unittest.TestCase):
    def test_long_plugin_names_keep_the_keyless_line_within_200_chars(self) -> None:
        long = ["superpowers:subagent-driven-development", "superpowers:verification-before-completion",
                "superpowers:finishing-a-development-branch"]
        with patch.dict(os.environ, {"SKILL_ROUTER_SHORTLIST": "1"}), \
             patch.object(router, "JEV_ANSWERED", False), \
             patch.object(router, "_work_signal", return_value=1.0), \
             patch.object(router, "shortlist_candidates", return_value=[(n, 15.0) for n in long]):
            line = router.shortlist_line("deploy the marketing site", "OPERATE")
            self.assertTrue(line)
            self.assertLessEqual(len(line), router.LINE_MAX)
            self.assertEqual(router._shown_from_output([], line)[1], [n for n in long if n in line])
        with patch.dict(os.environ, {"SKILL_ROUTER_SHORTLIST": "1"}), \
             patch.object(router, "JEV_ANSWERED", False), \
             patch.object(router, "_work_signal", return_value=1.0), \
             patch.object(router, "shortlist_candidates", return_value=[("y" * 200, 15.0)]):
            self.assertEqual(router.shortlist_line("deploy the marketing site", "OPERATE"), "")


class TestKeylessIsAJevFallback(unittest.TestCase):
    """The keyless list speaks only when Jev did not answer this prompt."""

    def _line(self) -> str:
        with patch.dict(os.environ, {"SKILL_ROUTER_SHORTLIST": "1"}), \
             patch.object(im, "rank", return_value=[M("a", 15)]), \
             patch.object(router, "_semantic_order", return_value=None), \
             patch.object(router, "_entry_types", return_value={}):
            return router.shortlist_line("deploy the marketing site", "OPERATE")

    def _decide(self, got, failure: str = ""):
        import jev_choose  # type: ignore[import-not-found]
        with patch.object(router, "_jev_active", return_value=True), \
             patch.object(jev_choose, "choose", return_value=got), \
             patch.object(jev_choose, "LAST_FAILURE", failure, create=True):
            router._jev_decide("deploy the marketing site")

    def test_silent_when_jev_answered_even_with_nothing_printed(self) -> None:
        with patch.object(router, "JEV_ANSWERED", False):
            self._decide(FakeJev(()))
            self.assertTrue(router.JEV_ANSWERED)
            self.assertEqual(self._line(), "")

    def test_speaks_when_jev_failed_or_timed_out(self) -> None:
        with patch.object(router, "JEV_ANSWERED", True):
            self._decide(None, "timeout")
            self.assertFalse(router.JEV_ANSWERED)
            self.assertTrue(self._line().endswith(": a"))

    def test_route_resets_the_flag_each_prompt(self) -> None:
        with patch.object(router, "JEV_ANSWERED", True), \
             patch.object(router, "_is_question", return_value=True):
            router.route("what is a webhook?")
            self.assertFalse(router.JEV_ANSWERED)
