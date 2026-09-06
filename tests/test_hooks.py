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

    def test_every_paired_skill_is_installed(self) -> None:
        import router  # type: ignore[import-not-found]
        pairings = json.loads((ROOT / "agent_skills.json").read_text())
        for agent, skills in pairings.items():
            if agent.startswith("_"):
                continue
            for skill in skills:
                with self.subTest(agent=agent, skill=skill):
                    self.assertTrue(router.valid_skill(skill),
                        f"{agent} is paired with uninstalled skill {skill}")


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
