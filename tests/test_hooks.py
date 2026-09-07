#!/usr/bin/env python3
"""
Tests for the hook processes — the layer that was silently absent.

The router's engine, docs and unit tests were all healthy while routing did
nothing at all, because the hooks that run the engine were not in
settings.json. Nothing in the old suite could have caught that: it tested
route() and never asked whether anything called it. These tests exercise the
hook scripts through their real contract (JSON on stdin, JSON on stdout) and
assert that the installer produces a settings file Claude Code will actually
run.

Run: python3 -m pytest tests/test_hooks.py -q
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

import install_hooks  # type: ignore[import-not-found]


def run_hook(script: str, payload: dict, *args: str,
             env: dict | None = None) -> str:
    proc = subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        input=json.dumps(payload),
        capture_output=True, text=True, timeout=30,
        env={**os.environ, **(env or {})},
    )
    assert proc.returncode == 0, (
        f"{script} exited {proc.returncode}; a hook must never fail the turn. "
        f"stderr={proc.stderr}"
    )
    return proc.stdout.strip()


class TestIronRuleHook(unittest.TestCase):
    """Enforcement, and the three ways it must decline to enforce."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="iron-"))
        self.pending = self.tmp / "pending.json"
        self.env = {"HOME": str(self.tmp)}
        (self.tmp / ".claude").mkdir(parents=True, exist_ok=True)
        self.live_pending = self.tmp / ".claude" / "skill_router_pending.json"
        (self.tmp / ".claude" / "skills" / "demo-skill").mkdir(parents=True, exist_ok=True)

    def seed(self, remaining: list[str]) -> None:
        self.live_pending.write_text(json.dumps({
            "primary": remaining[0] if remaining else "",
            "remaining": remaining, "all": remaining,
        }))

    def test_denies_edit_while_skill_pending(self) -> None:
        self.seed(["demo-skill"])
        out = run_hook("iron_rule_hook.py", {"tool_name": "Edit"}, "pre", env=self.env)
        decision = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(decision["permissionDecision"], "deny")
        self.assertIn('Skill(skill="demo-skill")', decision["permissionDecisionReason"])

    def test_stands_down_inside_a_subagent(self) -> None:
        """The deadlock this guard exists to prevent.

        Sub-agents inherit settings.json hooks, so the parent's pending skill
        was blocking every Edit inside every dispatched agent — and the
        sub-agent cannot satisfy it, because that skill belongs to the
        parent's turn. Routing could not extend to sub-agents at all until
        enforcement learned to stop at the boundary.
        """
        self.seed(["demo-skill"])
        out = run_hook("iron_rule_hook.py",
                       {"tool_name": "Edit", "agent_id": "ag_1",
                        "agent_type": "db-expert"}, "pre", env=self.env)
        self.assertEqual(out, "", "enforcement must not reach inside sub-agents")

    def test_uninstalled_skill_clears_itself(self) -> None:
        self.seed(["no-such-skill-anywhere"])
        out = run_hook("iron_rule_hook.py", {"tool_name": "Edit"}, "pre", env=self.env)
        self.assertEqual(out, "", "a skill that cannot be invoked cannot be required")
        self.assertEqual(json.loads(self.live_pending.read_text()), {})

    def test_stop_blocks_unsatisfied_route(self) -> None:
        self.seed(["demo-skill"])
        out = run_hook("iron_rule_hook.py", {}, "stop", env=self.env)
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_stop_does_not_recurse(self) -> None:
        self.seed(["demo-skill"])
        out = run_hook("iron_rule_hook.py", {"stop_hook_active": True},
                       "stop", env=self.env)
        self.assertEqual(out, "", "blocking a turn we already blocked loops forever")

    def test_no_pending_state_is_silent(self) -> None:
        self.assertEqual(
            run_hook("iron_rule_hook.py", {"tool_name": "Edit"}, "pre", env=self.env), "")

    def test_malformed_input_is_silent(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "iron_rule_hook.py"), "pre"],
            input="not json at all", capture_output=True, text=True, timeout=30,
            env={**os.environ, **self.env})
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "")


