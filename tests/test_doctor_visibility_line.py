"""The doctor's `skill visibility` line is advice only: reads a cache, never scans, never fails."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import doctor  # type: ignore[import-not-found]  # noqa: E402
import project_usage  # type: ignore[import-not-found]  # noqa: E402
import visibility as vis  # type: ignore[import-not-found]  # noqa: E402


def _run(capsys) -> tuple[doctor.Report, str]:
    r = doctor.Report(quiet=False)
    doctor.check_visibility(r)
    return r, capsys.readouterr().out


def _no_scan(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("doctor must not scan transcripts")
    monkeypatch.setattr(project_usage, "usage_by_project", boom)


def test_visibility_line_is_advisory(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _no_scan(monkeypatch)
    r, out = _run(capsys)                                   # cache absent -> hint
    assert r.failed == 0 and "FAIL" not in out
    assert "visibility plan" in out and "no plan measured" in out

    entry = {"worthwhile": True, "kept_before": 1, "kept_after": 9, "used_total": 10,
             "dropped_before": 5, "dropped_after": 2, "name_only": 3,
             "budget_fraction": 0.09, "budget_extra_tokens": 100}
    path = vis.summary_path()
    path.parent.mkdir(parents=True)
    import json
    import time
    path.write_text(json.dumps({"projects": {"abc": {"names_count": {**entry, "worthwhile": False},
                                                       "names_free": entry}}, "unused_skills": 12,
                                "updated": int(time.time()) - 40 * 86400}))
    r, out = _run(capsys)
    assert r.failed == 0 and "FAIL" not in out
    assert "0 (names count) / 1 (names free)" in out and "archive the 12 unused" in out
    assert "names free: skillListingBudgetFraction 0.090 (+100 tokens/session), reachable in 1 of 1" in out
    assert "names count: skillListingBudgetFraction 0.090" in out and "measured 40 days ago, stale" in out
    assert out.index("archive") < out.index("worthwhile")

    path.write_text("not json")                             # corrupt cache -> still a hint, not a failure
    r, out = _run(capsys)
    assert r.failed == 0 and "FAIL" not in out


def test_write_summary_is_mode_600_and_has_no_names(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    stats = {"project_used_kept": 1, "project_used_total": 4, "dropped": 9,
             "fit_tokens": 0, "name_cost_chars": 0, "budget_chars": 0, "names_in_budget": True}
    p = vis.VisibilityPlan(keep=[], name_only=["secret-skill-name"], plugin_suggestions=[],
                           before=stats, after={**stats, "project_used_kept": 3, "dropped": 7},
                           applicable=True, project_key="k1")
    vis.write_summary("k1", True, p, None, 0, 5)
    vis.write_summary("k1", False, p, 0.05, 50, 5)
    path = vis.summary_path()
    assert path.stat().st_mode & 0o777 == 0o600
    text = path.read_text()
    assert "secret-skill-name" not in text
    got = vis.read_summary()["projects"]["k1"]
    assert got["names_count"]["kept_after"] == 3 and got["names_free"]["budget_fraction"] == 0.05


def test_lever_not_reachable_branch(capsys, monkeypatch, tmp_path):
    import json
    import time
    monkeypatch.setenv("HOME", str(tmp_path))
    entry = {"worthwhile": False, "budget_fraction": None, "budget_extra_tokens": 0}
    path = vis.summary_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"projects": {"a": {"names_count": entry, "names_free": entry},
                                              "b": {"names_count": entry, "names_free": {**entry, "budget_fraction": 0.05}}},
                                "unused_skills": 1, "updated": int(time.time())}))
    r, out = _run(capsys)
    assert r.failed == 0
    assert "names count: not reachable (<= 0.10) in any of 2" in out
    assert "names free: skillListingBudgetFraction 0.050 (+0 tokens/session), reachable in 1 of 2" in out
