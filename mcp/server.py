#!/usr/bin/env python3
"""skill-router as a stdio MCP server (stdlib only).

Newline-delimited JSON-RPC 2.0 on stdin/stdout. Read-only tools: route, find_skill,
audit_skills, listing. Nothing here writes a file, applies a plan, installs a skill,
runs the /context probe or opens a network connection other than Jev's own call
(only when a TYPESAFE_API_KEY is available). Prompt text is never logged.

Run: python3 mcp/server.py
"""
from __future__ import annotations

import atexit
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

DEFAULT_PROTOCOL = "2025-06-18"
MAX_TEXT = 4000
MAX_INPUT = 16 * 1024

# Never hook mode: no pending state, outcome or feature logging, no holdout.
os.environ.pop("SKILL_ROUTER_HOOK_MODE", None)


def _version() -> str:
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip() or "0"
    except OSError:
        return "0"


_READY = False


def _router():
    """Import the router once: no LLM tie-break, Jev only with a key, no cache writes.

    The Jev key is resolved first (it may live in the real cache dir's env.json), then
    Jev's cache and decision log are redirected to a per-process temp dir removed at exit.
    """
    global _READY
    if not _READY:
        os.environ["SKILL_ROUTER_LLM"] = "0"          # no Anthropic/Gemini tie-break, no llm cache
        import jev_choose  # type: ignore[import-not-found]
        key = jev_choose._key()
        tmp = Path(tempfile.mkdtemp(prefix="skill-router-mcp-"))
        atexit.register(shutil.rmtree, tmp, True)
        os.environ["SKILL_ROUTER_CACHE_DIR"] = str(tmp)
        jev_choose.CACHE_ROOT, jev_choose.ENV_FILE, jev_choose.CACHE = tmp, tmp / "env.json", tmp / "jev"
        if key:
            os.environ["TYPESAFE_API_KEY"] = key
            os.environ["SKILL_ROUTER_JEV"] = "1"
        else:
            os.environ.pop("SKILL_ROUTER_JEV", None)
        _READY = True
    import router  # type: ignore[import-not-found]
    # SKILL_ROUTER_LLM=0 also makes the router's own gate report Jev off; Jev is decided by the key alone.
    router._jev_active = lambda: bool(os.environ.get("TYPESAFE_API_KEY"))
    return router


def _task(args: dict, key: str) -> str:
    v = args.get(key)
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"'{key}' must be a non-empty string")
    if len(v) > MAX_INPUT:
        raise ValueError(f"'{key}' is over {MAX_INPUT // 1024} KB")
    return v


def tool_route(args: dict) -> dict:
    task = _task(args, "task")
    router = _router()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        path, chain, _domains, announcement = router.route(task)
    announcement = announcement or ""
    card, shortlist = router._shown_from_output(chain, announcement)
    lines = [ln for ln in (announcement + "\n" + buf.getvalue()).splitlines()
             if ln.startswith("[skill-router]")]
    return {"card": card, "shortlist": shortlist, "path": path,
            "mode": "jev" if router.JEV_ANSWERED else "keyless",
            "text": "\n".join(lines)[:MAX_TEXT]}


def _sh_matches(capability: str, limit: int) -> list[dict]:
    import skills_sh_catalog as sh  # type: ignore[import-not-found]
    from index_match import tokenize  # type: ignore[import-not-found]
    path = Path(os.environ.get("SKILL_ROUTER_SKILLS_SH_FILE")
                or Path.home() / ".claude" / "skill_router_skills_sh.json")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        entries = (doc.get("catalogs") or {}).get("skills.sh") or []
    except (OSError, ValueError, AttributeError):
        return []
    want = set(tokenize(capability))
    scored = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        source, name = e.get("source"), e.get("name")
        if not sh.safe_ids(source, name):
            continue
        desc = sh.clean_text(e.get("description"))
        hits = len(want & set(tokenize(f"{name} {desc}")))
        if hits:
            installs = e.get("installs") if isinstance(e.get("installs"), (int, float)) else 0
            scored.append((hits, installs, {
                "name": name, "description": desc, "installs": installs,
                "audit": sh.clean_text(e.get("audit"), 60),
                "install_command": sh.install_command(source, name)}))
    scored.sort(key=lambda t: (-t[0], -t[1]))
    return [s[2] for s in scored[:limit]]