class TestSkillInvokedHook(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="invoked-"))
        (self.tmp / ".claude").mkdir(parents=True, exist_ok=True)
        self.env = {"HOME": str(self.tmp)}
        self.pending = self.tmp / ".claude" / "skill_router_pending.json"
        self.usage = self.tmp / ".claude" / "skill_usage.log"

    def test_satisfies_pending_and_logs(self) -> None:
        self.pending.write_text(json.dumps(
            {"primary": "alpha", "remaining": ["alpha", "beta"], "all": ["alpha", "beta"]}))
        run_hook("skill_invoked.py", {"tool_input": {"skill": "alpha"}}, env=self.env)
        self.assertEqual(json.loads(self.pending.read_text())["remaining"], ["beta"])
        self.assertIn("alpha", self.usage.read_text())

    def test_ignores_calls_without_a_skill(self) -> None:
        run_hook("skill_invoked.py", {"tool_input": {}}, env=self.env)
        self.assertFalse(self.usage.exists())

    def test_reads_stdin_exactly_once(self) -> None:
        """The bug that made re-arming impossible.

        This job used to be four separate shell hooks on the same matcher,
        each starting with `cat`. Only the first ever saw the payload, so the
        strike and override tallies were never cleared by a successful invoke
        and could only grow — which is how five core skills ended up
        permanently demoted.
        """
        source = (SCRIPTS / "skill_invoked.py").read_text()
        self.assertEqual(source.count("sys.stdin.read()"), 1)


class TestSubagentBrief(unittest.TestCase):

    def test_generic_agents_get_no_brief(self) -> None:
        for agent in ("general-purpose", "Explore", "researcher"):
            with self.subTest(agent=agent):
                self.assertEqual(
                    run_hook("subagent_brief.py", {"agent_type": agent},
                             env={"SKILL_ROUTER_NO_LOG": "1"}),
                    "", "a generic agent has no domain, so any skill is noise")

    def test_paired_agent_gets_its_skills(self) -> None:
        out = run_hook("subagent_brief.py", {"agent_type": "test-runner"},
                       env={"SKILL_ROUTER_NO_LOG": "1"})
        payload = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(payload["hookEventName"], "SubagentStart")
        self.assertIn("test-driven-development", payload["additionalContext"])

    def test_unpaired_agent_derives_from_its_own_description(self) -> None:
        """A new agent must get sensible skills with no registration step,
        or the pairing file silently rots as the roster changes."""
        out = run_hook("subagent_brief.py", {"agent_type": "seo-technical"},
                       env={"SKILL_ROUTER_NO_LOG": "1"})
        if not out:
            self.skipTest("seo-technical agent not installed on this machine")
        self.assertIn("seo", json.loads(out)["hookSpecificOutput"]["additionalContext"])

    def test_missing_agent_type_is_silent(self) -> None:
        self.assertEqual(run_hook("subagent_brief.py", {},
                                  env={"SKILL_ROUTER_NO_LOG": "1"}), "")

    def test_every_emitted_skill_is_installed(self) -> None:
        """The brief may only name skills the sub-agent can actually load.

        Pairings in agent_skills.json are aspirations; skills get archived and
        renamed under them. What matters is what reaches the sub-agent, so
        the contract is on the emitted list, filtered at brief time.
        """
        import router  # type: ignore[import-not-found]
        import subagent_brief  # type: ignore[import-not-found]
        pairings = json.loads((ROOT / "agent_skills.json").read_text())
        for agent in pairings:
            if agent.startswith("_"):
                continue
            skills, _ = subagent_brief.skills_for(agent)
            for skill in skills:
                with self.subTest(agent=agent, skill=skill):
                    self.assertTrue(router.valid_skill(skill),
                        f"brief for {agent} names uninstalled skill {skill}")

    def test_archived_pairing_falls_back_rather_than_naming_it(self) -> None:
        import subagent_brief  # type: ignore[import-not-found]
        real = subagent_brief._load_pairings
        subagent_brief._load_pairings = lambda: {"probe-agent": ["no-such-skill-xyz"]}
        try:
            skills, provenance = subagent_brief.skills_for("probe-agent")
            self.assertNotIn("no-such-skill-xyz", skills)
            self.assertEqual(provenance, "derived")
        finally:
            subagent_brief._load_pairings = real


