"""Random holdout: a deterministic tenth of soft-routed turns print nothing so lift can be measured.

Run: python3 -m pytest tests/test_holdout.py -q
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import outcomes  # type: ignore[import-not-found]  # noqa: E402
import router  # type: ignore[import-not-found]  # noqa: E402

LINE = "[skill-router] Invoke now: writing-plans"


def _events() -> list[dict]:
    return outcomes._read_events()


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("SKILL_ROUTER_OUTCOMES_FILE", str(tmp_path / "o.jsonl"))
    monkeypatch.setenv("SKILL_ROUTER_HOOK_MODE", "1")
    monkeypatch.delenv("SKILL_ROUTER_OFF", raising=False)
    monkeypatch.setenv("SKILL_ROUTER_SESSION_DIR", str(tmp_path))
    monkeypatch.setattr(router, "log_prompt_event", lambda *a, **k: None)
    monkeypatch.setattr(router, "log_chain", lambda *a, **k: None)
    monkeypatch.setattr(router, "previous_assistant_tail", lambda *_: "")
    monkeypatch.setattr(router, "loaded_this_session", lambda *_: frozenset())


def _run_main(monkeypatch, capsys, route_result, pending_calls=None):
    payload = json.dumps({"prompt": "plan the migration", "session_id": "s1", "prompt_id": "p1"})
    monkeypatch.setattr(sys, "stdin", io.StringIO(payload))
    monkeypatch.delenv("CLAUDE_USER_INPUT", raising=False)
    monkeypatch.setattr(router, "route", lambda prompt: route_result)
    monkeypatch.setattr(router, "write_pending",
                        lambda *a, **k: (pending_calls is not None) and pending_calls.append(a))
    assert router.main() == 0
    return capsys.readouterr().out


def _step():
    return router.Step("writing-plans", "general-purpose", "sonnet", "")


def test_holdout_is_deterministic_per_prompt():
    first = router.holdout("s1", "p1", 0.5)
    assert all(router.holdout("s1", "p1", 0.5) == first for _ in range(5))
    hits = sum(router.holdout("s", f"p{i}", 0.10) for i in range(5000))
    assert 350 < hits < 650            # about a tenth
    assert router.holdout("s", "p", 1.0) is True


def test_rate_zero_never_holds_out(monkeypatch):
    assert not any(router.holdout("s", f"p{i}", 0) for i in range(2000))
    monkeypatch.setenv("SKILL_ROUTER_HOLDOUT", "0")
    assert router.holdout_rate() == 0.0
    monkeypatch.delenv("SKILL_ROUTER_HOLDOUT")
    assert router.holdout_rate() == 0.10


def test_hard_routes_are_never_held_out(monkeypatch, capsys):
    monkeypatch.setenv("SKILL_ROUTER_HOLDOUT", "1")
    # BROKEN path
    out = _run_main(monkeypatch, capsys, ("BROKEN", [_step()], [], LINE))
    assert LINE in out
    # tier: hard on the card
    monkeypatch.setattr(router, "LAST_CARD", router.RouteCard(tier="hard"))
    out = _run_main(monkeypatch, capsys, ("project", [_step()], [], LINE))
    assert LINE in out
    assert not any(e.get("held_out") for e in _events())


def test_held_out_turn_prints_nothing_but_logs_would_show(monkeypatch, capsys):
    monkeypatch.setenv("SKILL_ROUTER_HOLDOUT", "1")
    monkeypatch.setattr(router, "LAST_CARD", router.RouteCard(tier="soft"))
    pending: list = []
    out = _run_main(monkeypatch, capsys, ("plan", [_step()], [], LINE), pending)
    assert out == ""
    assert pending == []               # Stop hook has nothing to ask about
    shown = [e for e in _events() if e.get("type") == "shown"]
    assert len(shown) == 1 and shown[0]["held_out"] is True
    assert "writing-plans" in shown[0]["card"]
    assert outcomes.summarize()["held_out"] == 1
    assert outcomes.summarize()["shown"] == 0


def test_holdout_and_timeout_never_both_print(monkeypatch, capsys):
    monkeypatch.setenv("SKILL_ROUTER_HOLDOUT", "1")
    monkeypatch.setattr(router, "LAST_CARD", router.RouteCard(tier="soft"))
    monkeypatch.setattr(router, "JEV_TIMED_OUT", True)
    out = _run_main(monkeypatch, capsys, ("plan", [_step()], [], LINE))
    assert out == ""                   # one decision: silent, not silent plus a timeout note


def test_cli_mode_is_never_held_out(monkeypatch, capsys):
    monkeypatch.setenv("SKILL_ROUTER_HOLDOUT", "1")
    monkeypatch.setenv("SKILL_ROUTER_HOOK_MODE", "0")
    monkeypatch.setattr(router, "LAST_CARD", router.RouteCard(tier="soft"))
    out = _run_main(monkeypatch, capsys, ("plan", [_step()], [], LINE))
    assert LINE in out


def test_quota_only_output_is_never_held_out(monkeypatch, capsys):
    monkeypatch.setenv("SKILL_ROUTER_HOLDOUT", "1")
    monkeypatch.setattr(router, "LAST_CARD", router.RouteCard(tier="soft"))
    quota = "[skill-router] Quota: 80% used - Kimi mode suggested"
    out = _run_main(monkeypatch, capsys, ("plan", [], [], quota))
    assert quota in out
    assert not any(e.get("held_out") for e in _events())


def test_jev_shortlist_only_output_is_held_out(monkeypatch, capsys):
    monkeypatch.setenv("SKILL_ROUTER_HOLDOUT", "1")
    monkeypatch.setattr(router, "LAST_CARD", router.RouteCard(tier="soft"))
    line = f"{router.SHORTLIST_MARKER}: alpha · beta · gamma"
    out = _run_main(monkeypatch, capsys, ("plan", [], [], line))
    assert out == ""
    held = [e for e in _events() if e.get("held_out")]
    assert len(held) == 1 and held[0]["shortlist"] == ["alpha", "beta", "gamma"]


def test_keyless_shortlist_branch_is_held_out(monkeypatch, capsys):
    monkeypatch.setenv("SKILL_ROUTER_HOLDOUT", "1")
    monkeypatch.setattr(router, "LAST_CARD", router.RouteCard(tier="soft"))
    line = f"{router.SHORTLIST_MARKER}: alpha · beta"
    monkeypatch.setattr(router, "shortlist_line", lambda *a, **k: line)
    monkeypatch.setattr(router, "_shortlist_recent", lambda *a, **k: False)
    out = _run_main(monkeypatch, capsys, ("SKIP", [], [], ""))
    assert out == ""
    held = [e for e in _events() if e.get("held_out")]
    assert len(held) == 1 and held[0]["shortlist"] == ["alpha", "beta"]


def test_empty_prompt_id_is_never_held_out(monkeypatch):
    """With no prompt id the coin would be fixed for the whole session."""
    monkeypatch.setenv("SKILL_ROUTER_HOLDOUT", "1")
    monkeypatch.setenv("SKILL_ROUTER_HOOK_MODE", "1")
    monkeypatch.setattr(router, "LAST_CARD", router.RouteCard(tier="soft"))
    monkeypatch.setattr(router, "SESSION_ID", "s1")
    line = f"{router.SHORTLIST_MARKER}: alpha"
    monkeypatch.setattr(router, "PROMPT_ID", "")
    assert router._held_out_this_turn("PLAN", [], line) is False
    monkeypatch.setattr(router, "PROMPT_ID", "p1")
    assert router._held_out_this_turn("PLAN", [], line) is True


WORK = "[skill-router] Work: light (jev 0.91) → sub-agents dispatch on haiku"
QUOTA = "[skill-router] Quota: 5h 87% → this is light work; offload it: bash k.sh \"<the task>\""


def test_held_out_turn_keeps_quota_line(monkeypatch, capsys):
    monkeypatch.setenv("SKILL_ROUTER_HOLDOUT", "1")
    monkeypatch.setattr(router, "LAST_CARD", router.RouteCard(tier="soft"))
    short = f"{router.SHORTLIST_MARKER}: alpha · beta"
    pending: list = []
    out = _run_main(monkeypatch, capsys, ("plan", [_step()], [], "\n".join([LINE, WORK, QUOTA, short])), pending)
    assert out.splitlines() == [WORK, QUOTA]
    assert pending == []
    assert [e for e in _events() if e.get("held_out")]


def test_held_out_names_are_not_recently_shown(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(router, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setenv("SKILL_ROUTER_HOLDOUT", "1")
    monkeypatch.setattr(router, "LAST_CARD", router.RouteCard(tier="soft"))
    line = f"{router.SHORTLIST_MARKER}: alpha · beta"
    assert _run_main(monkeypatch, capsys, ("plan", [], [], line)) == ""
    assert router._shortlist_recent("s1", ["alpha", "beta"], record=False) is False
    monkeypatch.setenv("SKILL_ROUTER_HOLDOUT", "0")
    assert line in _run_main(monkeypatch, capsys, ("plan", [], [], line))
    assert router._shortlist_recent("s1", ["alpha", "beta"], record=False) is True


class _FakeJev:
    ranked = ("alpha", "beta", "gamma")
    path, path_confidence = "OPERATE", 0.9


def test_suppressed_repeat_still_ages_the_quiet_window_jev(monkeypatch, tmp_path):
    """Same names on consecutive turns: shown, muted for SHORTLIST_QUIET+1 turns, shown again."""
    monkeypatch.setattr(router, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setattr(router, "SESSION_ID", "s1")
    monkeypatch.setattr(router, "_entry_types", lambda: {})
    monkeypatch.setattr(router, "valid_skill", lambda n: True)
    monkeypatch.setattr(router, "is_deferred", lambda n: False)
    monkeypatch.setattr(router, "LOADED", frozenset())
    printed = []
    for turn in range(1, 9):
        line = router.jev_shortlist_line("fix my broken build please", "OPERATE", _FakeJev())
        if line:
            router._record_printed_shortlists(line)
            printed.append(turn)
    assert printed == [1, 7]


def test_suppressed_repeat_still_ages_the_quiet_window_keyless(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(router, "SESSION_DIR", tmp_path / "sessions")
    monkeypatch.setenv("SKILL_ROUTER_HOLDOUT", "0")
    line = f"{router.SHORTLIST_MARKER}: alpha · beta"
    monkeypatch.setattr(router, "shortlist_line", lambda *a, **k: line)
    monkeypatch.setattr(router, "suggest_online_skill", lambda *a, **k: None)
    printed = []
    for turn in range(1, 9):
        if line in _run_main(monkeypatch, capsys, ("SKIP", [], [], "")):
            printed.append(turn)
    assert printed == [1, 7]