def tool_find_skill(args: dict) -> dict:
    cap = _task(args, "capability")
    limit = args.get("limit", 5)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 20:
        raise ValueError("'limit' must be an integer from 1 to 20")
    import index_match  # type: ignore[import-not-found]
    installed = [{"name": m.name, "score": round(m.score, 2), "description": m.description[:200]}
                 for m in index_match.rank(cap, limit)]
    return {"installed": installed, "skills_sh": _sh_matches(cap, limit)}


def tool_audit_skills(args: dict) -> dict:
    import skill_audit  # type: ignore[import-not-found]
    catalog, usage, days, outs, sh = skill_audit.load_inputs()
    rep = skill_audit.audit(catalog, usage, days, outs, sh, jev=None)
    return {"score": rep.score, "skills": rep.skills, "listing_tokens": rep.listing_tokens,
            "unused_90d": len(rep.unused_90d), "shown_never_loaded": len(rep.shown_never_loaded),
            "overlaps": len(rep.overlaps), "flagged": len(rep.flagged),
            "top_actions": list(rep.top_actions)[:3]}


def tool_listing(args: dict) -> dict:
    import listing_probe  # type: ignore[import-not-found]
    import project_usage  # type: ignore[import-not-found]
    project = args.get("project")
    if project is not None and not isinstance(project, str):
        raise ValueError("'project' must be a string path")
    key = project_usage.project_key(Path(project) if project else Path.cwd())
    m = listing_probe.load(key)
    return {"project_key": key, "measured": m is not None,
            "text": listing_probe.line(m) if m else "not measured"}


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": False}


TOOLS: dict[str, tuple[str, dict, Callable[[dict], dict]]] = {
    "route": ("Route a task: returns the skill card and shortlist the router would show.",
              _schema({"task": {"type": "string", "description": "the task to route"}}, ["task"]),
              tool_route),
    "find_skill": ("Find installed skills and cached skills.sh skills for a capability (read-only, no network).",
                   _schema({"capability": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5}},
                           ["capability"]), tool_find_skill),
    "audit_skills": ("Summarise the skill audit: score, counts and top actions.",
                     _schema({}, []), tool_audit_skills),
    "listing": ("Cached /context measurement line for a project, or 'not measured'.",
                _schema({"project": {"type": ["string", "null"]}}, []), tool_listing),
}


def _ok(rid: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def _err(rid: Any, code: int, msg: str) -> dict:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}}


def _tool_result(payload: Any, is_error: bool = False) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def handle(msg: Any) -> Optional[dict]:
    if not isinstance(msg, dict) or not isinstance(msg.get("method"), str):
        return _err(msg.get("id") if isinstance(msg, dict) else None, -32600, "invalid request")
    method, rid = msg["method"], msg.get("id")
    params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
    if "id" not in msg:                       # notification: never answered
        return None
    if method == "initialize":
        proto = params.get("protocolVersion")
        return _ok(rid, {"protocolVersion": proto if isinstance(proto, str) and proto else DEFAULT_PROTOCOL,
                         "capabilities": {"tools": {}},
                         "serverInfo": {"name": "skill-router", "version": _version()}})
    if method == "ping":
        return _ok(rid, {})
    if method == "tools/list":
        return _ok(rid, {"tools": [{"name": n, "description": d, "inputSchema": s}
                                   for n, (d, s, _f) in TOOLS.items()]})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") if params.get("arguments") is not None else {}
        if not isinstance(name, str) or not isinstance(args, dict):
            return _err(rid, -32602, "invalid params: name (string) and arguments (object) required")
        if name not in TOOLS:
            return _ok(rid, _tool_result(f"unknown tool: {name[:60]}", True))
        try:
            return _ok(rid, _tool_result(TOOLS[name][2](args)))
        except Exception as e:                # tool failure is a result, not a protocol error
            return _ok(rid, _tool_result(f"{name} failed: {type(e).__name__}: {str(e)[:200]}".replace("\n", " "), True))
    return _err(rid, -32601, f"method not found: {method[:60]}")


def serve(stdin=None, stdout=None) -> None:
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply: Optional[dict] = _err(None, -32700, "parse error")
        else:
            reply = handle(msg)
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve()
