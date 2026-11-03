"""Hygiene gate: personal identifiers and real prompts must not reach commits."""
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import hygiene  # noqa: E402

PROMPT = "please refactor the billing webhook handler so retries are idempotent across regions"


class HygieneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def _file(self, name, text):
        p = self.tmp / name
        p.write_text(text)
        return p

    def test_pattern_hit_reported_without_text(self):
        f = self._file("a.md", "fine\nthe zorblax token here\n")
        pf = self._file("pats", "# c\nzorb[l]ax\n")
        hits = hygiene.scan([f], hygiene.load_patterns(pf), set())
        self.assertEqual(hits, [(str(f), 2, "pattern:1")])
        self.assertNotIn("zorblax", repr(hits))

    def test_prompt_window_hit_detected(self):
        cases = self._file("cases.json", json.dumps([{"prompt": PROMPT}]))
        f = self._file("doc.md", "intro\nHe said: Refactor the billing, webhook handler so retries are idempotent!\n")
        hashes = hygiene.prompt_hashes([cases])
        hits = hygiene.scan([f], [], hashes)
        self.assertEqual(hits, [(str(f), 2, "prompt-window")])

    def test_prompt_window_across_adjacent_lines(self):
        cases = self._file("cases.json", json.dumps([{"prompt": PROMPT}]))
        f = self._file("doc.md", "refactor the billing webhook\nhandler so retries are idempotent\n")
        hits = hygiene.scan([f], [], hygiene.prompt_hashes([cases]))
        self.assertTrue(hits)

    def test_short_overlap_not_flagged(self):
        cases = self._file("cases.json", json.dumps([{"prompt": PROMPT}]))
        f = self._file("doc.md", "refactor the billing webhook handler so\n")
        self.assertEqual(hygiene.scan([f], [], hygiene.prompt_hashes([cases])), [])

    def test_missing_pattern_file_skips(self):
        r = subprocess.run([sys.executable, str(ROOT / "scripts/hygiene.py"), "--root", str(self.tmp)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertIn("hygiene: no pattern file, skipped", r.stdout)

    def test_clean_tree_passes(self):
        subprocess.run(["git", "init", "-q"], cwd=self.tmp, check=True)
        self._file("ok.md", "nothing sensitive\n")
        self._file(".hygiene-patterns", "zorblax\n")
        subprocess.run(["git", "add", "ok.md"], cwd=self.tmp, check=True)
        r = subprocess.run([sys.executable, str(ROOT / "scripts/hygiene.py"), "--root", str(self.tmp)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_cli_exit_1_on_hit_without_text(self):
        subprocess.run(["git", "init", "-q"], cwd=self.tmp, check=True)
        self._file("bad.md", "has zorblax inside\n")
        self._file(".hygiene-patterns", "zorblax\n")
        subprocess.run(["git", "add", "bad.md"], cwd=self.tmp, check=True)
        r = subprocess.run([sys.executable, str(ROOT / "scripts/hygiene.py"), "--root", str(self.tmp)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("bad.md:1 pattern:1", r.stdout)
        self.assertNotIn("zorblax", r.stdout)

    def test_short_prompt_detected_and_tiny_ignored(self):
        cases = self._file("cases.json", json.dumps([{"prompt": "fix flaky zorb tests now"}, {"prompt": "go ahead now"}]))
        f = self._file("d.md", "x\nsaid: Fix flaky zorb tests, now.\nand go ahead now\n")
        hits = hygiene.scan([f], [], hygiene.prompt_hashes([cases]))
        self.assertEqual(hits, [(str(f), 2, "prompt-short")])

    def test_curly_quotes_and_dashes_normalised(self):
        cases = self._file("cases.json", json.dumps([{"prompt": "don't rewrite the billing webhook handler — keep retries idempotent"}]))
        f = self._file("d.md", "don\u2019t rewrite the billing webhook handler\u2014keep retries idempotent\n")
        self.assertTrue(hygiene.scan([f], [], hygiene.prompt_hashes([cases])))

    def _repo(self):
        subprocess.run(["git", "init", "-q"], cwd=self.tmp, check=True)

    def _cli(self):
        return subprocess.run([sys.executable, str(ROOT / "scripts/hygiene.py"), "--root", str(self.tmp)],
                              capture_output=True, text=True)

    def test_cases_wrapper_form_loaded(self):
        cases = self._file("cases.json", json.dumps({"cases": [{"prompt": PROMPT}]}))
        self.assertEqual(len(hygiene.load_prompts([cases])), 1)

    def test_zero_prompts_fails(self):
        self._repo()
        d = self.tmp / "docs" / "jev-eval-x"
        d.mkdir(parents=True)
        (d / "cases.json").write_text(json.dumps([{"text": "no prompt key"}]))
        r = self._cli()
        self.assertEqual(r.returncode, 1)
        self.assertIn("0 prompts loaded", r.stdout)

    def test_untracked_file_scanned(self):
        self._repo()
        self._file(".hygiene-patterns", "zorblax\n")
        self._file("new.md", "zorblax\n")
        r = self._cli()
        self.assertEqual(r.returncode, 1)
        self.assertIn("new.md:1 pattern:1", r.stdout)

    def test_non_git_root_exit_2(self):
        self._file(".hygiene-patterns", "zorblax\n")
        r = self._cli()
        self.assertEqual(r.returncode, 2)
        self.assertNotIn("Traceback", r.stderr)


if __name__ == "__main__":
    unittest.main()
