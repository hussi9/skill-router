"""Dashboard session aggregation never trusts the obsolete global pending file."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dashboard


class DashboardSessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="dashboard-sessions-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sessions = self.root / "sessions"
        self.sessions.mkdir()
        for target, value in (
            ("ROUTER_LOG", self.root / "router.jsonl"),
            ("USAGE_LOG", self.root / "usage.log"),
            ("PENDING", self.root / "global.json"),
        ):
            patcher = patch.object(dashboard, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        environment = patch.dict(os.environ, {"SKILL_ROUTER_SESSION_DIR": str(self.sessions)})
        environment.start()
        self.addCleanup(environment.stop)
        dashboard.PENDING.write_text(json.dumps({"remaining": ["stale-global"], "name": "old"}))

    def test_two_sessions_are_aggregated_and_global_is_ignored(self):
        for session, name, remaining in (
            ("a", "review", ["design-review"]),
            ("b", "debug", ["systematic-debugging", "test"]),
        ):
            (self.sessions / f"{session}.pending.json").write_text(
                json.dumps({"name": name, "remaining": remaining}))
        before = dashboard.PENDING.read_text()
        self.assertEqual(dashboard.build_summary(7)["pending"], {
            "present": True, "remaining": ["design-review", "systematic-debugging", "test"],
            "name": "review, debug", "sessions": 2,
        })
        self.assertEqual(dashboard.PENDING.read_text(), before)

    def test_stale_global_does_not_make_empty_sessions_pending(self):
        self.assertEqual(dashboard.build_summary(7)["pending"], {
            "present": False, "remaining": [], "name": None, "sessions": 0,
        })

    def test_cleared_malformed_and_route_files_are_not_pending(self):
        for filename, content in (
            ("cleared.pending.json", '{"remaining": []}'),
            ("broken.pending.json", 'invalid'),
            ("wrong-shape.pending.json", '{"remaining": null}'),
            ("a.json", '{"remaining": ["route-card"]}'),
        ):
            (self.sessions / filename).write_text(content)
        self.assertFalse(dashboard.build_summary(7)["pending"]["present"])