class TestHandoverNudge(unittest.TestCase):
    """After a Skill call, say what usually comes next — if history is clear."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="nudge-"))
        (self.tmp / ".claude").mkdir(parents=True)
        self.env = {"HOME": str(self.tmp)}
        self.learned = self.tmp / ".claude" / "skill_router_learned.json"

    def _overlay(self, handovers: dict) -> None:
        self.learned.write_text(json.dumps({"handovers": handovers}))

    def test_clear_habit_produces_a_nudge(self) -> None:
        self._overlay({"alpha": [{"to": "beta", "n": 6, "p": 0.75, "median_min": 4.0}]})
        out = run_hook("skill_invoked.py", {"tool_input": {"skill": "alpha"},
                                            "session_id": "s", "prompt_id": "p"}, env=self.env)
        ctx = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(ctx["hookEventName"], "PostToolUse")
        self.assertIn("beta", ctx["additionalContext"])
        self.assertIn("Advisory", ctx["additionalContext"])

    def test_weak_habit_is_silent(self) -> None:
        self._overlay({"alpha": [{"to": "beta", "n": 2, "p": 0.9}]})
        self.assertEqual(run_hook("skill_invoked.py", {"tool_input": {"skill": "alpha"}},
                                  env=self.env), "")
        self._overlay({"alpha": [{"to": "beta", "n": 9, "p": 0.3}]})
        self.assertEqual(run_hook("skill_invoked.py", {"tool_input": {"skill": "alpha"}},
                                  env=self.env), "")

    def test_no_nudge_inside_a_subagent(self) -> None:
        self._overlay({"alpha": [{"to": "beta", "n": 6, "p": 0.75}]})
        self.assertEqual(run_hook("skill_invoked.py", {"tool_input": {"skill": "alpha"},
                                                       "agent_id": "ag1"}, env=self.env), "")

    def test_invoke_event_carries_ids(self) -> None:
        run_hook("skill_invoked.py", {"tool_input": {"skill": "alpha"},
                                      "session_id": "sess-9", "prompt_id": "pid-9"}, env=self.env)
        log = self.tmp / ".claude" / "skill_router_log.jsonl"
        events = [json.loads(l) for l in log.read_text().splitlines()]
        inv = next(e for e in events if e["type"] == "invoke")
        self.assertEqual((inv["session_id"], inv["prompt_id"], inv["skill"]),
                         ("sess-9", "pid-9", "alpha"))


class TestInstaller(unittest.TestCase):
    """The installer must add hooks beside other tools', never over them."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="install-"))
        self.settings = self.tmp / "settings.json"
        self._real = install_hooks.SETTINGS
        install_hooks.SETTINGS = self.settings

    def tearDown(self) -> None:
        install_hooks.SETTINGS = self._real

    def write(self, data: dict) -> None:
        self.settings.write_text(json.dumps(data))

    def install(self, *args: str) -> int:
        argv = sys.argv
        sys.argv = ["install_hooks.py", *args]
        try:
            return install_hooks.main()
        finally:
            sys.argv = argv

    def test_preserves_foreign_hooks(self) -> None:
        self.write({"hooks": {"UserPromptSubmit": [
            {"hooks": [{"type": "command", "command": "/opt/other-tool/hook.py"}]}]}})
        self.install()
        groups = json.loads(self.settings.read_text())["hooks"]["UserPromptSubmit"]
        commands = [h["command"] for g in groups for h in g["hooks"]]
        self.assertIn("/opt/other-tool/hook.py", commands)
        self.assertTrue(any("router.py" in c for c in commands))

    def test_preserves_unrelated_settings(self) -> None:
        self.write({"model": "claude-fable-5-1", "permissions": {"allow": ["Bash(*)"]},
                    "hooks": {}})
        self.install()
        settings = json.loads(self.settings.read_text())
        self.assertEqual(settings["model"], "claude-fable-5-1")
        self.assertEqual(settings["permissions"]["allow"], ["Bash(*)"])

    def test_is_idempotent(self) -> None:
        self.write({"hooks": {}})
        self.install()
        first = json.loads(self.settings.read_text())["hooks"]
        self.install()
        second = json.loads(self.settings.read_text())["hooks"]
        self.assertEqual(first, second, "re-running must not stack duplicates")

    def test_removes_superseded_shell_hooks(self) -> None:
        """An older hand-installed logger left in place would double-count every
        Skill call, silently doubling every follow rate the learning loop
        reports."""
        self.write({"hooks": {"PostToolUse": [{"matcher": "Skill", "hooks": [
            {"type": "command", "command": "echo x >> ~/.claude/skill_usage.log"}]}]}})
        self.install()
        commands = [h["command"] for g in
                    json.loads(self.settings.read_text())["hooks"]["PostToolUse"]
                    for h in g["hooks"]]
        self.assertEqual(sum("skill_usage.log" in c for c in commands), 0)
        self.assertEqual(sum("skill_invoked.py" in c for c in commands), 1)

    def test_remove_leaves_foreign_hooks_alone(self) -> None:
        self.write({"hooks": {"Stop": [
            {"hooks": [{"type": "command", "command": "/opt/other/stop-hook"}]}]}})
        self.install()
        self.install("--remove")
        groups = json.loads(self.settings.read_text())["hooks"]["Stop"]
        commands = [h["command"] for g in groups for h in g["hooks"]]
        self.assertEqual(commands, ["/opt/other/stop-hook"])

    def test_covers_every_event_routing_depends_on(self) -> None:
        managed = install_hooks.managed_hooks()
        for event in ("UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop",
                      "SubagentStart", "SessionStart"):
            self.assertIn(event, managed)

    def test_session_start_has_a_foreground_brief_and_background_learning(self) -> None:
        groups = install_hooks.managed_hooks()["SessionStart"]
        commands = [h["command"] for g in groups for h in g["hooks"]]
        brief = [c for c in commands if "session_brief.py" in c]
        self.assertEqual(len(brief), 1)
        self.assertNotIn("&", brief[0], "the brief is foreground: its stdout is context")
        learn = [c for c in commands if "learn.py" in c]
        self.assertEqual(len(learn), 1)
        self.assertTrue(learn[0].rstrip().endswith("&"),
            "learning is backgrounded: a session must never wait on it")
        matchers = [g.get("matcher") for g in groups if any("session_brief" in h["command"] for h in g["hooks"])]
        self.assertEqual(matchers, ["startup|resume"], "no brief on compact")

    def test_referenced_scripts_all_exist(self) -> None:
        for event, groups in install_hooks.managed_hooks().items():
            for group in groups:
                for hook in group["hooks"]:
                    for token in hook["command"].split():
                        if token.endswith((".py", ".sh")) and "/" in token:
                            with self.subTest(event=event, script=token):
                                self.assertTrue(Path(token).is_file(),
                                    f"{event} hook references missing {token}")


