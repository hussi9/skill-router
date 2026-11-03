"""Per-project skill usage mined from synthetic transcript trees."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import project_usage as pu  # noqa: E402


def _ts(days_ago: float = 1) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")


def _skill(name: str, days_ago: float = 1, **extra) -> dict:
    return {"type": "assistant", "timestamp": _ts(days_ago), **extra,
            "message": {"content": [{"type": "tool_use", "name": "Skill", "input": {"skill": name}}]}}


def _tree(tmp_path: Path, proj: Path, events: list, fname: str = "s1.jsonl") -> Path:
    tdir = tmp_path / "projects"
    folder = tdir / re.sub(r"[^A-Za-z0-9]", "-", str(proj))
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / fname).open("w") as f:
        for e in events:
            f.write((e if isinstance(e, str) else json.dumps(e)) + "\n")
    return tdir


def test_counts_skill_tool_uses_per_project(tmp_path):
    a = tmp_path / "work" / "app.one"
    a.mkdir(parents=True)
    tdir = _tree(tmp_path, a, [_skill("alpha"), _skill("alpha", 3), _skill("beta", 2, isSidechain=True),
                              "not json {{ \"Skill\""])
    u = pu.usage_by_project(tdir)
    entry = u[pu.project_key(a)]
    assert entry["dir"] == str(a.resolve())
    assert entry["skills"] == {"alpha": 2, "beta": 1}
    assert entry["joinable"] is True and entry["ambiguous"] is False
    assert pu._parse_ts(entry["last_used"]["alpha"]) > pu._parse_ts(_ts(2))


def test_ignores_message_text(tmp_path):
    a = tmp_path / "proj"
    a.mkdir()
    chatter = {"type": "user", "timestamp": _ts(), "message": {"content": 'use "Skill" gamma please'}}
    text_only = {"type": "assistant", "timestamp": _ts(),
                 "message": {"content": [{"type": "text", "text": '{"name":"Skill","input":{"skill":"gamma"}}'}]}}
    other_tool = {"type": "assistant", "timestamp": _ts(),
                  "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {"skill": "gamma"}}]}}
    u = pu.usage_by_project(_tree(tmp_path, a, [chatter, text_only, other_tool]))
    assert u == {}


def test_missing_project_dir_is_skipped(tmp_path):
    tdir = _tree(tmp_path, tmp_path / "gone" / "away", [_skill("alpha")])
    u = pu.usage_by_project(tdir)
    assert len(u) == 1
    (entry,) = u.values()
    assert entry["dir"] is None and entry["skills"] == {"alpha": 1}
    assert entry["joinable"] is False and entry["ambiguous"] is False


def test_old_events_outside_window_are_dropped(tmp_path):
    a = tmp_path / "proj"
    a.mkdir()
    u = pu.usage_by_project(_tree(tmp_path, a, [_skill("old", 200), _skill("new", 1)]), since_days=90)
    assert u[pu.project_key(a)]["skills"] == {"new": 1}


def test_project_key_is_12_hex_and_stable(tmp_path):
    k = pu.project_key(tmp_path)
    assert re.fullmatch(r"[0-9a-f]{12}", k) and k == pu.project_key(tmp_path / ".")


def test_router_events_carry_project_key(tmp_path, monkeypatch):
    import outcomes
    import router
    proj = tmp_path / "myproj"
    proj.mkdir()
    monkeypatch.setattr(router, "LOG", tmp_path / "router.jsonl")
    monkeypatch.delenv("SKILL_ROUTER_NO_LEARN", raising=False)
    monkeypatch.setenv("SKILL_ROUTER_OUTCOMES_FILE", str(tmp_path / "outcomes.jsonl"))
    key = pu.project_key(proj)
    monkeypatch.setattr(router, "PROJECT", key)      # main() sets it from the hook payload
    router.log_prompt_event("fix the failing checkout test please", {"session_id": "s", "prompt_id": "p",
                                                                    "cwd": str(proj)}, "BROKEN", [])
    rows = [json.loads(l) for l in (tmp_path / "router.jsonl").read_text().splitlines()]
    assert rows and rows[0]["project"] == key and str(proj) not in json.dumps(rows)
    outcomes.record_shown("s", "p", ["alpha"], [], "keyless", project=key)
    outcomes.record_signal("s", "p", "loaded", "alpha", project=key)
    ev = [json.loads(l) for l in (tmp_path / "outcomes.jsonl").read_text().splitlines()]
    assert [e["project"] for e in ev] == [key, key]
    assert router._project_of({"cwd": str(proj)}) == key
    assert router._project_of({}) is None


def test_colliding_encodings_are_ambiguous(tmp_path):
    base = tmp_path / "amb"
    (base / "a-b").mkdir(parents=True)
    (base / "a" / "b").mkdir(parents=True)
    tdir = tmp_path / "projects"
    folder = tdir / re.sub(r"[^A-Za-z0-9]", "-", str(base / "a-b"))
    folder.mkdir(parents=True)
    (folder / "s.jsonl").write_text(json.dumps(_skill("alpha")) + "\n")
    (entry,) = pu.usage_by_project(tdir).values()
    assert entry["dir"] is None and entry["joinable"] is False and entry["ambiguous"] is True
    assert entry["skills"] == {"alpha": 1}


def test_last_used_compares_by_epoch(tmp_path):
    a = tmp_path / "proj"
    a.mkdir()
    newer = {**_skill("x"), "timestamp": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)).isoformat()}
    older = {**_skill("x"), "timestamp": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=5)).isoformat().replace("+00:00", "Z")}
    u = pu.usage_by_project(_tree(tmp_path, a, [newer, older]))
    assert u[pu.project_key(a)]["last_used"]["x"] == newer["timestamp"]


def test_jev_features_row_carries_project(tmp_path, monkeypatch):
    import router
    from types import SimpleNamespace as NS
    feats = tmp_path / "f.jsonl"
    monkeypatch.setenv("SKILL_ROUTER_FEATURES_FILE", str(feats))
    monkeypatch.delenv("SKILL_ROUTER_NO_LEARN", raising=False)
    monkeypatch.setattr(router, "JEV_ANSWERED", True, raising=False)
    monkeypatch.setattr(router, "JEV_CHOICE", NS(ranked=["alpha"], domain=NS(name="d", confidence=0.5), path="P"))
    monkeypatch.setattr(router, "SESSION_ID", "s")
    monkeypatch.setattr(router, "PROMPT_ID", "p")
    monkeypatch.setattr(router, "PROJECT", "0123456789ab")
    monkeypatch.setattr(router, "_entry_types", lambda: {})
    import index_match
    monkeypatch.setattr(index_match, "rank", lambda *a, **k: [])
    monkeypatch.setattr(router, "_semantic_order", lambda *a, **k: [])
    router._record_jev_features("hello there")
    row = json.loads(feats.read_text().splitlines()[0])
    assert row["project"] == "0123456789ab"


def test_subagent_transcripts_count(tmp_path):
    a = tmp_path / "work" / "app.sub"
    a.mkdir(parents=True)
    tdir = _tree(tmp_path, a, [_skill("alpha")])
    folder = next((tdir).iterdir())
    sub = folder / "sess1" / "subagents"
    sub.mkdir(parents=True)
    (sub / "agent-x.jsonl").write_text(json.dumps(_skill("alpha")) + "\n" + json.dumps(_skill("gamma")) + "\n")
    old = sub / "agent-old.jsonl"
    old.write_text(json.dumps(_skill("delta")) + "\n")
    os.utime(old, (1, 1))
    got = pu.usage_by_project(tdir)[pu.project_key(a)]["skills"]
    assert got == {"alpha": 2, "gamma": 1}
