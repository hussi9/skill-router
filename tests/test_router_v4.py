#!/usr/bin/env python3
"""
v4 routing: enriched index → route card → tiered enforcement → sub-agent hand-off.

Everything here runs against an index built from the live catalog (lexical
only, no network) in a temp directory, with the small-model stage disabled
so results are deterministic.

Run: python3 -m pytest tests/test_router_v4.py -q
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

_STATE = Path(tempfile.mkdtemp(prefix="router-v4-"))
_ENV = {
    "SKILL_ROUTER_NO_EMBED": "1",
    "SKILL_ROUTER_LLM": "0",
    "SKILL_ROUTER_NO_ENRICH_CALLS": "1",
    "SKILL_ROUTER_SESSION_DIR": str(_STATE / "session"),
}
_SAVED: dict[str, str | None] = {}

import build_index  # type: ignore[import-not-found]
import index_match  # type: ignore[import-not-found]
import router  # type: ignore[import-not-found]

INDEX = _STATE / "skill_index.json"
_REAL_INDEX_FILE = index_match.INDEX_FILE


def setUpModule() -> None:
    # Environment is set here, not at import: pytest imports every test
    # module before running any, so import-time env edits leak across files
    # (test_router.py pins the v3 table; this file needs the v4 index).
    for k, v in _ENV.items():
        _SAVED[k] = os.environ.get(k)
        os.environ[k] = v
    _SAVED["SKILL_ROUTER_NO_INDEX"] = os.environ.pop("SKILL_ROUTER_NO_INDEX", None)
    build_index.build_and_write(INDEX)
    index_match.INDEX_FILE = INDEX
    index_match.load_index.cache_clear()
    router.PENDING = _STATE / "pending.json"
    router.STRIKES = _STATE / "strikes.json"
    router.OVERRIDES_COUNT = _STATE / "overrides_count.json"
    router.OVERRIDES_LOG = _STATE / "overrides.jsonl"
    router.HISTORY = _STATE / "learned.json"
    router.LEARNED = router.HISTORY
    router.LOG = _STATE / "log.jsonl"
    router.SESSION_DIR = _STATE / "session"
    for f in (router.PENDING, router.STRIKES, router.OVERRIDES_COUNT, router.HISTORY):
        f.write_text("{}\n")
    router._load_history.cache_clear()


def tearDownModule() -> None:
    for k, v in _SAVED.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    index_match.INDEX_FILE = _REAL_INDEX_FILE
    index_match.load_index.cache_clear()


def run_hook(script: str, payload: dict, *args: str, env: dict | None = None) -> str:
    proc = subprocess.run([sys.executable, str(SCRIPTS / script), *args],
                          input=json.dumps(payload), capture_output=True, text=True,
                          timeout=30, env={**os.environ, **(env or {})})
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


class TestRouteCard(unittest.TestCase):
    def test_domain_skill_leads_the_chain_on_broken(self) -> None:
        path, chain, _, text = router.route("my macbook restarted again last night, can you check why")
        self.assertEqual(path, "BROKEN")
        self.assertEqual(chain[0].skill, "mac-doctor")
        self.assertIn("superpowers:systematic-debugging", [s.skill for s in chain])
        self.assertIn("IRON RULE", text)
        self.assertEqual(router.LAST_CARD.tier, "hard")
        self.assertIn("Memory:", text)

    def test_index_route_on_operate_is_soft(self) -> None:
        path, chain, _, text = router.route("research competitors for theaibill")
        self.assertEqual(chain[0].skill, "theaibill")
        self.assertIn("Soft route", text)
        self.assertNotIn("IRON RULE", text)
        self.assertEqual(router.LAST_CARD.tier, "soft")
        # OPERATE with no table verb must not append `refactor`
        self.assertNotIn("refactor", [s.skill for s in chain])

    def test_project_route_with_gates_is_hard_and_shows_gates(self) -> None:
        _, chain, _, text = router.route("let's ship the next @economicalai short about water")
        self.assertEqual(chain[0].skill, "youtube-manager")
        self.assertEqual(router.LAST_CARD.tier, "hard")
        self.assertIn("Gates before done", text)
        self.assertIn("IRON RULE", text)

    def test_question_beats_project_route(self) -> None:
        path, chain, _, text = router.route("what does the skill router do")
        self.assertEqual((path, chain, text), ("SKIP", [], ""))

    def test_design_review_is_operate_and_names_a_design_skill(self) -> None:
        path, chain, _, text = router.route("review the design of the deenunlock home protection card")
        self.assertEqual(path, "OPERATE")
        # lexical-only (model stage off here): either design skill is acceptable,
        # and it must be a soft route on the user's own skill
        self.assertIn(chain[0].skill, ("design-review", "ui-pattern", "plan-design-review"))
        self.assertIn("Soft route", text)

    def test_supabase_task_in_scrollbook_is_not_book_authoring(self) -> None:
        _, chain, _, _ = router.route("add row level security to the scrollbook briefings table in supabase")
        self.assertNotIn("claude-author", [s.skill for s in chain])


class TestPendingAndSession(unittest.TestCase):
    def test_hook_mode_writes_tier_and_session_route(self) -> None:
        env = dict(os.environ, SKILL_ROUTER_HOOK_MODE="1", SKILL_ROUTER_NO_LEARN="1")
        payload = {"prompt": "my macbook restarted again last night, can you check why",
                   "session_id": "sess-v4", "prompt_id": "p1"}
        proc = subprocess.run([sys.executable, str(SCRIPTS / "router.py")],
                              input=json.dumps(payload), capture_output=True, text=True,
                              timeout=60, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("mac-doctor", proc.stdout)
        pending = json.loads((Path.home() / ".claude" / "skill_router_pending.json").read_text())
        self.assertEqual(pending.get("tier"), "hard")
        self.assertEqual(pending.get("remaining", [None])[0], "mac-doctor")
        sess = json.loads((_STATE / "session" / "sess-v4.json").read_text())
        self.assertEqual(sess["primary"], "mac-doctor")
        self.assertEqual(sess["tier"], "hard")
        # leave the live pending file clean for the user's session
        (Path.home() / ".claude" / "skill_router_pending.json").write_text("{}\n")


class TestTieredEnforcement(unittest.TestCase):
    def _pending(self, tier: str, **extra) -> Path:
        p = Path(tempfile.mkdtemp()) / "pending.json"
        p.write_text(json.dumps({"primary": "mac-doctor", "remaining": ["mac-doctor"],
                                 "all": ["mac-doctor"], "tier": tier, **extra}) + "\n")
        return p

    def _run(self, pending: Path, mode: str, payload: dict | None = None) -> str:
        import iron_rule_hook  # type: ignore[import-not-found]
        real = iron_rule_hook.PENDING
        iron_rule_hook.PENDING = pending
        try:
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            sys.argv = ["iron_rule_hook.py", mode]
            sys.stdin = io.StringIO(json.dumps(payload or {}))
            with redirect_stdout(buf):
                iron_rule_hook.main()
            return buf.getvalue().strip()
        finally:
            iron_rule_hook.PENDING = real
            sys.stdin = sys.__stdin__

    def test_hard_tier_blocks_edit(self) -> None:
        out = self._run(self._pending("hard"), "pre", {"tool_name": "Edit"})
        self.assertIn('"permissionDecision": "deny"', out)

    def test_soft_tier_allows_edit(self) -> None:
        out = self._run(self._pending("soft"), "pre", {"tool_name": "Edit"})
        self.assertEqual(out, "")

    def test_soft_tier_asks_once_at_stop(self) -> None:
        p = self._pending("soft")
        first = self._run(p, "stop", {})
        self.assertIn('"decision": "block"', first)
        self.assertIn("skipped mac-doctor", first)
        second = self._run(p, "stop", {})
        self.assertEqual(second, "", "a soft route must ask exactly once")

    def test_missing_tier_means_hard(self) -> None:
        out = self._run(self._pending(""), "pre", {"tool_name": "Write"})
        self.assertIn("deny", out)


class TestSubagentHandoff(unittest.TestCase):
    def setUp(self) -> None:
        sd = _STATE / "session"
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "sess-x.json").write_text(json.dumps({
            "session_id": "sess-x", "path": "OPERATE", "skills": ["theaibill"],
            "primary": "theaibill", "tier": "soft", "gates": ["scripts/check.sh green"],
            "memory": ["theaibill-practice"]}))

    def test_task_hook_appends_route_to_prompt(self) -> None:
        out = run_hook("task_brief.py", {"session_id": "sess-x", "tool_name": "Task",
                                         "tool_input": {"prompt": "audit the pricing page",
                                                        "subagent_type": "general-purpose"}})
        payload = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(payload["permissionDecision"], "allow")
        self.assertIn('Skill(skill="theaibill")', payload["updatedInput"]["prompt"])
        self.assertIn("Completion gates", payload["updatedInput"]["prompt"])
        self.assertTrue(payload["updatedInput"]["prompt"].startswith("audit the pricing page"))

    def test_task_hook_stands_down_inside_subagent(self) -> None:
        out = run_hook("task_brief.py", {"session_id": "sess-x", "agent_id": "a1",
                                         "tool_input": {"prompt": "x"}})
        self.assertEqual(out, "")

    def test_task_hook_does_not_double_brief(self) -> None:
        out = run_hook("task_brief.py", {"session_id": "sess-x",
                                         "tool_input": {"prompt": "x\n[skill-router] already"}})
        self.assertEqual(out, "")

    def test_task_hook_is_offline_outside_hook_mode(self) -> None:
        # No SKILL_ROUTER_HOOK_MODE, no SKILL_ROUTER_JEV=1: never a model, never a call.
        out = run_hook("task_brief.py", {"session_id": "sess-none",
                                         "tool_input": {"prompt": "list every file importing requests"}},
                       env={"SKILL_ROUTER_HOOK_MODE": "", "SKILL_ROUTER_JEV": ""})
        # A brief may still be appended (latest.json from another test); a model never is.
        self.assertNotIn('"model"', out)

    def test_subagent_brief_carries_parent_route(self) -> None:
        out = run_hook("subagent_brief.py", {"agent_type": "general-purpose", "session_id": "sess-x"},
                       env={"SKILL_ROUTER_NO_LOG": "1"})
        self.assertIn("theaibill", json.loads(out)["hookSpecificOutput"]["additionalContext"])


class TestJevRoute(unittest.TestCase):
    """route_v4 with a Jev answer injected. No network: _jev_decide is replaced."""

    def setUp(self) -> None:
        self._decide = router._jev_decide
        import jev_choose  # type: ignore[import-not-found]
        self.jev = jev_choose

    def tearDown(self) -> None:
        router._jev_decide = self._decide

    def answer(self, domain=(None, 0.0), process=(None, 0.0), path="OPERATE", pathc=0.9):
        j = self.jev
        choice = j.Choice(j.Pick(domain[0], domain[1], j.tier(domain[1])),
                          j.Pick(process[0], process[1], j.tier(process[1])),
                          path, pathc, 400, 15000)
        router._jev_decide = lambda prompt: choice

    def test_confident_domain_routes_despite_typos(self) -> None:
        self.answer(domain=("mac-doctor", 0.93), process=(None, 0.9))
        path, chain, _, text = router.route("my mcbook keps restaring evry nite pls chek")
        self.assertEqual([s.skill for s in chain], ["mac-doctor"])
        self.assertIn("▶ mac-doctor", text)
        self.assertEqual(router.LAST_CARD.decided_by, "jev")
        self.assertEqual(router.LAST_CARD.confidence, "jev:0.93")
        self.assertNotEqual(path, "SKIP")

    def test_middle_confidence_is_silent_unless_suggestions_are_switched_on(self) -> None:
        self.answer(domain=("mac-doctor", 0.64), process=(None, 0.9))
        os.environ.pop("SKILL_ROUTER_JEV_SUGGEST", None)
        self.assertEqual(router.route("my mcbook keps restaring evry nite pls chek")[1:], ([], [], ""))

    def test_middle_confidence_is_a_suggestion_not_a_route(self) -> None:
        self.answer(domain=("mac-doctor", 0.64), process=(None, 0.9))
        os.environ["SKILL_ROUTER_JEV_SUGGEST"] = "1"
        self.addCleanup(os.environ.pop, "SKILL_ROUTER_JEV_SUGGEST", None)
        path, chain, _, text = router.route("my mcbook keps restaring evry nite pls chek")
        self.assertEqual((path, chain), ("SKIP", []))
        self.assertIn('Possible fit: Skill(skill="mac-doctor")', text)
        self.assertNotIn("IRON RULE", text)
        self.assertNotIn("Soft route", text)
        self.assertNotIn("▶", text)

    def test_low_confidence_is_silent(self) -> None:
        self.answer(domain=("mac-doctor", 0.31), process=(None, 0.9))
        self.assertEqual(router.route("my mcbook keps restaring evry nite pls chek")[3], "")

    def test_a_name_that_is_not_installed_is_dropped(self) -> None:
        self.answer(domain=("no-such-skill-anywhere", 0.99), process=(None, 0.9))
        path, chain, _, text = router.route("please run the imaginary skill on this repo")
        self.assertEqual((path, chain, text), ("SKIP", [], ""))

    def test_confident_process_pick_leads_when_no_domain(self) -> None:
        self.answer(domain=(None, 0.9), process=("superpowers:brainstorming", 0.88), path="BUILD")
        _, chain, _, text = router.route("lets think abuot how the onbording shuld work")
        self.assertEqual([s.skill for s in chain], ["superpowers:brainstorming"])
        self.assertIn("▶ superpowers:brainstorming", text)

    def test_an_unsure_jev_does_not_bring_the_regex_table_back(self) -> None:
        # Measured: on 150 no-skill turns the table leg made 25 of 53 carded steps.
        prompt = "add tests for the payment webhook handler"
        router._jev_decide = lambda p: None
        self.assertTrue(router.route(prompt)[1], "the lexical fallback itself still routes this")
        for conf in (0.3, 0.7, 0.95):
            self.answer(domain=(None, 0.9), process=(None, conf))
            self.assertEqual(router.route(prompt)[1], [], conf)

    def test_never_cards_a_skill_already_loaded_this_session(self) -> None:
        self.answer(domain=("mac-doctor", 0.93), process=("superpowers:systematic-debugging", 0.9),
                    path="BROKEN")
        prompt = "my mcbook keps restaring evry nite pls chek"
        saved = router.LOADED
        try:
            router.LOADED = frozenset({"mac-doctor"})
            _, chain, _, text = router.route(prompt)
            self.assertEqual([s.skill for s in chain], ["superpowers:systematic-debugging"])
            self.assertNotIn("mac-doctor", text)
            router.LOADED = frozenset({"mac-doctor", "superpowers:systematic-debugging"})
            self.assertEqual(router.route(prompt)[1:], ([], [], ""))
        finally:
            router.LOADED = saved

    def test_loaded_file_round_trip_and_subagents_do_not_count(self) -> None:
        sid = "sess-loaded-1"
        run_hook("skill_invoked.py", {"session_id": sid, "tool_name": "Skill",
                                      "tool_input": {"skill": "mac-doctor"}},
                 env={"SKILL_ROUTER_NO_LEARN": "1", "SKILL_ROUTER_NO_LOG": "1"})
        run_hook("skill_invoked.py", {"session_id": sid, "agent_id": "sub-1", "tool_name": "Skill",
                                      "tool_input": {"skill": "theaibill"}},
                 env={"SKILL_ROUTER_NO_LEARN": "1", "SKILL_ROUTER_NO_LOG": "1"})
        self.assertEqual(router.loaded_this_session({"session_id": sid}), frozenset({"mac-doctor"}))
        self.assertEqual(router.loaded_this_session({}), frozenset())

    def test_a_dead_agent_degrades_to_in_session_not_to_silence(self) -> None:
        saved = router.valid_agent
        try:
            router.valid_agent = lambda name: name != "product-designer" and saved(name)
            self.answer(domain=("design-review", 0.92), process=(None, 0.9))
            _, chain, _, text = router.route("do a comeplte desing revewi of the setings page")
            self.assertEqual([(s.skill, s.agent) for s in chain], [("design-review", "general-purpose")])
            self.assertNotIn("product-designer", text)
        finally:
            router.valid_agent = saved

    def test_router_never_routes_to_itself(self) -> None:
        self.answer(domain=("skill-router", 0.99), process=(None, 0.9))
        chain = router.route("pls chek why the anouncemnt hook is so noisey")[1]
        self.assertNotIn("skill-router", [s.skill for s in chain])

    def test_card_is_capped(self) -> None:
        self.answer(domain=("mac-doctor", 0.93), process=(None, 0.9))
        saved = router.specialist_for
        try:
            router.specialist_for = lambda prompt, exclude: ("some-specialist", "x" * 3000)
            self.answer(domain=(None, 0.9), process=("superpowers:brainstorming", 0.9), path="BUILD")
            text = router.route("lets think abuot how the onbording shuld work")[3]
            self.assertLessEqual(len(text), router.CARD_MAX_CHARS)
            self.assertIn("▶ superpowers:brainstorming", text)
        finally:
            router.specialist_for = saved

    def test_jev_question_never_silences_a_confident_route(self) -> None:
        self.answer(domain=("mac-doctor", 0.9), process=(None, 0.9), path="QUESTION", pathc=0.97)
        _, chain, _, _ = router.route("my mcbook keps restaring evry nite pls chek")
        self.assertEqual([s.skill for s in chain], ["mac-doctor"])

    def test_no_answer_falls_back_to_the_lexical_path(self) -> None:
        prompt = "my mac keeps restarting randomly with kernel panics, diagnose it"
        router._jev_decide = lambda p: None
        fallback = router.route(prompt)
        router._jev_decide = self._decide                  # real one: inactive outside hook mode
        self.assertEqual(router.route(prompt)[:2], fallback[:2])
        self.assertNotEqual(router.LAST_CARD.decided_by, "jev")

    def test_a_jev_timeout_does_not_also_pay_for_the_small_model(self) -> None:
        saved_flag, saved_llm = router.JEV_TIMED_OUT, os.environ.get("SKILL_ROUTER_LLM")
        os.environ["SKILL_ROUTER_LLM"] = "1"
        try:
            res = router._index_classify("review the design of the settings page and fix spacing")
            router.JEV_TIMED_OUT = True
            self.assertIsNone(router._llm_decide("review the design of the settings page", res))
        finally:
            router.JEV_TIMED_OUT = saved_flag
            os.environ["SKILL_ROUTER_LLM"] = saved_llm if saved_llm is not None else "0"

    def test_offline_outside_hook_mode(self) -> None:
        saved = {k: os.environ.pop(k, None) for k in ("SKILL_ROUTER_HOOK_MODE", "SKILL_ROUTER_JEV")}
        llm = os.environ.pop("SKILL_ROUTER_LLM", None)
        try:
            self.assertFalse(router._jev_active())
            os.environ["SKILL_ROUTER_HOOK_MODE"] = "1"
            self.assertTrue(router._jev_active())
            os.environ["SKILL_ROUTER_JEV"] = "0"
            self.assertFalse(router._jev_active())
            os.environ["SKILL_ROUTER_JEV"] = "1"
            os.environ["SKILL_ROUTER_LLM"] = "0"
            self.assertFalse(router._jev_active())
        finally:
            for k, v in {**saved, "SKILL_ROUTER_LLM": llm}.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v


class TestWorkTierAndQuota(unittest.TestCase):
    """v4.2: the work tier on the card, the Kimi nudge, the dispatch model."""

    def setUp(self) -> None:
        self._decide = router._jev_decide
        import jev_choose  # type: ignore[import-not-found]
        import quota  # type: ignore[import-not-found]
        self.jev, self.quota = jev_choose, quota
        self.qfile = _STATE / "quota.json"
        os.environ["SKILL_ROUTER_QUOTA_FILE"] = str(self.qfile)
        os.environ.pop("SKILL_ROUTER_KIMI", None)
        os.environ.pop("SKILL_ROUTER_HOOK_MODE", None)
        self.write_quota(five=20, seven=30)

    def tearDown(self) -> None:
        router._jev_decide = self._decide
        os.environ.pop("SKILL_ROUTER_QUOTA_FILE", None)
        os.environ.pop("SKILL_ROUTER_KIMI", None)

    def write_quota(self, five: float, seven: float, model: str = "claude-fable-5-1",
                    age_s: float = 0.0) -> None:
        import time
        self.qfile.write_text(json.dumps({"ts": time.time() - age_s, "model_id": model,
                                          "five_hour": five, "seven_day": seven}))

    def answer(self, domain=(None, 0.0), work=("heavy", 0.9), path="OPERATE", pathc=0.9):
        j = self.jev
        choice = j.Choice(j.Pick(domain[0], domain[1], j.tier(domain[1])),
                          j.Pick(None, 0.9, "route"), path, pathc, 400, 15000,
                          work=j.Pick(work[0], work[1], j.tier(work[1])))
        router._jev_decide = lambda prompt: choice

    def test_light_work_prints_the_dispatch_model_and_records_it(self) -> None:
        self.answer(domain=("mac-doctor", 0.93), work=("light", 0.91))
        _, chain, _, text = router.route("list every launch agent on this mac")
        self.assertIn("Work: light (jev 0.91) → sub-agents dispatch on haiku", text)
        self.assertEqual((router.LAST_CARD.work, router.LAST_CARD.model), ("light", "haiku"))
        router.write_session_route(chain, "OPERATE", {"session_id": "sess-w"})
        rec = json.loads((_STATE / "session" / "sess-w.json").read_text())
        self.assertEqual((rec["work"], rec["model"], rec["work_confidence"]), ("light", "haiku", 0.91))

    def test_heavy_or_unsure_work_prints_nothing_extra(self) -> None:
        self.answer(domain=("mac-doctor", 0.93), work=("heavy", 0.95))
        text = router.route("my mac keeps restarting at night, find the root cause")[3]
        self.assertNotIn("Work:", text)
        self.answer(domain=("mac-doctor", 0.93), work=("light", 0.7))
        text = router.route("clean up the launch agents")[3]
        self.assertNotIn("Work:", text)
        self.assertEqual(router.LAST_CARD.model, "inherit")

    def test_quota_high_points_light_work_at_kimi(self) -> None:
        self.write_quota(five=87, seven=40)
        self.answer(domain=("mac-doctor", 0.93), work=("light", 0.9))
        text = router.route("list every launch agent on this mac")[3]
        self.assertIn("Quota: 5h 87% · 7d 40%", text)
        self.assertIn("kimi_offload.sh --tier light", text)

    def test_quota_high_leaves_heavy_work_on_the_session_model(self) -> None:
        self.write_quota(five=97, seven=90)
        self.answer(domain=("mac-doctor", 0.93), work=("heavy", 0.9))
        text = router.route("find the root cause of the nightly restarts")[3]
        self.assertNotIn("Quota:", text)
        self.assertNotIn("kimi", text.lower())

    def test_critical_quota_nudges_at_half_confidence_high_does_not(self) -> None:
        self.answer(domain=("mac-doctor", 0.93), work=("light", 0.6))
        self.write_quota(five=88, seven=40)                                 # high: bar stays 0.8
        self.assertNotIn("kimi", router.route("commit the launch agent cleanup")[3].lower())
        self.write_quota(five=96, seven=40)                                 # critical: bar drops to 0.5
        text = router.route("commit the launch agent cleanup")[3]
        self.assertIn("kimi_offload.sh --tier light", text)
        self.assertNotIn("Work:", text)                                     # 0.6 never moves a dispatch model
        self.answer(domain=("mac-doctor", 0.93), work=("heavy", 0.55))
        self.assertNotIn("kimi", router.route("rethink the launch agent setup")[3].lower())

    def test_quota_low_never_mentions_kimi(self) -> None:
        self.answer(domain=("mac-doctor", 0.93), work=("light", 0.9))
        text = router.route("list every launch agent on this mac")[3]
        self.assertNotIn("kimi", text.lower())

    def test_stale_quota_file_reads_as_unknown(self) -> None:
        self.write_quota(five=99, seven=99, age_s=3600)
        self.assertEqual(self.quota.band(), "")
        self.answer(domain=("mac-doctor", 0.93), work=("light", 0.9))
        self.assertNotIn("kimi", router.route("list every launch agent")[3].lower())

    def test_kimi_always_mode_and_off_mode(self) -> None:
        self.answer(domain=("mac-doctor", 0.93), work=("standard", 0.85))
        os.environ["SKILL_ROUTER_KIMI"] = "always"
        text = router.route("add a launchd plist for the backup script")[3]
        self.assertIn("Kimi mode: always", text)
        self.assertNotIn("--tier", text)                       # standard is the script default
        os.environ["SKILL_ROUTER_KIMI"] = "off"
        self.write_quota(five=99, seven=99)
        text = router.route("add a launchd plist for the backup script")[3]
        self.assertNotIn("kimi", text.lower())

    def test_no_card_still_gets_the_nudge_on_strained_quota(self) -> None:
        self.write_quota(five=91, seven=50)
        self.answer(domain=(None, 0.9), work=("light", 0.88), path="OPERATE", pathc=0.8)
        path, chain, _, text = router.route("rename every occurrence of fetchUser to loadUser")
        self.assertEqual((path, chain), ("SKIP", []))
        self.assertIn("kimi_offload.sh", text)
        self.assertNotIn("▶", text)

    def test_no_card_and_question_stays_silent(self) -> None:
        self.write_quota(five=91, seven=50)
        self.answer(domain=(None, 0.9), work=("light", 0.88), path="QUESTION", pathc=0.9)
        self.assertEqual(router.route("what does launchd do")[3], "")

    def test_nudge_is_rate_limited_per_session_in_hook_mode(self) -> None:
        self.write_quota(five=91, seven=50)
        self.answer(domain=("mac-doctor", 0.93), work=("light", 0.9))
        os.environ["SKILL_ROUTER_HOOK_MODE"] = "1"
        self.addCleanup(os.environ.pop, "SKILL_ROUTER_HOOK_MODE", None)
        router.SESSION_ID = "sess-rl"
        self.addCleanup(setattr, router, "SESSION_ID", "")
        first = router.route("list every launch agent on this mac")[3]
        second = router.route("list every launch agent on this mac")[3]
        self.assertIn("kimi_offload.sh", first)
        self.assertNotIn("kimi_offload.sh", second)
        self.assertIn("Work: light", second)                  # the model line still prints

    def test_quota_module_reads_model_class_and_bands(self) -> None:
        q = self.quota
        self.assertEqual(q.model_class("claude-fable-5-1"), "fable")
        self.assertEqual(q.model_class("claude-haiku-4-5-20251001"), "haiku")
        self.assertEqual(q.model_class("kimi-k3[1m]"), "")
        self.write_quota(five=50, seven=96)
        self.assertEqual(q.band(), "critical")
        self.write_quota(five=81, seven=10)
        self.assertEqual(q.band(), "high")
        self.qfile.write_text("not json")
        self.assertEqual((q.band(), q.session_model(), q.summary()), ("", "", ""))


class TestDispatchModel(unittest.TestCase):
    """task_brief.py sets the sub-agent model from Jev's tier — in-process, no network."""

    def setUp(self) -> None:
        import task_brief  # type: ignore[import-not-found]
        import jev_choose  # type: ignore[import-not-found]
        self.tb, self.jev = task_brief, jev_choose
        self._tier_only = jev_choose.tier_only
        self.tb.LOG = _STATE / "tb-log.jsonl"
        for f in (_STATE / "session").glob("*.json"):      # no parent route unless a test writes one
            f.unlink()
        os.environ["SKILL_ROUTER_JEV"] = "1"
        os.environ.pop("SKILL_ROUTER_SUBAGENT_MODEL", None)

    def tearDown(self) -> None:
        self.jev.tier_only = self._tier_only
        os.environ.pop("SKILL_ROUTER_JEV", None)
        os.environ.pop("SKILL_ROUTER_SUBAGENT_MODEL", None)

    def fake(self, name, conf):
        self.jev.tier_only = lambda prompt, timeout=None: self.jev.Pick(name, conf, self.jev.tier(conf))

    def run_main(self, payload: dict) -> dict:
        import io
        saved_in, saved_out = sys.stdin, sys.stdout
        sys.stdin, sys.stdout = io.StringIO(json.dumps(payload)), io.StringIO()
        try:
            self.assertEqual(self.tb.main(), 0)
            out = sys.stdout.getvalue().strip()
        finally:
            sys.stdin, sys.stdout = saved_in, saved_out
        return json.loads(out)["hookSpecificOutput"]["updatedInput"] if out else {}

    def test_light_dispatch_goes_to_haiku(self) -> None:
        self.fake("light", 0.92)
        upd = self.run_main({"session_id": "s1", "tool_input": {
            "prompt": "find every call site of fetchUser", "subagent_type": "Explore"}})
        self.assertEqual(upd["model"], "haiku")
        self.assertEqual(upd["prompt"], "find every call site of fetchUser")
        rec = json.loads(self.tb.LOG.read_text().splitlines()[-1])
        self.assertEqual((rec["type"], rec["model"], rec["work"], rec["agent"]),
                         ("subagent-model", "haiku", "light", "Explore"))

    def test_standard_dispatch_goes_to_sonnet(self) -> None:
        self.fake("standard", 0.83)
        upd = self.run_main({"session_id": "s1", "tool_input": {"prompt": "add a unit test for parse_date"}})
        self.assertEqual(upd["model"], "sonnet")

    def test_heavy_or_unsure_dispatch_is_untouched(self) -> None:
        self.fake("heavy", 0.9)
        self.assertEqual(self.run_main({"tool_input": {"prompt": "redesign the auth flow"}}), {})
        self.fake("light", 0.74)
        self.assertEqual(self.run_main({"tool_input": {"prompt": "tidy this up"}}), {})
        rec = json.loads(self.tb.LOG.read_text().splitlines()[-1])
        self.assertEqual((rec["model"], rec["work"]), ("inherit", "light"))

    def test_explicit_model_and_escape_marker_are_respected(self) -> None:
        self.fake("light", 0.99)
        self.assertEqual(self.run_main({"tool_input": {"prompt": "grep for TODO", "model": "opus"}}), {})
        self.assertEqual(self.run_main({"tool_input": {"prompt": "grep for TODO [no-router]"}}), {})

    def test_kill_switch(self) -> None:
        self.fake("light", 0.99)
        os.environ["SKILL_ROUTER_SUBAGENT_MODEL"] = "0"
        self.assertEqual(self.run_main({"tool_input": {"prompt": "grep for TODO"}}), {})

    def test_jev_failure_leaves_the_dispatch_alone(self) -> None:
        self.jev.tier_only = lambda prompt, timeout=None: None
        self.assertEqual(self.run_main({"tool_input": {"prompt": "grep for TODO"}}), {})

    def test_model_and_brief_compose(self) -> None:
        sd = _STATE / "session"
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "sess-m.json").write_text(json.dumps({
            "session_id": "sess-m", "path": "OPERATE", "skills": ["theaibill"],
            "primary": "theaibill", "tier": "soft", "gates": [], "memory": []}))
        self.fake("light", 0.9)
        upd = self.run_main({"session_id": "sess-m", "tool_input": {"prompt": "list the pricing tiers"}})
        self.assertEqual(upd["model"], "haiku")
        self.assertIn('Skill(skill="theaibill")', upd["prompt"])


