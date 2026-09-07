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

    def test_subagent_brief_carries_parent_route(self) -> None:
        out = run_hook("subagent_brief.py", {"agent_type": "general-purpose", "session_id": "sess-x"},
                       env={"SKILL_ROUTER_NO_LOG": "1"})
        self.assertIn("theaibill", json.loads(out)["hookSpecificOutput"]["additionalContext"])


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
