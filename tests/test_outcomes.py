#!/usr/bin/env python3
"""
Tests for outcome events: did the lines the router showed lead anywhere?

Every test writes to its own temp log through SKILL_ROUTER_OUTCOMES_FILE;
nothing here reads or writes the developer's ~/.claude.

Run: python3 -m pytest tests/test_outcomes.py -q
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import outcomes  # type: ignore[import-not-found]  # noqa: E402


class OutcomesCase(unittest.TestCase):

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="outcomes-"))
        self.log = self.tmp / "outcomes.jsonl"
        self._env = {k: os.environ.get(k) for k in
                     ("SKILL_ROUTER_OUTCOMES_FILE", "SKILL_ROUTER_NO_LEARN")}
        os.environ["SKILL_ROUTER_OUTCOMES_FILE"] = str(self.log)
        os.environ.pop("SKILL_ROUTER_NO_LEARN", None)

    def tearDown(self) -> None:
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def events(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]

    def run_hook(self, payload: dict | str, *args: str) -> subprocess.CompletedProcess:
        data = payload if isinstance(payload, str) else json.dumps(payload)
        env = {**os.environ, "HOME": str(self.tmp),
               "SKILL_ROUTER_SESSION_DIR": str(self.tmp / "sessions")}
        proc = subprocess.run([sys.executable, str(SCRIPTS / "outcomes.py"), *args],
                              input=data, capture_output=True, text=True, timeout=30, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc


class TestFollowed(OutcomesCase):

    def test_shown_then_loaded_counts_as_followed(self) -> None:
        outcomes.record_shown("s1", "p1", ["systematic-debugging"], [], "jev")
        outcomes.record_signal("s1", "p1", "loaded", "systematic-debugging")
        outcomes.record_shown("s1", "p2", ["writing-plans"], [], "jev")
        summary = outcomes.summarize()
        self.assertEqual(summary["shown"], 2)
        self.assertEqual(summary["loaded_from_card"], 1)
        self.assertEqual(summary["loaded_from_shortlist"], 0)
        self.assertAlmostEqual(summary["followed_rate"], 0.5)

    def test_shortlist_load_is_attributed_to_shortlist(self) -> None:
        outcomes.record_shown("s1", "p1", [], ["alpha", "beta", "gamma"], "keyless")
        outcomes.record_signal("s1", "p1", "loaded", "beta")
        # A load of something never shown is not a follow.
        outcomes.record_shown("s1", "p2", [], ["alpha"], "keyless")
        outcomes.record_signal("s1", "p2", "loaded", "delta")
        summary = outcomes.summarize()
        self.assertEqual(summary["loaded_from_shortlist"], 1)
        self.assertEqual(summary["loaded_from_card"], 0)
        self.assertAlmostEqual(summary["followed_rate"], 0.5)

    def test_load_in_another_turn_does_not_count(self) -> None:
        outcomes.record_shown("s1", "p1", ["alpha"], [], "jev")
        outcomes.record_signal("s1", "p9", "loaded", "alpha")
        outcomes.record_signal("s2", "p1", "loaded", "alpha")
        self.assertEqual(outcomes.summarize()["loaded_from_card"], 0)

    def test_duplicate_loads_count_once(self) -> None:
        outcomes.record_shown("s1", "p1", ["alpha"], [], "jev")
        outcomes.record_signal("s1", "p1", "loaded", "alpha")
        outcomes.record_signal("s1", "p1", "loaded", "alpha")
        self.assertEqual(outcomes.summarize()["loaded_from_card"], 1)

    def test_summary_splits_jev_and_keyless(self) -> None:
        outcomes.record_shown("s1", "p1", ["alpha"], [], "jev")
        outcomes.record_signal("s1", "p1", "loaded", "alpha")
        outcomes.record_signal("s1", "p1", "tests_green")
        outcomes.record_shown("s1", "p2", [], ["beta"], "keyless")
        outcomes.record_shown("s1", "p3", [], ["gamma"], "keyless")
        outcomes.record_signal("s1", "p3", "correction")
        summary = outcomes.summarize()
        self.assertEqual(set(summary), {"shown", "loaded_from_card", "loaded_from_shortlist",
                                        "followed_rate", "tests_green_after",
                                        "corrections_after", "by_mode", "held_out", "followed"})
        jev, keyless = summary["by_mode"]["jev"], summary["by_mode"]["keyless"]
        self.assertEqual((jev["shown"], jev["loaded_from_card"], jev["tests_green_after"]), (1, 1, 1))
        self.assertAlmostEqual(jev["followed_rate"], 1.0)
        self.assertEqual((keyless["shown"], keyless["corrections_after"]), (2, 1))
        self.assertAlmostEqual(keyless["followed_rate"], 0.0)
        self.assertEqual(summary["tests_green_after"], 1)
        self.assertEqual(summary["corrections_after"], 1)

    def test_summary_window_excludes_old_events(self) -> None:
        old = time.time() - 40 * 86400
        self.log.write_text(json.dumps({"ts": old, "type": "shown", "session_id": "s",
                                        "prompt_id": "p", "card": ["a"], "shortlist": [],
                                        "source": "jev"}) + "\n")
        self.assertEqual(outcomes.summarize(30)["shown"], 0)
        self.assertEqual(outcomes.summarize(60)["shown"], 1)

    def test_empty_log_summarizes_to_zero(self) -> None:
        summary = outcomes.summarize()
        self.assertEqual(summary["shown"], 0)
        self.assertEqual(summary["followed_rate"], 0.0)


class TestSignals(OutcomesCase):

    def test_test_command_exit_codes_map_to_signals(self) -> None:
        sig = outcomes.test_signal
        self.assertEqual(sig("python3 -m pytest tests -q", {"exit_code": 0}), "tests_green")
        self.assertEqual(sig("npx vitest run", {"exit_code": 1}), "tests_red")
        self.assertEqual(sig("npm test", {"returncode": 2}), "tests_red")
        self.assertEqual(sig("go test ./...", {"exitCode": 0}), "tests_green")
        self.assertEqual(sig("cargo test", {"is_error": True}), "tests_red")
        self.assertEqual(sig("npx jest", {"exit_code": 0}), "tests_green")
        self.assertIsNone(sig("ls -la", {"exit_code": 0}), "not a test command")
        # Live PostToolUse Bash shape: no exit field. Reaching PostToolUse means success.
        self.assertEqual(sig("pytest -q", {"stdout": "3 passed", "stderr": "",
                                           "interrupted": False}), "tests_green")
        self.assertEqual(sig("pytest", {"stdout": "", "interrupted": True}), "tests_red")
        self.assertEqual(sig("pytest", {"exit_code": 0, "is_error": True}), "tests_green",
                         "an explicit exit code wins over flags")
        self.assertEqual(sig("pytest", "plain string"), "tests_green")
        self.assertIsNone(sig("pytest", None), "no response at all: record nothing")

    def test_live_shaped_post_bash_payload_records_green(self) -> None:
        outcomes.on_prompt("s1", "p1", "run the tests")
        self.run_hook({"session_id": "s1", "hook_event_name": "PostToolUse", "tool_name": "Bash",
                       "tool_input": {"command": "pytest -q"},
                       "tool_response": {"stdout": "...", "stderr": "", "interrupted": False}},
                      "--post-bash")
        self.assertEqual([(e["prompt_id"], e["kind"]) for e in self.events() if e["type"] == "signal"],
                         [("p1", "tests_green")])

    def test_post_bash_failure_event_records_red(self) -> None:
        outcomes.on_prompt("s1", "p1", "run the tests")
        self.run_hook({"session_id": "s1", "hook_event_name": "PostToolUseFailure",
                       "tool_name": "Bash", "tool_input": {"command": "npx vitest run"},
                       "error": "Command failed with exit code 1"}, "--post-bash-failure")
        # A failing non-test command records nothing.
        self.run_hook({"session_id": "s1", "hook_event_name": "PostToolUseFailure",
                       "tool_name": "Bash", "tool_input": {"command": "ls /nope"},
                       "error": "exit 1"}, "--post-bash-failure")
        self.assertEqual([(e["prompt_id"], e["kind"]) for e in self.events() if e["type"] == "signal"],
                         [("p1", "tests_red")])

    def test_post_bash_hook_records_against_latest_turn(self) -> None:
        outcomes.on_prompt("s1", "p1", "fix the failing test")
        self.run_hook({"session_id": "s1", "tool_name": "Bash",
                       "tool_input": {"command": "python3 -m pytest -q"},
                       "tool_response": {"exit_code": 0}}, "--post-bash")
        sigs = [e for e in self.events() if e["type"] == "signal"]
        self.assertEqual([(s["prompt_id"], s["kind"]) for s in sigs], [("p1", "tests_green")])

    def test_post_bash_hook_never_fails(self) -> None:
        for payload in ("not json", "[]", "{}", json.dumps({"tool_input": "x"}),
                        json.dumps({"session_id": "s", "tool_input": {"command": "pytest"},
                                    "tool_response": {"exit_code": "weird"}})):
            with self.subTest(payload=payload):
                proc = self.run_hook(payload, "--post-bash")
                self.assertEqual(proc.stdout.strip(), "")
        self.assertEqual([e for e in self.events() if e["type"] == "signal"], [])

    def test_correction_regex_matches_only_leading_negation(self) -> None:
        yes = ("no, use the other file", "No that's wrong", "wrong file", "not that one",
               "undo that", "revert the last change", "that's not what I asked", "STOP",
               "  stop doing that")
        no = ("I said no", "is this wrong?", "nothing changed", "notable bug in parser",
              "stopwatch feature", "please revert nothing", "know what, add tests", "")
        for p in yes:
            with self.subTest(p=p):
                self.assertTrue(outcomes.is_correction(p))
        for p in no:
            with self.subTest(p=p):
                self.assertFalse(outcomes.is_correction(p))

    def test_correction_is_attributed_to_previous_prompt(self) -> None:
        outcomes.on_prompt("s1", "p1", "add a dark mode toggle")
        outcomes.on_prompt("s2", "q1", "something else entirely")
        outcomes.on_prompt("s1", "p2", "no, not like that")
        outcomes.on_prompt("s1", "p3", "great, now commit")
        sigs = [(e["session_id"], e["prompt_id"], e["kind"])
                for e in self.events() if e["type"] == "signal"]
        self.assertEqual(sigs, [("s1", "p1", "correction")])

    def test_first_prompt_correction_has_nothing_to_attribute(self) -> None:
        outcomes.on_prompt("s1", "p1", "no")
        self.assertEqual([e for e in self.events() if e["type"] == "signal"], [])

    def test_unknown_signal_kind_is_ignored(self) -> None:
        outcomes.record_signal("s1", "p1", "bogus")  # type: ignore[arg-type]
        self.assertEqual(self.events(), [])


class TestRework(OutcomesCase):

    def test_files_hash_ignores_order(self) -> None:
        self.assertEqual(outcomes.files_hash(["b.py", "a.py"]), outcomes.files_hash(["a.py", "b.py", "a.py"]))
        self.assertNotEqual(outcomes.files_hash(["a.py"]), outcomes.files_hash(["b.py"]))

    def test_rework_is_same_file_set_edited_after_green_turn(self) -> None:
        t0 = 1_000_000.0
        h = outcomes.files_hash(["src/a.py", "src/b.py"])
        events = [
            {"ts": t0, "type": "edits", "session_id": "s", "prompt_id": "p1", "files": h},
            {"ts": t0 + 5, "type": "signal", "session_id": "s", "prompt_id": "p1", "kind": "tests_green"},
            {"ts": t0 + 3600, "type": "edits", "session_id": "s", "prompt_id": "p2", "files": h},
            # Same set, but more than 24 h after the green turn: not rework.
            {"ts": t0, "type": "edits", "session_id": "t", "prompt_id": "q1", "files": h},
            {"ts": t0, "type": "signal", "session_id": "t", "prompt_id": "q1", "kind": "tests_green"},
            {"ts": t0 + 90000, "type": "edits", "session_id": "t", "prompt_id": "q2", "files": h},
            # Different set after green: not rework.
            {"ts": t0, "type": "edits", "session_id": "u", "prompt_id": "r1", "files": h},
            {"ts": t0, "type": "signal", "session_id": "u", "prompt_id": "r1", "kind": "tests_green"},
            {"ts": t0 + 10, "type": "edits", "session_id": "u", "prompt_id": "r2",
             "files": outcomes.files_hash(["other.py"])},
        ]
        self.assertEqual(outcomes.detect_rework(events), [("s", "p1")])

    def test_record_edits_stores_a_hash_not_paths(self) -> None:
        outcomes.record_edits("s", "p", ["/Users/me/secret/project/a.py"])
        raw = self.log.read_text()
        self.assertNotIn("secret", raw)
        self.assertEqual(self.events()[0]["files"], outcomes.files_hash(["/Users/me/secret/project/a.py"]))


class TestPrivacyAndStorage(OutcomesCase):

    def test_no_prompt_text_is_ever_written(self) -> None:
        secret = "please fix the login bug in my super secret payroll module " * 10
        outcomes.on_prompt("s1", "p1", secret)
        outcomes.on_prompt("s1", "p2", "no, " + secret)
        outcomes.record_shown("s1", "p1", ["alpha"], ["beta"], "jev")
        outcomes.record_signal("s1", "p1", "loaded", "x" * 500)
        self.run_hook({"session_id": "s1", "prompt_id": "p2",
                       "tool_input": {"command": "pytest " + secret},
                       "tool_response": {"exit_code": 1, "stdout": secret}}, "--post-bash")
        raw = self.log.read_text()
        self.assertNotIn("payroll", raw)

        def walk(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    self.assertNotEqual(k, "prompt")
                    yield from walk(v)
            elif isinstance(obj, list):
                for v in obj:
                    yield from walk(v)
            else:
                yield obj

        events = self.events()
        self.assertTrue(events)
        for e in events:
            for v in walk(e):
                self.assertLessEqual(len(str(v)), 200)

    def test_log_is_created_mode_600(self) -> None:
        outcomes.record_signal("s1", "p1", "tests_green")
        self.assertEqual(stat.S_IMODE(self.log.stat().st_mode), 0o600)

    def test_no_learn_disables_writes(self) -> None:
        os.environ["SKILL_ROUTER_NO_LEARN"] = "1"
        outcomes.record_shown("s1", "p1", ["a"], [], "jev")
        self.assertFalse(self.log.exists())

    def test_default_path_is_under_claude_dir(self) -> None:
        os.environ.pop("SKILL_ROUTER_OUTCOMES_FILE")
        self.assertEqual(outcomes.outcomes_path(),
                         Path.home() / ".claude" / "skill_router_outcomes.jsonl")

    def test_unwritable_log_never_raises(self) -> None:
        blocker = self.tmp / "a-file"
        blocker.write_text("")
        os.environ["SKILL_ROUTER_OUTCOMES_FILE"] = str(blocker / "outcomes.jsonl")
        outcomes.record_shown("s1", "p1", ["a"], [], "jev")
        outcomes.record_signal("s1", "p1", "loaded", "a")
        outcomes.on_prompt("s1", "p2", "no")
        self.assertIsInstance(outcomes.summarize(), dict)


class TestWiring(OutcomesCase):

    def test_router_extracts_what_it_printed(self) -> None:
        import router  # type: ignore[import-not-found]
        step = router.Step("systematic-debugging", "general-purpose", "inherit", "think")
        text = ("[skill-router] some card\n"
                "[skill-router] Possibly relevant installed skills (Jev's top picks, below its "
                "routing bar; load one only if it clearly fits): alpha · beta · gamma")
        self.assertEqual(router._shown_from_output([step], text),
                         (["systematic-debugging"], ["alpha", "beta", "gamma"]))
        text = ("[skill-router] Possibly relevant installed skills (unranked; load one only "
                "if it clearly fits): delta")
        self.assertEqual(router._shown_from_output([], text), ([], ["delta"]))
        text = router.JEV_PREFIX + "superpowers:subagent-driven-development · epsilon"
        self.assertEqual(router._shown_from_output([], text),
                         ([], ["superpowers:subagent-driven-development", "epsilon"]))
        text = router.KEYLESS_PREFIX + "zeta"
        self.assertEqual(router._shown_from_output([], text), ([], ["zeta"]))
        text = router._suggestion_line("frontend-design", 0.62)
        self.assertEqual(router._shown_from_output([], text), ([], ["frontend-design"]))
        self.assertEqual(router._shown_from_output([], ""), ([], []))

    def test_router_hook_mode_records_turn_shown_and_correction(self) -> None:
        (self.tmp / ".claude").mkdir(exist_ok=True)
        env = {**os.environ, "HOME": str(self.tmp), "SKILL_ROUTER_HOOK_MODE": "1",
               "SKILL_ROUTER_SESSION_DIR": str(self.tmp / "sessions"),
               "SKILL_ROUTER_JEV": "0", "SKILL_ROUTER_LLM": "0"}
        for pid, prompt in (("P1", "TypeError: cannot read property map of undefined"),
                            ("P2", "no, wrong file")):
            proc = subprocess.run([sys.executable, str(SCRIPTS / "router.py")],
                                  input=json.dumps({"session_id": "S", "prompt_id": pid,
                                                    "prompt": prompt}),
                                  capture_output=True, text=True, timeout=60, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        events = self.events()
        first_shown = next(e for e in events if e["type"] == "shown")
        self.assertEqual(first_shown["prompt_id"], "P1")
        self.assertEqual(first_shown["source"], "keyless")
        self.assertTrue(first_shown["card"])
        self.assertIn(("P1", "correction"),
                      [(e["prompt_id"], e["kind"]) for e in events if e["type"] == "signal"])
        self.assertEqual([e["prompt_id"] for e in events if e["type"] == "turn"], ["P1", "P2"])

    def test_skill_invoked_records_loaded_signal(self) -> None:
        outcomes.on_prompt("s1", "p1", "debug this")
        env = {**os.environ, "HOME": str(self.tmp),
               "SKILL_ROUTER_SESSION_DIR": str(self.tmp / "sessions")}
        for payload in ({"session_id": "s1", "tool_input": {"skill": "alpha"}},
                        {"session_id": "s1", "prompt_id": "p7", "tool_input": {"skill": "beta"}},
                        {"session_id": "s1", "agent_id": "a1", "tool_input": {"skill": "gamma"}}):
            proc = subprocess.run([sys.executable, str(SCRIPTS / "skill_invoked.py")],
                                  input=json.dumps(payload), capture_output=True, text=True,
                                  timeout=30, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
        sigs = [(e["prompt_id"], e["kind"], e["skill"]) for e in self.events() if e["type"] == "signal"]
        self.assertEqual(sigs, [("p1", "loaded", "alpha"), ("p7", "loaded", "beta")],
                         "sub-agent loads are not the parent following a shown line")

    def test_installer_wires_post_bash_hook(self) -> None:
        import install_hooks  # type: ignore[import-not-found]
        groups = install_hooks.managed_hooks()["PostToolUse"]
        bash = [g for g in groups if g.get("matcher") == "Bash"]
        self.assertEqual(len(bash), 1)
        cmd = bash[0]["hooks"][0]["command"]
        self.assertTrue("outcomes.py --post-bash ;; esac" in cmd, cmd)
        self.assertTrue(install_hooks._is_ours({"command": cmd}))
        self.assertIn("skill-router/scripts/outcomes.py", install_hooks.OWNED_SCRIPTS)
        failure = install_hooks.managed_hooks()["PostToolUseFailure"]
        self.assertEqual([g.get("matcher") for g in failure], ["Bash"])
        self.assertTrue("outcomes.py --post-bash-failure ;; esac" in failure[0]["hooks"][0]["command"])

    def _run_wrapped(self, command: str, payload: dict, stub: Path):
        import subprocess
        cmd = command.replace(
            next(tok for tok in command.split() if tok.endswith("outcomes.py")), str(stub))
        return subprocess.run(["sh", "-c", cmd], input=json.dumps(payload),
                              capture_output=True, text=True, timeout=30)

    def _stub(self, d: str) -> tuple[Path, Path]:
        marker = Path(d) / "ran"
        stub = Path(d) / "stub.py"
        stub.write_text(f"import sys\nopen({str(marker)!r}, 'a').write(sys.stdin.read())\n")
        return stub, marker

    def test_non_test_command_does_not_start_python(self) -> None:
        import install_hooks  # type: ignore[import-not-found]
        import tempfile
        command = install_hooks.managed_hooks()["PostToolUse"][-1]["hooks"][0]["command"]
        with tempfile.TemporaryDirectory() as d:
            stub, marker = self._stub(d)
            proc = self._run_wrapped(command, {"tool_input": {"command": "ls"}}, stub)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertFalse(marker.exists())

    def test_test_command_reaches_outcomes(self) -> None:
        import install_hooks  # type: ignore[import-not-found]
        import tempfile
        for event in ("PostToolUse", "PostToolUseFailure"):
            command = install_hooks.managed_hooks()[event][-1]["hooks"][0]["command"]
            for runner in outcomes.TEST_RUNNERS:
                with tempfile.TemporaryDirectory() as d:
                    stub, marker = self._stub(d)
                    payload = {"tool_input": {"command": f"{runner} -q"}}
                    self._run_wrapped(command, payload, stub)
                    self.assertTrue(marker.exists(), (event, runner))
                    self.assertEqual(json.loads(marker.read_text()), payload)

    def test_adversarial_payload_arrives_byte_equal(self) -> None:
        import install_hooks  # type: ignore[import-not-found]
        import tempfile
        command = install_hooks.managed_hooks()["PostToolUse"][-1]["hooks"][0]["command"]
        nasty = "pytest -k 'a\"b' $HOME `id` $(id) \\n\nline2 ;; esac %s"
        payload = {"tool_input": {"command": nasty}}
        raw = json.dumps(payload, ensure_ascii=False)
        with tempfile.TemporaryDirectory() as d:
            stub, marker = self._stub(d)
            import subprocess
            cmd = command.replace(next(t for t in command.split() if t.endswith("outcomes.py")), str(stub))
            subprocess.run(["sh", "-c", cmd], input=raw, text=True, timeout=30, check=True)
            self.assertEqual(marker.read_text(), raw)

    def test_doctor_treats_failure_event_as_optional(self) -> None:
        import doctor  # type: ignore[import-not-found]
        self.assertNotIn("PostToolUseFailure", doctor.REQUIRED_EVENTS)
        self.assertIn("PostToolUseFailure", doctor.OPTIONAL_EVENTS)

    def test_settings_hooks_json_mirrors_installer(self) -> None:
        shipped = json.loads((ROOT / "settings-hooks.json").read_text())["hooks"]
        fail_cmds = [h["command"] for g in shipped["PostToolUseFailure"] for h in g["hooks"]]
        self.assertTrue(any("outcomes.py --post-bash-failure ;; esac" in c for c in fail_cmds))
        matchers = {g.get("matcher") for g in shipped["PostToolUse"]}
        self.assertEqual(matchers, {"Skill", "Bash"})
        cmds = [h["command"] for g in shipped["PostToolUse"] for h in g["hooks"]]
        self.assertTrue(any("outcomes.py --post-bash ;; esac" in c for c in cmds))



class ShapeCaptureCase(OutcomesCase):
    def setUp(self) -> None:
        super().setUp()
        self.shapes = self.tmp / "shapes.jsonl"
        self._shape_env = {k: os.environ.get(k) for k in
                           ("SKILL_ROUTER_SHAPES_FILE", "SKILL_ROUTER_CAPTURE_SHAPES")}
        os.environ["SKILL_ROUTER_SHAPES_FILE"] = str(self.shapes)
        os.environ["SKILL_ROUTER_CAPTURE_SHAPES"] = "1"

    def tearDown(self) -> None:
        for k, v in self._shape_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        super().tearDown()

    def test_shape_has_no_values(self) -> None:
        payload = {"session_id": "SECRET-SID", "tool_name": "Bash",
                   "tool_input": {"command": "echo SECRET-CMD"},
                   "tool_response": {"stdout": "SECRET-OUT", "files": ["a", "b"]}}
        self.run_hook(payload, "--post-bash")
        text = self.shapes.read_text()
        self.assertNotIn("SECRET", text)
        rec = json.loads(text.splitlines()[0])
        self.assertEqual(rec["tool"], "Bash")
        self.assertEqual(rec["event"], "PostToolUse")
        self.assertEqual(rec["shape"]["tool_input.command"], "str")
        self.assertEqual(rec["shape"]["tool_response.files"], "list[str]")
        self.assertEqual(stat.S_IMODE(self.shapes.stat().st_mode), 0o600)

    def test_shape_capture_capped_at_50(self) -> None:
        for _ in range(55):
            outcomes.capture_shape({"tool_name": "Bash", "a": 1}, "PostToolUse")
        self.assertEqual(len(self.shapes.read_text().splitlines()), 50)

    def test_capture_off_by_default(self) -> None:
        os.environ.pop("SKILL_ROUTER_CAPTURE_SHAPES")
        outcomes.capture_shape({"a": 1}, "PostToolUse")
        self.assertFalse(self.shapes.exists())

if __name__ == "__main__":
    unittest.main()