class TestPreviousAssistantTail(unittest.TestCase):
    def test_reads_last_main_thread_assistant_text(self) -> None:
        t = _STATE / "transcript.jsonl"
        rows = [
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "old turn"}]}},
            {"type": "user", "message": {"content": "do the thing"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Done. Shall I run the design review next?"},
                {"type": "tool_use", "name": "Bash", "input": {}}]}},
            {"type": "assistant", "isSidechain": True,
             "message": {"content": [{"type": "text", "text": "sub-agent chatter"}]}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}]}},
        ]
        t.write_text("\n".join(json.dumps(r, separators=(",", ":")) for r in rows) + "\n")
        got = router.previous_assistant_tail({"transcript_path": str(t)}, chars=19)
        self.assertEqual(got, "design review next?")
        self.assertEqual(router.previous_assistant_tail({}), "")
        self.assertEqual(router.previous_assistant_tail({"transcript_path": "/nope/x.jsonl"}), "")


class TestInstallerWiring(unittest.TestCase):
    def test_task_hook_and_index_are_installed(self) -> None:
        import install_hooks  # type: ignore[import-not-found]
        hooks = install_hooks.managed_hooks()
        pre = [g for g in hooks["PreToolUse"]]
        self.assertTrue(any("task_brief.py" in h["command"] for g in pre for h in g["hooks"]))
        session = " ".join(h["command"] for g in hooks["SessionStart"] for h in g["hooks"])
        self.assertIn("build_index.py", session)
        self.assertIn("refresh_env.py", session)
        ups = hooks["UserPromptSubmit"][0]["hooks"][0]
        self.assertGreaterEqual(ups["timeout"], 10)


if __name__ == "__main__":
    unittest.main()
