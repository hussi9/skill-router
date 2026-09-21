#!/usr/bin/env python3
"""
refresh_env.py — cache the model keys the router may need, from Doppler.

Runs in the SessionStart background chain. Writes
~/.claude/skill_router_cache/env.json (mode 600) so jev_choose.py and
llm_classify.py can read a key in microseconds instead of shelling out to Doppler on every prompt.
Doppler stays the source of truth (project shared, config prd); this is a
read-through cache that is rewritten every session and never edited by hand.

Silent on every failure: no Doppler CLI, no network, no token → the file is
left as it was, and the router's LLM stage simply stays off.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
OUT = HOME / ".claude" / "skill_router_cache" / "env.json"
KEYS = ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "TYPESAFE_API_KEY")
PROJECT, CONFIG = "shared", "prd"


def doppler_bin() -> str:
    for cand in (shutil.which("doppler"), str(HOME / ".local" / "bin" / "doppler"),
                 "/opt/homebrew/bin/doppler"):
        if cand and Path(cand).is_file():
            return cand
    return ""


def fetch() -> dict[str, str]:
    d = doppler_bin()
    if not d:
        return {}
    try:
        raw = subprocess.run(
            [d, "secrets", "get", *KEYS, "--project", PROJECT, "--config", CONFIG, "--json"],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if raw.returncode != 0:
        # `secrets get A B C` fails whole when one name is absent. Asking again
        # one by one keeps the keys that do exist (SessionStart, not per prompt).
        return _fetch_each(d)
    try:
        data = json.loads(raw.stdout)
    except json.JSONDecodeError:
        return {}
    out: dict[str, str] = {}
    for k in KEYS:
        v = data.get(k)
        if isinstance(v, dict):
            v = v.get("computed") or v.get("raw")
        if isinstance(v, str) and v.strip():
            out[k] = v.strip()
    return out


def _fetch_each(d: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for k in KEYS:
        try:
            raw = subprocess.run([d, "secrets", "get", k, "--project", PROJECT, "--config", CONFIG,
                                  "--plain"], capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if raw.returncode == 0 and raw.stdout.strip():
            out[k] = raw.stdout.strip()
    return out


def main() -> int:
    got = fetch()
    if not got:
        return 0
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        tmp = OUT.with_suffix(".tmp")
        tmp.write_text(json.dumps(got))
        os.chmod(tmp, 0o600)
        tmp.replace(OUT)
    except OSError:
        return 0
    if "--verbose" in sys.argv:
        print(f"cached {sorted(got)} → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
