#!/usr/bin/env python3
"""
llm_classify.py — the small-model stage the router calls when lexical
ranking is unsure, and the enrichment call build_index.py makes once per skill.

Why a provider chain and not one SDK:
  - `claude -p` from inside a hook re-enters Claude Code with every hook and
    MCP server of this machine attached. Measured at 3.5 minutes. Unusable.
  - The Anthropic key in Doppler is valid but its billing balance is zero
    (2026-09-07). Tried first anyway; the moment it has credit it wins.
  - Gemini 2.5 Flash-Lite answered the same classification in 0.55 s and is
    the house default for cheap generation (CLAUDE.md economics rule).

Keys come from the environment first, then from
~/.claude/skill_router_cache/env.json which refresh_env.py writes from Doppler
at SessionStart. Nothing here ever calls Doppler itself: a hook that shells
out to a secrets manager on every prompt is a second of latency for nothing.

Every result is cached under ~/.claude/skill_router_cache/llm/<sha1>.json.
Every failure returns None. Nothing raises.

    classify(prompt, candidates) -> {"path": ..., "skills": [...], "reason": ...} | None
    complete_json(prompt)        -> dict | None
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Sequence

HOME = Path.home()
CACHE_ROOT = HOME / ".claude" / "skill_router_cache"
ENV_FILE = CACHE_ROOT / "env.json"
LLM_CACHE = CACHE_ROOT / "llm"
TIMEOUT_S = float(os.environ.get("SKILL_ROUTER_LLM_TIMEOUT", "6"))

ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
GEMINI_MODEL = "gemini-2.5-flash-lite"

PATHS = ("BROKEN", "BUILD", "OPERATE", "QUESTION")


def enabled() -> bool:
    v = os.environ.get("SKILL_ROUTER_LLM", "1")
    return v not in ("0", "off", "false")


def _keys() -> dict[str, str]:
    out: dict[str, str] = {}
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        if os.environ.get(k):
            out[k] = os.environ[k]
    if len(out) < 2 and ENV_FILE.is_file():
        try:
            data = json.loads(ENV_FILE.read_text(encoding="utf-8"))
            for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
                if k not in out and data.get(k):
                    out[k] = str(data[k])
        except (OSError, json.JSONDecodeError):
            pass
    return out


def _post(url: str, headers: dict, body: dict) -> Optional[dict]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json", **headers},
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        return None


def _anthropic(prompt: str, key: str, max_tokens: int) -> Optional[str]:
    data = _post("https://api.anthropic.com/v1/messages",
                 {"x-api-key": key, "anthropic-version": "2023-06-01"},
                 {"model": ANTHROPIC_MODEL, "max_tokens": max_tokens,
                  "messages": [{"role": "user", "content": prompt}]})
    if not data or data.get("type") == "error":
        return None
    parts = data.get("content") or []
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict)) or None


def _gemini(prompt: str, key: str, max_tokens: int) -> Optional[str]:
    data = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={key}",
                 {},
                 {"contents": [{"parts": [{"text": prompt}]}],
                  "generationConfig": {"responseMimeType": "application/json",
                                       "temperature": 0, "maxOutputTokens": max_tokens}})
    if not data:
        return None
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        return None


_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _parse(text: Optional[str]) -> Optional[dict]:
    if not text:
        return None
    m = _JSON_RE.search(text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def complete_json(prompt: str, max_tokens: int = 300) -> Optional[dict]:
    """Run `prompt` through the first provider that answers. Cached by prompt hash."""
    if not enabled():
        return None
    h = hashlib.sha1(prompt.encode()).hexdigest()
    cpath = LLM_CACHE / f"{h}.json"
    if cpath.is_file():
        try:
            return json.loads(cpath.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    keys = _keys()
    result: Optional[dict] = None
    provider = None
    if keys.get("ANTHROPIC_API_KEY") and os.environ.get("SKILL_ROUTER_LLM_SKIP_ANTHROPIC") != "1":
        result = _parse(_anthropic(prompt, keys["ANTHROPIC_API_KEY"], max_tokens))
        provider = "anthropic" if result else None
    if result is None and keys.get("GEMINI_API_KEY"):
        result = _parse(_gemini(prompt, keys["GEMINI_API_KEY"], max_tokens))
        provider = "gemini" if result else None
    if result is None:
        return None
    result["_provider"] = provider
    result["_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    try:
        LLM_CACHE.mkdir(parents=True, exist_ok=True)
        cpath.write_text(json.dumps(result))
    except OSError:
        pass
    return result


def classify(prompt: str, candidates: Sequence[tuple[str, str]],
             projects: Sequence[str] = ()) -> Optional[dict]:
    """Pick a path and up to two skills for `prompt` from `candidates`.

    candidates: (skill_name, one-line use_when summary). The model may answer
    with an empty skills list — that is a legitimate "none of these".
    """
    if not candidates:
        return None
    lines = "\n".join(f"- {n}: {d[:140]}" for n, d in candidates[:8])
    proj = f"\nKnown project names: {', '.join(projects[:20])}" if projects else ""
    p = (
        "You route a developer's request to the right installed Claude Code skill.\n"
        "Return JSON only: {\"path\": \"BROKEN|BUILD|OPERATE|QUESTION\", "
        "\"skills\": [\"up to 2 candidate names that truly fit, best first, or empty\"], "
        "\"reason\": \"<12 words\"}\n"
        "BROKEN = something is failing/wrong. BUILD = create something new. OPERATE = improve, "
        "ship, research, configure, review. QUESTION = the user wants an answer, not work.\n"
        f"Candidates:\n{lines}{proj}\n\nRequest: {prompt[:600]}"
    )
    out = complete_json(p, max_tokens=120)
    if not out:
        return None
    path = str(out.get("path", "")).upper()
    names = {n for n, _ in candidates}
    skills = [s for s in out.get("skills", []) if isinstance(s, str) and s in names][:2]
    if path not in PATHS:
        path = ""
    return {"path": path, "skills": skills, "reason": str(out.get("reason", ""))[:80],
            "provider": out.get("_provider")}


def status() -> dict:
    keys = _keys()
    return {"enabled": enabled(), "anthropic_key": bool(keys.get("ANTHROPIC_API_KEY")),
            "gemini_key": bool(keys.get("GEMINI_API_KEY")),
            "env_file": ENV_FILE.is_file(),
            "cache_entries": len(list(LLM_CACHE.glob("*.json"))) if LLM_CACHE.is_dir() else 0}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--status":
        print(json.dumps(status(), indent=1))
    else:
        text = " ".join(sys.argv[1:]) or sys.stdin.read()
        print(json.dumps(classify(text, [("mac-doctor", "mac restarts, kernel panic, disk"),
                                         ("youtube-manager", "@economicalai shorts pipeline")]), indent=1))
