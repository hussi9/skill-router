"""Distillation dataset: Jev's decisions as training rows, with no prompt text.

Run: python3 -m pytest tests/test_jev_dataset.py -q
"""
from __future__ import annotations

import io
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import jev_dataset  # type: ignore[import-not-found]  # noqa: E402
import jev_choose  # type: ignore[import-not-found]  # noqa: E402
import outcomes  # type: ignore[import-not-found]  # noqa: E402
import router  # type: ignore[import-not-found]  # noqa: E402

SECRET = "please refactor the zebra-crossing billing module"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("SKILL_ROUTER_OUTCOMES_FILE", str(tmp_path / "o.jsonl"))
    monkeypatch.setenv("SKILL_ROUTER_FEATURES_FILE", str(tmp_path / "f.jsonl"))
    monkeypatch.delenv("SKILL_ROUTER_NO_LEARN", raising=False)
    monkeypatch.setenv("SKILL_ROUTER_NO_EMBED", "1")


def _feature(path: Path, sid="s1", pid="p1", ranked=("a", "b"), ts=None, **extra):
    row = {"ts": ts or time.time(), "session_id": sid, "prompt_id": pid, "prompt_sha256": "ab" * 32,
           "lex_top10": ["a", "c"], "sem_top10": ["b"], "jev_ranked": list(ranked),
           "jev_domain": "a", "jev_conf": 0.9, "jev_path": "BUILD", **extra}
    with path.open("a") as f:
        f.write(json.dumps(row) + "\n")


def test_rows_have_no_text_fields(tmp_path):
    _feature(tmp_path / "f.jsonl")
    out = tmp_path / "out"
    assert jev_dataset.export(out)["rows"] == 1
    row = json.loads((out / "train.jsonl").read_text())
    assert set(row) == {"prompt_sha256", "features", "label_ranked", "label_loaded"}
    assert set(row["features"]) == {"lex_top10", "sem_top10"}
    assert stat.S_IMODE(os.stat(out / "train.jsonl").st_mode) == 0o600


def test_label_prefers_loaded_skill_over_jev_rank(tmp_path):
    _feature(tmp_path / "f.jsonl", ranked=("a", "b"))
    outcomes.record_signal("s1", "p1", "loaded", "z")
    outcomes.record_signal("s1", "p1", "loaded", "b")
    _feature(tmp_path / "f.jsonl", pid="p2")
    counts = jev_dataset.export(tmp_path / "out")
    rows = [json.loads(l) for l in (tmp_path / "out" / "train.jsonl").read_text().splitlines()]
    assert counts == {"rows": 2, "with_loaded": 1, "skipped": 0}
    assert [r["label_loaded"] for r in rows] == ["z", None]
    assert rows[0]["label_ranked"] == ["a", "b"]


def test_old_and_malformed_rows(tmp_path):
    f = tmp_path / "f.jsonl"
    _feature(f, ts=time.time() - 200 * 86400)
    _feature(f, pid="p2")
    f.open("a").write("not json\n")
    assert jev_dataset.export(tmp_path / "out", since_days=90) == {"rows": 1, "with_loaded": 0, "skipped": 1}


def test_refuses_non_empty_dir(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "x").write_text("1")
    with pytest.raises(ValueError):
        jev_dataset.export(out)
    assert jev_dataset.main(["--out", str(out)]) == 1


# ---- router: the features record ---------------------------------------------

def _choice():
    return jev_choose.Choice(domain=jev_choose.Pick("writing-plans", 0.91, "route"),
                             process=jev_choose.Pick(None, 0.0, "silent"), path="BUILD",
                             path_confidence=0.9, ms=5, tokens=1, ranked=("writing-plans", "plan"))


def _run(monkeypatch, capsys, tmp_path, hook: bool, answered: bool):
    monkeypatch.setenv("SKILL_ROUTER_HOOK_MODE", "1" if hook else "0")
    monkeypatch.setenv("SKILL_ROUTER_SESSION_DIR", str(tmp_path))
    monkeypatch.delenv("CLAUDE_USER_INPUT", raising=False)
    import index_match  # type: ignore[import-not-found]
    monkeypatch.setattr(index_match, "INDEX_FILE", tmp_path / "no-skill_index.json")
    index_match.load_index.cache_clear()
    monkeypatch.setattr(router, "log_prompt_event", lambda *a, **k: None)
    monkeypatch.setattr(router, "previous_assistant_tail", lambda *_: "")
    monkeypatch.setattr(router, "loaded_this_session", lambda *_: frozenset())

    monkeypatch.setattr(router, "JEV_ANSWERED", False)   # registers restore of the globals
    monkeypatch.setattr(router, "JEV_CHOICE", None)

    def fake_route(prompt):
        router.JEV_ANSWERED = answered
        router.JEV_CHOICE = _choice() if answered else None
        return ("SKIP", [], [], "")

    monkeypatch.setattr(router, "route", fake_route)
    monkeypatch.setattr(router, "shortlist_line", lambda *a, **k: "")
    monkeypatch.setattr(router, "suggest_online_skill", lambda *a, **k: None)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"prompt": SECRET, "session_id": "s1", "prompt_id": "p1"})))
    assert router.main() == 0
    capsys.readouterr()


