"""listing_probe: parse a synthetic /context report, cache it, feed the audit and the doctor."""
from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import cli  # type: ignore[import-not-found]  # noqa: E402
import doctor  # type: ignore[import-not-found]  # noqa: E402
import listing_probe as lp  # type: ignore[import-not-found]  # noqa: E402
import skill_audit  # type: ignore[import-not-found]  # noqa: E402

SAMPLE = """## Context Usage

**Model:** some-model
**Tokens:** 37.2k / 1m (4%)

### Estimated usage by category

| Category | Tokens | Percentage |
|----------|--------|------------|
| System prompt | 2.5k | 0.3% |
| Skills | 9.8k | 1.0% |
| Messages | 4.5k | 0.4% |

### Memory Files

| Type | Path | Tokens |
|------|------|--------|
| User | x | 100 |

### Skills

| Skill | Source | Tokens |
|-------|--------|--------|
| alpha-one | User | ~60 |
| beta-two | Plugin | < 20 |
| gamma:three | claude.ai sync | < 20 |
"""


def _runner(stdout=SAMPLE, rc=0, seen=None):
    def run(cmd, **kw):
        if seen is not None:
            seen.update(cmd=cmd, **kw)
        return SimpleNamespace(returncode=rc, stdout=stdout, stderr="")
    return run


def test_parses_skills_row_and_table():
    p = lp.parse_context(SAMPLE)
    assert p["skills_tokens"] == 9800 and p["context_window"] == 1_000_000
    assert [(e["name"], e["described"], e["tokens"]) for e in p["entries"]] == [
        ("alpha-one", True, 60), ("beta-two", False, None), ("gamma:three", False, None)]
    assert p["entries"][2]["source"] == "claude.ai sync"


def test_unknown_format_returns_none():
    assert lp.parse_context("hello") is None
    assert lp.parse_context("## Context Usage\n\nnothing else") is None
    assert lp.parse_context("") is None


def test_odd_token_form_or_total_mismatch_returns_none():
    assert lp.parse_context(SAMPLE.replace("| ~60 |", "| 60 |")) is None
    assert lp.parse_context(SAMPLE.replace("| ~60 |", "| about 60 |")) is None
    assert lp.parse_context(SAMPLE.replace("| ~60 |", "| ~10k |")) is None     # 10k > Skills 9.8k


