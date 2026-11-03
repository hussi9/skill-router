#!/usr/bin/env python3
"""
project_usage.py -- which skills each project actually uses.

Reads Claude Code transcripts (~/.claude/projects/<encoded-dir>/*.jsonl) and
counts `Skill` tool_use events per project. Only the skill name and the event
timestamp are kept; message text is never retained. Sub-agent (isSidechain)
events are counted too: a sub-agent's skill use is still the project's usage.

The folder name is the project's cwd with every non-alphanumeric character
turned into "-", which is lossy, so decoding walks the real filesystem and
keeps `dir` only when a matching directory exists (else None).

Contract: an entry has `dir`, `joinable` and `ambiguous`. `joinable` is True
only when the folder decoded to exactly one existing directory, so its key
equals the hook-side project_key(cwd). `dir is None` => no join to hook logs
(directory gone, or `ambiguous` because several directories encode to the same
name, e.g. `a-b` vs `a/b`); global usage still counts, but callers that change
project settings must skip such entries (their key is a hash of the folder name).

project_key() is the shared 12-hex project identity (sha256 of the resolved
path); logs carry it instead of the raw path.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Optional

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")


def project_key(path: Path) -> str:
    """12 hex chars of sha256 over the resolved path."""
    return hashlib.sha256(str(Path(path).resolve()).encode("utf-8")).hexdigest()[:12]


def _encode(name: str) -> str:
    return _NON_ALNUM.sub("-", name)


def decode_candidates(folder: str) -> list[str]:
    """Every existing directory whose encoded name equals `folder` (usually one, sometimes none or several)."""
    if not folder.startswith("-"):
        return []
    found: list[str] = []

    def walk(base: Path, rest: str) -> None:
        try:
            children = sorted(os.listdir(base))
        except OSError:
            return
        for child in children:
            enc = _encode(child)
            p = base / child
            if rest == enc:
                if p.is_dir():
                    found.append(str(p))
            elif rest.startswith(enc + "-") and p.is_dir():
                walk(p, rest[len(enc) + 1:])

    walk(Path("/"), folder[1:])
    return found


def decode_dir(folder: str) -> Optional[str]:
    """The directory a transcript folder name was encoded from; None if gone or ambiguous."""
    c = decode_candidates(folder)
    return c[0] if len(c) == 1 else None


def _parse_ts(ts: str) -> Optional[float]:
    try:
        return _dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def _skill_uses(path: Path):
    """Yield (skill, timestamp) for each Skill tool_use in one transcript."""
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"Skill"' not in line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(ev, dict) or ev.get("type") != "assistant":
                    continue
                msg = ev.get("message")
                content = msg.get("content") if isinstance(msg, dict) else None
                if not isinstance(content, list):
                    continue
                ts = ev.get("timestamp")
                for part in content:
                    if (isinstance(part, dict) and part.get("type") == "tool_use"
                            and part.get("name") == "Skill"):
                        inp = part.get("input")
                        name = inp.get("skill") if isinstance(inp, dict) else None
                        if isinstance(name, str) and name.strip() and isinstance(ts, str):
                            yield name.strip(), ts
    except OSError:
        return


def usage_by_project(transcripts_dir: Path, since_days: int = 90) -> dict[str, dict]:
    """{project_key: {"dir": str | None, "joinable": bool, "ambiguous": bool, "skills": {name: count}, "last_used": {name: ts}}}"""
    root = Path(transcripts_dir)
    cutoff = time.time() - since_days * 86400
    out: dict[str, dict] = {}
    try:
        folders = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return out
    for folder in folders:
        cands = decode_candidates(folder.name)
        decoded = cands[0] if len(cands) == 1 else None
        ambiguous = len(cands) > 1
        key = project_key(Path(decoded)) if decoded else \
            hashlib.sha256(folder.name.encode("utf-8")).hexdigest()[:12]
        for tf in [*folder.glob("*.jsonl"), *folder.glob("*/subagents/*.jsonl")]:
            try:
                if tf.stat().st_mtime < cutoff:
                    continue
            except OSError:
                continue
            for skill, ts in _skill_uses(tf):
                epoch = _parse_ts(ts)
                if epoch is None or epoch < cutoff:
                    continue
                entry = out.setdefault(key, {"dir": decoded, "joinable": decoded is not None,
                                                     "ambiguous": ambiguous, "skills": {}, "last_used": {}})
                entry["skills"][skill] = entry["skills"].get(skill, 0) + 1
                prev = _parse_ts(entry["last_used"].get(skill, ""))
                if prev is None or epoch > prev:
                    entry["last_used"][skill] = ts
    return out
