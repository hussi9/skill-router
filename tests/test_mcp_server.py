"""The stdio MCP server, driven as a subprocess over pipes (offline, no Jev key)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "mcp" / "server.py"

INDEX = {"entries": [
    {"name": "chart-maker", "description": "Plot charts and graphs from tabular data",
     "use_when": ["plot a chart", "make a graph"], "keywords": ["chart", "plot", "graph"]},
    {"name": "pdf-tools", "description": "Read and split PDF files", "keywords": ["pdf"]},
]}
SH = {"catalogs": {"skills.sh": [
    {"name": "diagram-pro", "source": "acme/skills", "description": "Draw diagrams and flowcharts",
     "installs": 900, "audit": "pass"},
    {"name": "bad name!", "source": "acme/skills", "description": "diagrams", "installs": 1},
]}}


@pytest.fixture()
def env(tmp_path):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "skill_index.json").write_text(json.dumps(INDEX))
    sh = tmp_path / "sh.json"
    sh.write_text(json.dumps(SH))
    e = {k: v for k, v in os.environ.items() if k not in ("TYPESAFE_API_KEY", "SKILL_ROUTER_HOOK_MODE")}
    e.update(HOME=str(home), SKILL_ROUTER_SKILLS_SH_FILE=str(sh),
             SKILL_ROUTER_OUTCOMES_FILE=str(tmp_path / "outcomes.jsonl"),
             SKILL_ROUTER_SESSION_DIR=str(tmp_path / "sessions"),
             SKILL_ROUTER_FEATURES_FILE=str(tmp_path / "features.jsonl"))
    e["_TMP"] = str(tmp_path)
    return e


def talk(env, lines):
    """Send raw lines, return (parsed stdout lines, raw stdout)."""
    run_env = {k: v for k, v in env.items() if k != "_TMP"}
    p = subprocess.run([sys.executable, str(SERVER)], input="\n".join(lines) + "\n", text=True,
                       capture_output=True, env=run_env, timeout=60)
    out = p.stdout.splitlines()
    return [json.loads(x) for x in out], p.stdout


def req(i, method, params=None):
    return json.dumps({"jsonrpc": "2.0", "id": i, "method": method, **({"params": params} if params is not None else {})})


def call(i, name, args=None):
    return req(i, "tools/call", {"name": name, "arguments": args or {}})


def test_initialize_and_list_tools(env):
    out, _ = talk(env, [req(1, "initialize", {"protocolVersion": "2024-11-05"}),
                        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
                        req(2, "ping"), req(3, "tools/list")])
    assert len(out) == 3                      # the notification gets no reply
    init = out[0]["result"]
    assert init["protocolVersion"] == "2024-11-05"
    assert init["capabilities"] == {"tools": {}}
    assert init["serverInfo"] == {"name": "skill-router", "version": (ROOT / "VERSION").read_text().strip()}
    assert out[1]["result"] == {}
    tools = {t["name"]: t for t in out[2]["result"]["tools"]}
    assert set(tools) == {"route", "find_skill", "audit_skills", "listing"}
    assert tools["route"]["inputSchema"]["required"] == ["task"]
    out, _ = talk(env, [req(1, "initialize", {})])
    assert out[0]["result"]["protocolVersion"] == "2025-06-18"


def test_route_returns_card_without_side_effects(env):
    out, _ = talk(env, [call(1, "route", {"task": "plot a chart of sales data"})])
    res = out[0]["result"]
    assert res["isError"] is False
    body = json.loads(res["content"][0]["text"])
    assert set(body) == {"card", "shortlist", "path", "mode", "text"}
    assert body["mode"] == "keyless"
    assert isinstance(body["card"], list) and isinstance(body["shortlist"], list)
    tmp = Path(env["_TMP"])
    assert not (tmp / "outcomes.jsonl").exists()
    assert not (tmp / "sessions").exists()
    assert not (tmp / "features.jsonl").exists()
    assert not (Path(env["HOME"]) / ".claude" / "skill_router_pending.json").exists()
    bad, _ = talk(env, [call(2, "route", {"task": ""})])
    assert bad[0]["result"]["isError"] is True


def test_find_skill_reads_local_caches_only(env):
    out, _ = talk(env, [call(1, "find_skill", {"capability": "draw diagrams and plot charts", "limit": 3})])
    body = json.loads(out[0]["result"]["content"][0]["text"])
    assert [m["name"] for m in body["installed"]][:1] == ["chart-maker"]
    assert [m["name"] for m in body["skills_sh"]] == ["diagram-pro"]   # unsafe id dropped
    s = body["skills_sh"][0]
    assert s["installs"] == 900 and s["audit"] == "pass"
    assert s["install_command"] == "npx skills add acme/skills --skill diagram-pro"
    # no cache and no index: empty lists, not an error
    env2 = dict(env, SKILL_ROUTER_SKILLS_SH_FILE=str(Path(env["_TMP"]) / "missing.json"))
    out, _ = talk(env2, [call(2, "find_skill", {"capability": "diagram"})])
    assert json.loads(out[0]["result"]["content"][0]["text"])["skills_sh"] == []
    out, _ = talk(env, [call(3, "find_skill", {"capability": "x", "limit": 99})])
    assert out[0]["result"]["isError"] is True


def test_audit_and_listing_tools(env):
    out, _ = talk(env, [call(1, "audit_skills"), call(2, "listing", {"project": env["_TMP"]})])
    audit = json.loads(out[0]["result"]["content"][0]["text"])
    assert 0 <= audit["score"] <= 100 and "top_actions" in audit
    listing = json.loads(out[1]["result"]["content"][0]["text"])
    assert listing["text"] == "not measured" and listing["measured"] is False


def test_malformed_line_keeps_server_alive(env):
    out, _ = talk(env, ["{not json", req(1, "ping"), "[1,2]", req(2, "nope/method"),
                        req(3, "tools/call", {"name": 5})])
    assert out[0]["error"]["code"] == -32700 and out[0]["id"] is None
    assert out[1]["result"] == {}
    assert out[2]["error"]["code"] == -32600
    assert out[3]["error"]["code"] == -32601
    assert out[4]["error"]["code"] == -32602


def test_unknown_tool_is_error_result(env):
    out, _ = talk(env, [call(1, "does_not_exist")])
    assert "error" not in out[0]
    assert out[0]["result"]["isError"] is True
    assert "unknown tool" in out[0]["result"]["content"][0]["text"]


def test_stdout_is_protocol_only(env):
    out, raw = talk(env, [call(1, "route", {"task": "plot a chart"}), call(2, "find_skill", {"capability": "pdf"})])
    lines = raw.splitlines()
    assert len(lines) == 2 == len(out)
    assert all(m["jsonrpc"] == "2.0" for m in out)


STUB = str(ROOT / "tests" / "mcp_stub")


def _stubbed(env, **extra):
    e = dict(env, PYTHONPATH=STUB, MCP_STUB_NET_LOG=str(Path(env["_TMP"]) / "net.log"), **extra)
    return e


def _tree(root):
    return sorted(str(p.relative_to(root)) for p in Path(root).rglob("*") if p.is_file())


def test_keyless_route_makes_no_llm_call_or_cache(env):
    home = Path(env["HOME"])
    cache = home / ".claude" / "skill_router_cache"
    cache.mkdir()
    (cache / "env.json").write_text(json.dumps({"GEMINI_API_KEY": "fake", "ANTHROPIC_API_KEY": "fake"}))
    e = _stubbed(env, GEMINI_API_KEY="fake", ANTHROPIC_API_KEY="fake")
    before = _tree(home)
    out, _ = talk(e, [call(1, "route", {"task": "plot a chart and fix the graph rendering bug in my report"})])
    assert json.loads(out[0]["result"]["content"][0]["text"])["mode"] == "keyless"
    assert not (Path(env["_TMP"]) / "net.log").exists()
    assert _tree(home) == before


def test_keyed_route_writes_nothing_under_home(env):
    home = Path(env["HOME"])
    before = _tree(home)
    e = _stubbed(env, TYPESAFE_API_KEY="fake-key")
    out, _ = talk(e, [call(1, "route", {"task": "plot a chart and fix the graph rendering bug in my report"})])
    assert out[0]["result"]["isError"] is False
    assert _tree(home) == before


def test_oversized_input_is_error_result(env):
    out, _ = talk(env, [call(1, "route", {"task": "x" * (16 * 1024 + 1)}),
                        call(2, "find_skill", {"capability": "x" * (16 * 1024 + 1)})])
    assert all(o["result"]["isError"] for o in out)


def test_router_prints_never_reach_stdout(env, monkeypatch, capsys):
    sys.path.insert(0, str(ROOT / "mcp"))
    import importlib
    import io
    server = importlib.import_module("server")
    monkeypatch.setattr(os, "environ", os.environ.copy())   # _router() exports env; keep it out of later subprocesses
    monkeypatch.setattr(server, "_READY", False)
    sys.path.insert(0, str(ROOT / "scripts"))
    import router as _r
    monkeypatch.setattr(_r, "_jev_active", _r._jev_active)   # restored after _router() overrides it
    router = server._router()

    def noisy(prompt):
        print("[skill-router-warn] stray stdout line")
        print("[skill-router-warn] stray stderr line", file=sys.stderr)
        return "SKIP", [], [], "[skill-router] hello"
    monkeypatch.setattr(router, "route", noisy)
    out = io.StringIO()
    server.serve(io.StringIO(call(1, "route", {"task": "anything"}) + "\n"), out)
    lines = out.getvalue().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["result"]["isError"] is False
    assert "stray stdout line" not in out.getvalue()


def test_key_only_in_cache_env_json_gives_jev_mode_and_no_writes(env):
    home = Path(env["HOME"])
    cache = home / ".claude" / "skill_router_cache"
    cache.mkdir()
    (cache / "env.json").write_text(json.dumps({"TYPESAFE_API_KEY": "fake-key"}))
    e = _stubbed(env, MCP_STUB_JEV="1")
    before = _tree(home)
    out, _ = talk(e, [call(1, "route", {"task": "plot a chart and fix the graph rendering bug in my report"})])
    body = json.loads(out[0]["result"]["content"][0]["text"])
    assert (Path(env["_TMP"]) / "net.log").exists()        # Jev was called
    assert body["mode"] == "jev"
    assert _tree(home) == before