def test_cache_dir_tightened_when_loose(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    lp.cache_dir().mkdir(parents=True, exist_ok=True)
    os.chmod(lp.cache_dir(), 0o755)
    lp.save("k", {"entries": []})
    assert stat.S_IMODE(lp.cache_dir().stat().st_mode) == 0o700


def test_claude_missing_is_clean_error(tmp_path):
    def gone(*a, **k):
        raise FileNotFoundError("claude")
    with pytest.raises(lp.ProbeError, match="not found"):
        lp.measure(tmp_path, runner=gone)

    def slow(*a, **k):
        raise subprocess.TimeoutExpired("claude", 300)
    with pytest.raises(lp.ProbeError, match="300 s"):
        lp.measure(tmp_path, runner=slow)
    with pytest.raises(lp.ProbeError, match="unrecognised"):
        lp.measure(tmp_path, runner=_runner("junk"))


def test_cache_mode_600_and_atomic(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    seen: dict = {}
    m = lp.measure(tmp_path, runner=_runner(seen=seen))
    assert seen["cmd"] == ["claude", "-p", "/context", "--output-format", "text"]
    assert seen["env"]["SKILL_ROUTER_OFF"] == "1" and seen["timeout"] == 300
    assert seen["stdin"] == subprocess.DEVNULL
    assert seen["cwd"] == str(tmp_path.resolve())
    path = lp.cache_dir() / f"{m['project_key']}.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(lp.cache_dir().stat().st_mode) == 0o700
    assert [p.name for p in lp.cache_dir().iterdir()] == [path.name]      # no temp leftovers
    assert lp.load(m["project_key"])["skills_tokens"] == 9800
    assert lp.load("nope") is None


def test_stale_measurement_is_labelled(tmp_path):
    m = lp.measure(tmp_path, runner=_runner())
    assert lp.is_fresh(m) and "stale" not in lp.line(m)
    assert "1 of 3 entries described; Skills ~9800 tokens" in lp.line(m)
    old = dict(m, measured_at=time.time() - 8 * 86400)
    assert not lp.is_fresh(old) and "stale" in lp.line(old)


def _catalog():
    return [{"name": f"s{i}", "description": "does a useful thing for the user " * 3,
             "type": "skill", "kind": "skill"} for i in range(4)]


def test_audit_prefers_fresh_measurement(tmp_path, monkeypatch):
    base = skill_audit.audit(_catalog(), {}, 0, None, None, jev=None, budget_s=0.0)
    m = lp.parse_context(SAMPLE)
    rep = skill_audit.audit(_catalog(), {}, 0, None, None, jev=None, budget_s=0.0, measured=m)
    assert (rep.measured_entries, rep.measured_described, rep.descriptions_dropped) == (3, 1, 2)
    assert rep.score <= base.score
    text = cli.render(rep, "text")
    assert "measured: 1 of 3 entries described; Skills ~9800 tokens" in text
    assert "measured:" not in cli.render(base, "text")
    # run_audit picks up the cached measurement for the project
    lp.measure(tmp_path, runner=_runner())
    monkeypatch.setattr(skill_audit, "load_inputs", lambda: (_catalog(), {}, 0, None, None))
    assert "measured: 1 of 3" in cli.run_audit("text", True, project=str(tmp_path))


def _meas(user_described, user_total, tokens, window, extra=()):
    ents = [{"name": f"u{i}", "source": "User", "described": i < user_described, "tokens": None}
            for i in range(user_total)]
    ents += [{"name": f"x{i}", "source": src, "described": False, "tokens": None}
             for i, src in enumerate(extra)]
    return {"skills_tokens": tokens, "entries": ents, "context_window": window}


def _score(m):
    return skill_audit.audit(_catalog(), {}, 0, None, None, jev=None, budget_s=0.0, measured=m)


def test_measured_penalty_scales_with_actionable_share_and_window_share():
    extra = ["claude.ai sync"] * 50 + ["Built-in"] * 10        # never actionable
    all_listed = _score(_meas(10, 10, 50_000, 1_000_000, extra))
    assert all_listed.measured_actionable_dropped == 0
    half = _score(_meas(5, 10, 50_000, 1_000_000, extra))      # 5% window: full scale
    none = _score(_meas(0, 10, 50_000, 1_000_000, extra))
    assert (half.measured_actionable, half.measured_actionable_dropped) == (10, 5)
    assert all_listed.score - half.score == 10 and all_listed.score - none.score == 20
    low = _score(_meas(0, 10, 10_000, 1_000_000, extra))       # 1% window: a fifth of the penalty
    assert all_listed.score - low.score == 4
    unknown = _score(_meas(0, 10, 10_000, None, extra))        # unknown window: factor 1.0
    assert all_listed.score - unknown.score == 20
    assert any("10 of 10 of your skills are listed by name only" in a
               and "~1.0% of the context window" in a for a in low.top_actions)
    assert not any("listed by name only" in a for a in all_listed.top_actions)


def test_doctor_line_never_runs_probe(capsys, monkeypatch, tmp_path):
    def boom(*a, **k):
        raise AssertionError("doctor must not run the probe")
    monkeypatch.setattr(subprocess, "run", boom)
    monkeypatch.setattr(lp, "measure", boom)
    monkeypatch.chdir(tmp_path)
    r = doctor.Report(quiet=False)
    doctor.check_listing(r)
    out = capsys.readouterr().out
    assert r.failed == 0 and "not measured" in out and "listing measure" in out
    from project_usage import project_key  # type: ignore[import-not-found]
    lp.save(project_key(tmp_path), dict(lp.parse_context(SAMPLE), measured_at=time.time()))
    doctor.check_listing(r)
    assert "1 of 3 entries described" in capsys.readouterr().out