if __name__ == "__main__":
    unittest.main()


class TestAgentModels(unittest.TestCase):
    """Sub-agents must run at the session's model, not one pinned years ago.

    Measured on Claude Code 2.1.263 from an Opus 5 parent session, by reading
    `modelUsage` out of a headless run rather than asking a model to name
    itself (self-reports were wrong):

        model: sonnet   ->  claude-opus-5 + claude-sonnet-5   (downgraded)
        model: inherit  ->  claude-opus-5                     (correct)
    """

    def setUp(self) -> None:
        import fix_agent_models  # type: ignore[import-not-found]
        self.mod = fix_agent_models

    def test_rewrites_only_the_frontmatter_model(self) -> None:
        text = ("---\nname: x\nmodel: sonnet\ntools: Read\n---\n\n"
                "Body text mentioning model: sonnet in prose.\n")
        out = self.mod.retarget(text)
        self.assertIn("model: inherit", out.split("---")[1])
        self.assertIn("model: sonnet in prose", out,
                      "prose outside the frontmatter must not be rewritten")

    def test_leaves_files_without_frontmatter_alone(self) -> None:
        text = "# Just a heading\n\nmodel: sonnet\n"
        self.assertEqual(self.mod.retarget(text), text)

    def test_haiku_is_a_deliberate_downgrade_and_is_kept(self) -> None:
        self.assertIn("haiku", self.mod.KEEP,
            "haiku is the one intentional downgrade — bulk read-only work")

    def test_live_agents_do_not_override_the_session(self) -> None:
        agents = Path.home() / ".claude" / "agents"
        if not agents.is_dir():
            self.skipTest("no agents directory")
        pinned = []
        for path in sorted(agents.glob("*.md")):
            if path.name.startswith("_"):
                continue
            model = self.mod.current_model(path.read_text(encoding="utf-8"))
            if model and model != "inherit" and model not in self.mod.KEEP:
                pinned.append(f"{path.stem}={model}")
        self.assertEqual(pinned, [],
            "a pinned agent downgrades every dispatch on a stronger session; "
            "run scripts/fix_agent_models.py")
