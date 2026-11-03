"""Gate mode: machine-state checks warn, everything else behaves as before."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import doctor  # type: ignore[import-not-found]  # noqa: E402


def _agents(tmp_path, monkeypatch, files):
    d = tmp_path / ".claude" / "agents"
    d.mkdir(parents=True)
    for name, text in files.items():
        (d / name).write_text(text)
    monkeypatch.setattr(doctor, "HOME", tmp_path)


def test_machine_checks_fail_normally_and_warn_in_gate(tmp_path, monkeypatch, capsys):
    _agents(tmp_path, monkeypatch, {
        "a.md": "---\nname: a\ndescription: d\nmodel: sonnet\n---\n",
        "b.md": "# no frontmatter\n"})
    plain = doctor.Report(False)
    doctor.check_agent_models(plain)
    doctor.check_agents_register(plain)
    assert plain.failed == 2
    capsys.readouterr()
    gate = doctor.Report(False, gate=True)
    doctor.check_agent_models(gate)
    doctor.check_agents_register(gate)
    out = capsys.readouterr().out
    assert gate.failed == 0 and out.count("[warn]") == 2 and "FAIL" not in out


def test_non_machine_failure_still_fails_in_gate(capsys):
    r = doctor.Report(False, gate=True)
    r.check("other", False, "bad")
    r.check("machine", False, "bad", machine=True)
    assert r.failed == 1
    assert "[FAIL] other" in capsys.readouterr().out


def test_env_var_enables_gate(monkeypatch):
    monkeypatch.setenv("SKILL_ROUTER_DOCTOR_GATE", "1")
    monkeypatch.setattr(sys, "argv", ["doctor.py", "--quiet"])
    seen = {}
    orig = doctor.Report.__init__
    def spy(self, quiet, gate=False):
        seen["gate"] = gate
        orig(self, quiet, gate)
    monkeypatch.setattr(doctor.Report, "__init__", spy)
    for name in [n for n in dir(doctor) if n.startswith("check_")]:
        monkeypatch.setattr(doctor, name, lambda r: None)
    assert doctor.main() == 0 and seen["gate"] is True


def test_audit_md_and_html_put_top_actions_right_after_score():
    import cli  # type: ignore[import-not-found]
    import skill_audit  # type: ignore[import-not-found]
    rep = skill_audit.AuditReport(
        skills=3, listing_tokens=1000, unused_90d=[], history_days=0, shown_never_loaded=[],
        overlaps=[], flagged=[], score=90, top_actions=["Do the thing"])
    md = cli.render(rep, "md")
    assert md.index("Top actions") < md.index("Overlaps")
    html = cli.render(rep, "html")
    assert html.index("Top actions") < html.index("Overlaps")