def test_features_written_only_in_hook_mode_with_jev_answer(monkeypatch, capsys, tmp_path):
    feat = tmp_path / "f.jsonl"
    _run(monkeypatch, capsys, tmp_path, hook=False, answered=True)
    _run(monkeypatch, capsys, tmp_path, hook=True, answered=False)
    assert not feat.exists()
    _run(monkeypatch, capsys, tmp_path, hook=True, answered=True)
    raw = feat.read_text()
    row = json.loads(raw)
    assert "zebra" not in raw and SECRET not in raw
    assert set(row) == {"ts", "session_id", "prompt_id", "prompt_sha256", "lex_top10", "sem_top10",
                        "jev_ranked", "jev_domain", "jev_conf", "jev_path"}
    assert row["jev_ranked"] == ["writing-plans", "plan"] and row["jev_domain"] == "writing-plans"
    assert len(row["prompt_sha256"]) == 64 and stat.S_IMODE(os.stat(feat).st_mode) == 0o600


def test_semantic_order_called_once_per_prompt(monkeypatch):
    calls = []
    import types
    monkeypatch.setitem(sys.modules, "embedder_client",
                        types.SimpleNamespace(rank_skills=lambda p: calls.append(p) or ["x"]))
    monkeypatch.delenv("SKILL_ROUTER_NO_EMBED")
    monkeypatch.setattr(router, "_SEM_CACHE", (None, None))
    assert router._semantic_order("q") == ["x"] and router._semantic_order("q") == ["x"]
    assert calls == ["q"]


def test_no_row_without_prompt_or_session_id(monkeypatch, capsys, tmp_path):
    feat = tmp_path / "f.jsonl"
    for sid, pid in (("s1", ""), ("", "p1")):
        monkeypatch.setenv("SKILL_ROUTER_HOOK_MODE", "1")
        monkeypatch.setattr(router, "JEV_ANSWERED", True)
        monkeypatch.setattr(router, "JEV_CHOICE", _choice())
        monkeypatch.setattr(router, "SESSION_ID", sid)
        monkeypatch.setattr(router, "PROMPT_ID", pid)
        router._record_jev_features(SECRET)
    assert not feat.exists()


def test_exporter_skips_empty_ids_and_bad_ts(tmp_path):
    f = tmp_path / "f.jsonl"
    _feature(f, pid="")
    _feature(f, sid="")
    _feature(f, pid="p3", ts="yesterday")
    _feature(f, pid="p4")
    assert jev_dataset.export(tmp_path / "out") == {"rows": 1, "with_loaded": 0, "skipped": 3}


def test_rank_skills_once_per_prompt_across_shortlist_and_features(monkeypatch):
    import types
    calls = []
    monkeypatch.setitem(sys.modules, "embedder_client",
                        types.SimpleNamespace(rank_skills=lambda p: calls.append(p) or [p + "-skill"]))
    monkeypatch.delenv("SKILL_ROUTER_NO_EMBED")
    monkeypatch.setattr(router, "_SEM_CACHE", (None, None))
    monkeypatch.setattr(router, "JEV_ANSWERED", True)
    monkeypatch.setattr(router, "JEV_CHOICE", _choice())
    monkeypatch.setattr(router, "SESSION_ID", "s1")
    monkeypatch.setattr(router, "PROMPT_ID", "p1")
    monkeypatch.setattr(router, "_entry_types", lambda: {})
    router.shortlist_candidates("alpha")
    router._record_jev_features("alpha")
    assert calls == ["alpha"]
    router._record_jev_features("beta")
    router.shortlist_candidates("beta")
    assert calls == ["alpha", "beta"]
    rows = [json.loads(l) for l in Path(os.environ["SKILL_ROUTER_FEATURES_FILE"]).read_text().splitlines()]
    assert [r["sem_top10"] for r in rows] == [["alpha-skill"], ["beta-skill"]]
