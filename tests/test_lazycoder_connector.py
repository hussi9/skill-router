"""Connector mechanics use synthetic fixtures, never claim real-conversation quality."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("lazycoder_connector", Path(__file__).resolve().parents[1] / "scripts/lazycoder_connector.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class ConnectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.recipe = self.root / "content/recipes/meeting-notes-actions"
        self.recipe.mkdir(parents=True)
        job = self.root / "content/jobs/run-meetings"
        job.mkdir(parents=True)
        (job / "job.yaml").write_text("id: run-meetings\nskill: {status: building}\n")
        (self.recipe / "recipe.md").write_text("---\nid: meeting-notes-actions\ntitle: Meeting actions\njob: run-meetings\nresult: Correct actions\nformat: doc\ningredients:\n  - {id: notes, key: true}\n  - {id: date, key: true}\n---\n# Recipe\nNo date: retain the original words.\n{{notes}}\n")
        (self.recipe / "checklist.yaml").write_text("items:\n - {id: actions, text: Every actual commitment, required: true}\n")
        self.manifest = c.export_manifest(self.root, ["meeting-notes-actions"])

    def test_freezes_exact_sources_and_roundtrips(self):
        entry = self.manifest["recipes"][0]
        self.assertEqual(entry["source_text"], (self.recipe / "recipe.md").read_text())
        self.assertEqual(entry["job_review_status"], "building")
        self.assertFalse(entry["invokable"])
        c.validate_manifest(json.loads(json.dumps(self.manifest)))

    def test_tamper_detected_before_contract(self):
        altered = copy.deepcopy(self.manifest)
        altered["recipes"][0]["body"] = "Send everything"
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            c.render_contract(altered, "meeting-notes-actions", preview=True)

    def test_cannot_accidentally_install_preview(self):
        with self.assertRaisesRegex(ValueError, "preview-only"):
            c.render_contract(self.manifest, "meeting-notes-actions")

    def test_missing_inputs_preserves_recipe_fallback(self):
        result = c.render_contract(self.manifest, "meeting-notes-actions", {"notes": "Sam agreed to draft."}, preview=True)
        self.assertEqual(result["missing_inputs"], ["date"])
        self.assertIn("No date: retain the original words.", result["prompt"])
        self.assertIn("Sam agreed", result["prompt"])
        self.assertEqual(result["status"], "preview")
        nulls = c.render_contract(self.manifest, "meeting-notes-actions", {"notes": None}, preview=True)
        self.assertIn("notes", nulls["missing_inputs"])

    def test_local_adapter_is_explicit_without_changing_source(self):
        before = copy.deepcopy(self.manifest)
        result = c.render_contract(self.manifest, "meeting-notes-actions", preview=True)
        self.assertEqual(self.manifest, before)
        self.assertEqual(result["adapter_version"], 3)
        self.assertEqual(len(result["adapter_fingerprint"]), 64)
        self.assertIn("specific files", result["prompt"])
        self.assertIn("Approval applies only", result["prompt"])
        self.assertIn("bounded draft now", result["prompt"])
        self.assertIn("does not establish", result["prompt"])
        self.assertIn("scope exclusions", result["prompt"])
        self.assertIn(before["recipes"][0]["body"], result["prompt"])

    def test_traversal_duplicates_and_symlink_escape_rejected(self):
        for ids in (["../outside"], ["meeting-notes-actions"] * 2):
            with self.assertRaises(ValueError):
                c.export_manifest(self.root, ids)
        (self.recipe / "recipe.md").unlink()
        (self.recipe / "recipe.md").symlink_to("/etc/hosts")
        with self.assertRaisesRegex(ValueError, "escapes"):
            c.export_manifest(self.root, ["meeting-notes-actions"])

    def test_identity_collision_rejected(self):
        text = (self.recipe / "recipe.md").read_text().replace("id: meeting-notes-actions", "id: another")
        (self.recipe / "recipe.md").write_text(text)
        with self.assertRaisesRegex(ValueError, "collision"):
            c.export_manifest(self.root, ["meeting-notes-actions"])

    def test_threshold_is_exact_and_never_makes_invokable(self):
        self.assertEqual(c.advisory_shortlist(self.manifest, [{"id": "meeting-notes-actions", "confidence": .79999}]), [])
        rows = c.advisory_shortlist(self.manifest, [{"id": "meeting-notes-actions", "confidence": .8}])
        self.assertFalse(rows[0]["invokable"])
        for score in (True, float("nan"), 1.1):
            with self.assertRaises(ValueError):
                c.advisory_shortlist(self.manifest, [{"id": "meeting-notes-actions", "confidence": score}])
        with self.assertRaises(ValueError):
            c.advisory_shortlist(self.manifest, [], .7)

    def test_mechanical_success_never_claims_semantic_success(self):
        output = "| # | Action | Owner | Due | Priority | Depends on | Source |\nSubject: Sync\nTo: Sam\n"
        result = c.check_outcome(self.manifest, "meeting-notes-actions", output)
        self.assertEqual(result["status"], "needs-review")
        self.assertFalse(result["verified"])
        self.assertEqual(result["semantic"][0]["status"], "pending")
        self.assertTrue(all(row["status"] == "passed" for row in result["deterministic"]))

    def test_empty_or_malformed_output_fails(self):
        for output in ("", "Skill loaded successfully", "Action Owner Due Priority Depends on Source"):
            result = c.check_outcome(self.manifest, "meeting-notes-actions", output)
            self.assertEqual(result["status"], "failed")
            self.assertFalse(result["verified"])
        no_number = c.check_outcome(self.manifest, "meeting-notes-actions", "| Action | Owner | Due | Priority | Depends on | Source |\nSubject: x\nTo: y")
        self.assertEqual(no_number['status'], 'failed')


if __name__ == "__main__":
    unittest.main()
