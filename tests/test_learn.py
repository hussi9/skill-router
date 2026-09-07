#!/usr/bin/env python3
"""
Tests for the learner and the session brief.

Every learned association here is checked against synthetic logs written into
a temp HOME, so the assertions say what the algorithms do, not what this
machine's history happens to contain. The thresholds are part of the
contract: a pattern seen twice must not become a suggestion.

Run: python3 -m pytest tests/test_learn.py -q
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
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import learn  # type: ignore[import-not-found]
import session_brief  # type: ignore[import-not-found]


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


def _events(*specs: tuple) -> list[dict]:
    """(seconds_ago, type, fields...) → event dicts with _t set, sorted."""
    now = time.time()
    out = []
    for secs, typ, fields in specs:
        e = {"type": typ, "ts": _iso(now - secs), "_t": now - secs, **fields}
        out.append(e)
    return sorted(out, key=lambda e: e["_t"])


def _inv(secs: float, skill: str, session: str, prompt: str = "p") -> tuple:
    return (secs, "invoke", {"skill": skill, "session_id": session, "prompt_id": prompt})


class TestSessions(unittest.TestCase):

    def test_session_ids_group_exactly(self) -> None:
        evs = _events(_inv(300, "a", "s1"), _inv(200, "b", "s1"), _inv(100, "a", "s2"))
        invs = learn.invocations(evs, legacy=[])
        sessions = learn.sessionize(invs)
        self.assertEqual(sorted(len(s) for s in sessions), [1, 2])

    def test_legacy_gap_splits_sessions(self) -> None:
        now = time.time()
        gap = learn.SESSION_GAP_MIN * 60
        legacy = [{"_t": now - 3 * gap, "skill": "a"},
                  {"_t": now - 3 * gap + 60, "skill": "b"},
                  {"_t": now - 60, "skill": "c"}]
        sessions = learn.sessionize(learn.invocations([], legacy))
        self.assertEqual(sorted(len(s) for s in sessions), [1, 2])

    def test_structured_events_supersede_overlapping_legacy(self) -> None:
        """The usage log and the invoke event record the same call. Once
        structured events exist, legacy lines from that era are duplicates."""
        evs = _events(_inv(100, "a", "s1"))
        t_structured = evs[0]["_t"]   # anchor on the event's own clock, not a second time.time()
        legacy = [{"_t": t_structured - 4900, "skill": "old"},
                  {"_t": t_structured, "skill": "a"}]
        invs = learn.invocations(evs, legacy)
        self.assertEqual([i["skill"] for i in invs], ["old", "a"])


class TestHandovers(unittest.TestCase):

    def _sessions(self, pairs: int, noise: int = 0) -> list[list[dict]]:
        specs = []
        t = 10_000
        for i in range(pairs):
            specs += [_inv(t, "plan", f"s{i}"), _inv(t - 60, "tdd", f"s{i}")]
            t -= 1000
        for i in range(noise):
            specs += [_inv(t, "plan", f"n{i}"), _inv(t - 60, f"other{i}", f"n{i}")]
            t -= 1000
        return learn.sessionize(learn.invocations(_events(*specs), []))

    def test_habit_clears_thresholds(self) -> None:
        h = learn.learn_handovers(self._sessions(pairs=4))
        self.assertEqual(h["plan"][0]["to"], "tdd")
        self.assertEqual(h["plan"][0]["n"], 4)
        self.assertEqual(h["plan"][0]["p"], 1.0)

    def test_two_occurrences_is_a_coincidence(self) -> None:
        self.assertEqual(learn.learn_handovers(self._sessions(pairs=2)), {})

    def test_diluted_habit_is_dropped(self) -> None:
        """plan → tdd 3 times, plan → seven different things 7 times: 30%."""
        h = learn.learn_handovers(self._sessions(pairs=3, noise=7))
        self.assertNotIn("plan", h)

    def test_meta_skill_never_a_handover(self) -> None:
        specs = []
        for i in range(4):
            specs += [_inv(9000 - i * 1000, "skill-router", f"s{i}"),
                      _inv(8900 - i * 1000, "plan", f"s{i}")]
        h = learn.learn_handovers(learn.sessionize(learn.invocations(_events(*specs), [])))
        self.assertNotIn("skill-router", h)


class TestChains(unittest.TestCase):

    def test_recurring_sequence_counted_once_per_session(self) -> None:
        specs = []
        for i in range(3):
            base = 9000 - i * 1000
            specs += [_inv(base, "plan", f"s{i}"), _inv(base - 10, "tdd", f"s{i}"),
                      _inv(base - 20, "tdd", f"s{i}"), _inv(base - 30, "verify", f"s{i}")]
        chains = learn.learn_chains(learn.sessionize(learn.invocations(_events(*specs), [])))
        steps = {tuple(c["steps"]): c["n"] for c in chains}
        self.assertEqual(steps.get(("plan", "tdd", "verify")), 3)
        self.assertEqual(steps.get(("plan", "tdd")), 3, "prefix really did happen 3 times")
        self.assertNotIn(("tdd", "tdd"), steps, "consecutive repeats collapse")

    def test_below_support_is_not_a_chain(self) -> None:
        specs = [_inv(9000, "a", "s0"), _inv(8990, "b", "s0"),
                 _inv(8000, "a", "s1"), _inv(7990, "b", "s1")]
        self.assertEqual(learn.learn_chains(learn.sessionize(learn.invocations(_events(*specs), []))), [])


class TestTriggers(unittest.TestCase):

    def _corpus(self, n_signal: int, n_noise: int) -> tuple[list[dict], list[dict]]:
        specs = []
        t = 9000
        for i in range(n_signal):
            specs.append((t, "prompt", {"session_id": f"s{i}", "prompt_id": "p",
                                        "tokens": ["testflight", "submit"]}))
            specs.append(_inv(t - 5, "scrollbook-deploy", f"s{i}"))
            t -= 100
        for i in range(n_noise):
            specs.append((t, "prompt", {"session_id": f"n{i}", "prompt_id": "p",
                                        "tokens": ["submit", "form"]}))
            specs.append(_inv(t - 5, "frontend-design", f"n{i}"))
            t -= 100
        evs = _events(*specs)
        return evs, learn.invocations(evs, [])

    def test_distinctive_keyword_learns_a_trigger(self) -> None:
        evs, invs = self._corpus(n_signal=4, n_noise=4)
        triggers = {(t["token"], t["skill"]): t for t in learn.learn_triggers(evs, invs)}
        self.assertIn(("testflight", "scrollbook-deploy"), triggers)
        self.assertEqual(triggers[("testflight", "scrollbook-deploy")]["precision"], 1.0)

    def test_ambiguous_keyword_does_not(self) -> None:
        """'submit' precedes two different skills equally: precision 0.5 < 0.6."""
        evs, invs = self._corpus(n_signal=4, n_noise=4)
        pairs = {(t["token"], t["skill"]) for t in learn.learn_triggers(evs, invs)}
        self.assertNotIn(("submit", "scrollbook-deploy"), pairs)
        self.assertNotIn(("submit", "frontend-design"), pairs)

    def test_support_threshold(self) -> None:
        evs, invs = self._corpus(n_signal=2, n_noise=0)
        self.assertEqual(learn.learn_triggers(evs, invs), [])


class TestFollowRates(unittest.TestCase):

    def test_joins_on_session_and_prompt_ids(self) -> None:
        evs = _events(
            (300, "chain-start", {"steps": ["plan"], "session_id": "s1", "prompt_id": "p1"}),
            _inv(200, "plan", "s1", "p1"),
            (100, "chain-start", {"steps": ["plan"], "session_id": "s2", "prompt_id": "p2"}),
        )
        per = learn.learn_follow_rates(evs, learn.invocations(evs, []))
        self.assertEqual(per["plan"]["announcements"], 2)
        self.assertEqual(per["plan"]["follow_rate"], 0.5)

    def test_unstamped_announcements_after_structured_era_are_ignored(self) -> None:
        """A doctor smoke prompt or a test run must not count as an
        announcement you ignored."""
        evs = _events(
            (300, "chain-start", {"steps": ["plan"], "session_id": "s1", "prompt_id": "p1"}),
            _inv(290, "plan", "s1", "p1"),
            (50, "chain-start", {"steps": ["plan"]}),   # unstamped, later: a probe
        )
        per = learn.learn_follow_rates(evs, learn.invocations(evs, []))
        self.assertEqual(per["plan"]["announcements"], 1)
        self.assertEqual(per["plan"]["follow_rate"], 1.0)


class TestDiscoveryAndOnline(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="learn-"))
        self._real = (learn.CATALOG, learn.ONLINE_CATALOG, learn.LEARNED)
        learn.CATALOG = self.tmp / "catalog.json"
        learn.ONLINE_CATALOG = self.tmp / "online.json"
        learn.LEARNED = self.tmp / "learned.json"

    def tearDown(self) -> None:
        learn.CATALOG, learn.ONLINE_CATALOG, learn.LEARNED = self._real

    def _catalog(self, names: list[str]) -> None:
        learn.CATALOG.write_text(json.dumps({"entries": [
            {"name": n, "invokable": True, "description": f"about {n}"} for n in names]}))

    def test_first_run_reports_nothing_new(self) -> None:
        self._catalog(["a", "b"])
        d = learn.learn_discovery({}, "2026-09-07T00:00:00")
        self.assertEqual(d["discovered"]["new"], [])
        self.assertEqual(sorted(d["catalog_snapshot"]), ["a", "b"])

    def test_new_skill_is_detected_and_first_seen_persists(self) -> None:
        self._catalog(["a", "b", "c"])
        prev = {"catalog_snapshot": ["a", "b"]}
        d = learn.learn_discovery(prev, "2026-09-07T00:00:00")
        self.assertEqual([x["name"] for x in d["discovered"]["new"]], ["c"])
        # Second run: still new, same first_seen, not re-stamped.
        d2 = learn.learn_discovery({**d, "discovered": d["discovered"]}, "2026-09-08T00:00:00")
        self.assertEqual(d2["discovered"]["new"][0]["first_seen"], "2026-09-07T00:00:00")

    def test_alias_of_a_present_plugin_skill_is_not_a_removal(self) -> None:
        self._catalog(["superpowers:brainstorming"])
        prev = {"catalog_snapshot": ["superpowers:brainstorming", "brainstorming", "superpowers"]}
        d = learn.learn_discovery(prev, "2026-09-07T00:00:00")
        self.assertEqual(d["discovered"]["removed"], [])

    def test_underscore_internals_are_not_skills(self) -> None:
        self._catalog(["vercel:_conventions", "real"])
        prev = {"catalog_snapshot": ["real"]}
        d = learn.learn_discovery(prev, "2026-09-07T00:00:00")
        self.assertEqual(d["discovered"]["new"], [])

    def test_online_suggestions_wait_for_prompt_signal(self) -> None:
        self._catalog(["a"])
        learn.ONLINE_CATALOG.write_text(json.dumps({"catalogs": {"x": [
            {"name": "stripe-webhooks", "description": "stripe checkout webhooks payments"}]}}))
        evs = _events(*[_inv(1000 - i, "plan", f"s{i}") for i in range(10)])
        self.assertEqual(learn.learn_online(evs, learn.invocations(evs, [])), [],
            "skill names alone are not a profile of your work")

    def test_online_suggestion_matches_prompt_profile(self) -> None:
        self._catalog(["a"])
        learn.ONLINE_CATALOG.write_text(json.dumps({"catalogs": {"x": [
            {"name": "stripe-webhooks", "description": "stripe checkout webhooks payments handling", "source_url": "u"},
            {"name": "kubernetes-ops", "description": "kubernetes pods helm clusters"}]}}))
        specs = [(1000 - i, "prompt", {"session_id": f"s{i}", "prompt_id": "p",
                                       "tokens": ["stripe", "checkout", "webhooks"]})
                 for i in range(learn.ONLINE_MIN_PROMPTS)]
        evs = _events(*specs)
        out = learn.learn_online(evs, [])
        self.assertEqual([o["name"] for o in out], ["stripe-webhooks"])
        self.assertIn("stripe", out[0]["matched"])

    def test_installed_plugin_skill_is_never_suggested(self) -> None:
        self._catalog(["superpowers:brainstorming"])
        learn.ONLINE_CATALOG.write_text(json.dumps({"catalogs": {"x": [
            {"name": "brainstorming", "description": "brainstorm ideas design requirements"}]}}))
        specs = [(1000 - i, "prompt", {"session_id": f"s{i}", "prompt_id": "p",
                                       "tokens": ["brainstorm", "ideas", "design", "requirements"]})
                 for i in range(learn.ONLINE_MIN_PROMPTS)]
        self.assertEqual(learn.learn_online(_events(*specs), []), [])


class TestCompaction(unittest.TestCase):

    def test_drops_only_old_embedder_dumps(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="compact-")) / "log.jsonl"
        real = learn.ROUTER_LOG
        learn.ROUTER_LOG = tmp
        try:
            old = _iso(time.time() - 30 * 86400)
            new = _iso(time.time())
            tmp.write_text("\n".join([
                json.dumps({"ts": old, "type": "embedding-skip", "neighbors": ["x"] * 50}),
                json.dumps({"ts": new, "type": "embedding-skip"}),
                json.dumps({"ts": old, "type": "chain-start", "steps": ["a"]}),
                json.dumps({"ts": old, "type": "invoke", "skill": "a"}),
                "not json at all",
            ]) + "\n")
            removed = learn.compact_log(keep_days=7)
            kept = tmp.read_text().splitlines()
            self.assertEqual(removed, 1)
            self.assertEqual(len(kept), 4)
            self.assertIn("not json at all", kept)
        finally:
            learn.ROUTER_LOG = real


class TestSessionBrief(unittest.TestCase):

    def test_silent_when_nothing_to_say(self) -> None:
        self.assertEqual(session_brief.brief({}), [])
        self.assertEqual(session_brief.brief({"discovered": {"new": []}, "online": [], "chains": []}), [])

    def test_new_skill_announced_only_while_recent(self) -> None:
        fresh = _iso(time.time() - 3600)
        stale = _iso(time.time() - 10 * 86400)
        lines = session_brief.brief({"discovered": {"new": [
            {"name": "fresh-skill", "first_seen": fresh},
            {"name": "stale-skill", "first_seen": stale}]}})
        self.assertEqual(len(lines), 1)
        self.assertIn("fresh-skill", lines[0])
        self.assertNotIn("stale-skill", lines[0])

    def test_three_lines_at_most(self) -> None:
        fresh = _iso(time.time())
        lines = session_brief.brief({
            "discovered": {"new": [{"name": f"s{i}", "first_seen": fresh} for i in range(9)]},
            "online": [{"name": "x", "source": "antigravity", "matched": ["a", "b"]}] * 5,
            "chains": [{"steps": ["a", "b", "c"], "n": 4}, {"steps": ["d", "e", "f"], "n": 3}],
        })
        self.assertEqual(len(lines), 3)
        self.assertIn("(+5)", lines[0])

    def test_hook_runs_silently_without_overlay(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="brief-"))
        proc = subprocess.run([sys.executable, str(SCRIPTS / "session_brief.py")],
                              capture_output=True, text=True, timeout=30,
                              env={**os.environ, "HOME": str(tmp)})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")


if __name__ == "__main__":
    unittest.main()
