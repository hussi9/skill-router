"""Live dashboard: aggregation from synthetic logs, privacy, escaping, localhost-only server."""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import live_dashboard as ld  # type: ignore[import-not-found]  # noqa: E402

SECRET_WORD = "zebrafjordquux"   # a prompt keyword that must never surface


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _paths(tmp: Path) -> "ld.Paths":
    return ld.Paths(
        router_log=tmp / "router.jsonl",
        jev_log=tmp / "cache" / "jev-decisions.jsonl",
        outcomes=tmp / "outcomes.jsonl",
        index=tmp / "index.json",
        listing_dir=tmp / "cache" / "listing",
        visibility=tmp / "cache" / "visibility-summary.json",
    )


def _fixture(tmp: Path, now: float) -> "ld.Paths":
    p = _paths(tmp)
    p.index.write_text(json.dumps({"entries": [
        {"name": "alpha-skill", "type": "skill", "description": "a"},
        {"name": "beta-skill", "type": "skill", "description": "b"},
        {"name": "gamma-skill", "type": "skill", "description": "c"},
    ]}))
    _jsonl(p.router_log, [
        {"ts": _iso(now - 60), "type": "prompt", "path": "BUILD", "route": "alpha-skill",
         "tokens": [SECRET_WORD, "other"], "session_id": "s", "prompt_id": "p1"},
        {"ts": _iso(now - 50), "type": "invoke", "skill": "alpha-skill", "session_id": "s"},
        {"ts": _iso(now - 40), "type": "prompt", "path": "SKIP", "route": None, "tokens": [SECRET_WORD]},
    ])
    confs = [0.05, 0.15, 0.85, 0.95, 0.91]
    _jsonl(p.jev_log, [
        {"timestamp": now - 100 + i, "operation": "choose", "cache_hit": i == 0,
         "provider_attempted": i != 0, "elapsed_ms": 100 * (i + 1), "confidence": c,
         "failure": "", "session_id": "s", "prompt_id": f"p{i + 1}",
         "input": {"request": {"sha256": "x", "chars": 10}}}
        for i, c in enumerate(confs)
    ] + [{"timestamp": now - 30 * 86400, "operation": "choose", "confidence": 0.5,
          "elapsed_ms": 9, "cache_hit": False, "provider_attempted": True}])  # outside window
    _jsonl(p.outcomes, [
        {"ts": now - 60, "type": "turn", "session_id": "s", "prompt_id": "p1"},
        {"ts": now - 60, "type": "shown", "session_id": "s", "prompt_id": "p1",
         "card": ["alpha-skill"], "shortlist": [], "source": "jev", "chars": 300},
        {"ts": now - 55, "type": "turn", "session_id": "s", "prompt_id": "p2"},
        {"ts": now - 55, "type": "shown", "session_id": "s", "prompt_id": "p2",
         "card": [], "shortlist": ["beta-skill", "gamma-skill"], "source": "jev", "chars": 200},
        {"ts": now - 50, "type": "signal", "session_id": "s", "prompt_id": "p1",
         "kind": "loaded", "skill": "alpha-skill"},
        {"ts": now - 45, "type": "signal", "session_id": "s", "prompt_id": "p1", "kind": "tests_green"},
        {"ts": now - 44, "type": "turn", "session_id": "s", "prompt_id": "p3"},
        {"ts": now - 44, "type": "shown", "session_id": "s", "prompt_id": "p3",
         "card": [], "shortlist": [], "source": "jev", "held_out": True},
        {"ts": now - 43, "type": "signal", "session_id": "s", "prompt_id": "p3", "kind": "tests_red"},
        {"ts": now - 42, "type": "turn", "session_id": "s", "prompt_id": "p4"},
    ])
    return p


def test_aggregate_counts_and_histogram(tmp_path: Path) -> None:
    now = time.time()
    s = ld.aggregate(_fixture(tmp_path, now), days=7, now=now)
    j = s["jev"]
    assert j["decisions"] == 5
    assert j["cache_hit_rate"] == pytest.approx(0.2)
    assert sum(j["histogram"]) == 5 and len(j["histogram"]) == 10
    assert j["histogram"][0] == 1 and j["histogram"][1] == 1 and j["histogram"][9] == 2
    assert j["route_rate"] == pytest.approx(0.6)
    assert j["median_ms"] == pytest.approx(350)       # attempted calls only: 200..500
    r = s["routing"]
    assert (r["cards"], r["shortlists"], r["silent"], r["held_out"]) == (1, 1, 1, 1)
    assert sum(d["cards"] for d in r["per_day"]) == 1
    kinds = [e["kind"] for e in s["events"]]
    assert {"route", "shortlist", "held_out", "loaded", "tests_green", "tests_red"} <= set(kinds)
    route = next(e for e in s["events"] if e["kind"] == "route")
    assert route["skills"] == ["alpha-skill"] and route["jev_conf"] == pytest.approx(0.05)
    assert route["mode"] == "jev"
    assert s["events"][0]["ts"] >= s["events"][-1]["ts"]   # newest first
    assert s["header"]["installed"] == 3
    assert s["top_skills"][0]["name"] == "alpha-skill"
    assert s["outcomes"]["summary"]["shown"] == 2
    assert s["outcomes"]["lift"]["routed"]["known"] == 1
    assert "alpha-skill" in s["recent_skills"]


def test_empty_sources_say_no_data(tmp_path: Path) -> None:
    s = ld.aggregate(_paths(tmp_path), days=7, now=time.time())
    assert s["jev"] is None
    assert s["routing"] is None
    assert s["outcomes"] is None
    assert s["listing"] is None and s["visibility"] is None
    assert s["events"] == [] and s["top_skills"] == []
    assert s["header"]["installed"] == 0
    assert "no data yet" in ld.page(s)


def test_no_prompt_text_in_state(tmp_path: Path) -> None:
    now = time.time()
    s = ld.aggregate(_fixture(tmp_path, now), days=7, now=now)
    blob = json.dumps(s)
    assert SECRET_WORD not in blob
    assert "sha256" not in blob and "tokens" not in blob


def test_binds_localhost_only() -> None:
    with pytest.raises(ValueError):
        ld.make_server(0, 7, host="0.0.0.0")
    srv = ld.make_server(0, 7)
    try:
        assert srv.server_address[0] == "127.0.0.1"
    finally:
        srv.server_close()


def test_html_escapes_skill_names(tmp_path: Path) -> None:
    now = time.time()
    p = _fixture(tmp_path, now)
    bad = "</script><img src=x onerror=alert(1)>"
    p.index.write_text(json.dumps({"entries": [{"name": bad, "type": "skill"}]}))
    _jsonl(p.outcomes, [{"ts": now - 5, "type": "shown", "session_id": "s", "prompt_id": "q",
                         "card": [bad], "shortlist": [], "source": "jev"}])
    html = ld.page(ld.aggregate(p, days=7, now=now))
    assert bad not in html and "<img" not in html
    assert "\\u003c/script\\u003e" in html


def test_state_endpoint_json(tmp_path: Path) -> None:
    now = time.time()
    p = _fixture(tmp_path, now)
    srv = ld.make_server(0, 7, paths=p, audit=False)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        port = srv.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=5) as r:
            assert r.headers["Content-Type"].startswith("application/json")
            data = json.loads(r.read())
        assert data["jev"]["decisions"] == 5
        assert SECRET_WORD not in json.dumps(data)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
            assert b"SKILL-ROUTER" in r.read()
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/state",
                                     headers={"Host": "evil.example"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=5)
        assert e.value.code == 403
    finally:
        srv.shutdown()
        srv.server_close()
