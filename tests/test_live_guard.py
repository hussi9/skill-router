"""The suite-wide guard (tests/conftest.py) blocks any touch of the live ~/.claude."""
from __future__ import annotations

import os

import pytest

import conftest  # type: ignore[import-not-found]

_REAL_HOME = conftest._REAL_HOME_STR  # real home captured by conftest before it swaps HOME


def test_live_claude_read_is_blocked(monkeypatch):
    monkeypatch.delenv("SKILL_ROUTER_CHECK_LIVE", raising=False)
    path = os.path.join(_REAL_HOME, ".claude", "no-such-file-guard-probe.json")
    with pytest.raises(PermissionError, match="live ~/.claude"):
        open(path)
    with pytest.raises(PermissionError):
        os.listdir(os.path.join(_REAL_HOME, ".claude"))
    conftest.LIVE_TRIPS.clear()  # these trips were the point of the test


def test_temp_home_is_allowed(tmp_path, monkeypatch):
    monkeypatch.delenv("SKILL_ROUTER_CHECK_LIVE", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    f = tmp_path / ".claude" / "x.json"
    f.parent.mkdir()
    f.write_text("{}")
    assert open(f).read() == "{}"
    assert os.listdir(tmp_path / ".claude") == ["x.json"]


def test_check_live_env_allows(monkeypatch):
    monkeypatch.setenv("SKILL_ROUTER_CHECK_LIVE", "1")
    path = os.path.join(_REAL_HOME, ".claude", "no-such-file-guard-probe.json")
    with pytest.raises(FileNotFoundError):
        open(path)


def test_swallowed_trip_is_still_recorded(monkeypatch):
    monkeypatch.delenv("SKILL_ROUTER_CHECK_LIVE", raising=False)
    conftest.LIVE_TRIPS.clear()
    try:
        open(os.path.join(_REAL_HOME, ".claude", "x"))
    except PermissionError:
        pass
    assert len(conftest.LIVE_TRIPS) == 1
    conftest.LIVE_TRIPS.clear()
