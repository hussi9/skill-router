"""The doctor's `skill audit` line is advice only: it can never fail the doctor."""
from __future__ import annotations

import sys

import pytest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import doctor  # type: ignore[import-not-found]  # noqa: E402
import skill_audit  # type: ignore[import-not-found]  # noqa: E402


def _run(capsys) -> tuple[doctor.Report, str]:
    r = doctor.Report(quiet=False)
    doctor.check_skill_audit(r)
    return r, capsys.readouterr().out


@pytest.fixture(autouse=True)
def _no_live_settings(monkeypatch):
    monkeypatch.setattr(skill_audit, "load_settings", lambda *a, **k: {})


def test_doctor_audit_line_is_advisory(capsys, monkeypatch):
    monkeypatch.setattr(skill_audit, "load_inputs", lambda: ([], {}, 0, None, None))
    r, out = _run(capsys)
    assert r.failed == 0
    assert "skill audit" in out and "FAIL" not in out


def test_doctor_audit_line_survives_exception(capsys, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("nope")
    monkeypatch.setattr(skill_audit, "load_inputs", lambda: ([], {}, 0, None, None))
    monkeypatch.setattr(skill_audit, "audit", boom)
    r, out = _run(capsys)
    assert r.failed == 0
    assert "unavailable" in out and "FAIL" not in out


def test_doctor_audit_line_format(capsys, monkeypatch):
    rep = skill_audit.AuditReport(
        skills=3, listing_tokens=23500, unused_90d=[], history_days=0, shown_never_loaded=[],
        overlaps=[], flagged=[], score=32, top_actions=["x" * 200],
        listing_sent_tokens=2000, descriptions_dropped=7)
    monkeypatch.setattr(skill_audit, "load_inputs", lambda: ([], {}, 0, None, None))
    monkeypatch.setattr(skill_audit, "audit", lambda *a, **k: rep)
    r, out = _run(capsys)
    assert "score 32/100" in out and "~2.0k of ~23.5k (uncapped) listing tokens fit; 7 descriptions dropped" in out
    assert "x" * 91 not in out
