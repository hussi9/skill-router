#!/usr/bin/env python3
"""
listing_probe.py -- measure the skill listing Claude Code actually sends.

Simulating the capped listing proved far off (predicted 154 described skills; the harness
described 43 of 764 entries with a 1M context). The only trustworthy figure is the one Claude
Code reports itself in `/context`. `measure` starts ONE Claude Code session (about a minute,
normal subscription) running `claude -p "/context"` in the project, parses the report, and
caches it at ~/.claude/skill_router_cache/listing/<project_key>.json (mode 600, atomic).

Only an explicit `cli.py listing measure` runs the probe; hooks and the doctor only read the cache.
The cache holds skill names (local only); committed docs carry counts only.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import project_usage  # noqa: E402

TIMEOUT_S = 300
FRESH_DAYS = 7
COMMAND = ["claude", "-p", "/context", "--output-format", "text"]

_HEADER = re.compile(r"^##\s+Context Usage\s*$", re.M)
_WINDOW = re.compile(r"\*\*Tokens:\*\*\s*[\d.]+\s*[kKmM]?\s*/\s*([\d.]+\s*[kKmM]?)")
_CATEGORY = re.compile(r"^\|\s*Skills\s*\|\s*([\d.,]+\s*[kKmM]?)\s*\|", re.M)
_SKILLS_SECTION = re.compile(r"^###\s+Skills\s*$", re.M)
_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|\s*(.*?)\s*\|\s*$")
_TOKEN = re.compile(r"^(<\s*\d+|~\s*[\d.,]+\s*[kKmM]?)$")


class ProbeError(Exception):
    """One-line reason the measurement failed."""


def _number(text: str) -> Optional[int]:
    t = text.strip().lower().replace(",", "").replace(" ", "")
    mult = 1
    if t.endswith("k"):
        mult, t = 1_000, t[:-1]
    elif t.endswith("m"):
        mult, t = 1_000_000, t[:-1]
    try:
        return int(round(float(t) * mult))
    except ValueError:
        return None


def parse_context(text: str) -> Optional[dict]:
    """{"skills_tokens", "entries": [{name, source, described, tokens}], "context_window"} or None."""
    if not isinstance(text, str) or not _HEADER.search(text):
        return None
    cat = _CATEGORY.search(text)
    sec = _SKILLS_SECTION.search(text)
    if not cat or not sec:
        return None
    skills_tokens = _number(cat.group(1))
    if skills_tokens is None:
        return None
    window = _WINDOW.search(text)
    entries: list[dict] = []
    for line in text[sec.end():].splitlines():
        if line.startswith("#"):
            break
        m = _ROW.match(line.strip())
        if not m or m.group(1) in ("Skill", "-------"):
            continue
        name, source, tok = m.group(1), m.group(2), m.group(3).strip()
        if set(name) <= {"-"}:
            continue
        if not _TOKEN.match(tok):
            return None                       # a token form we do not know: the format changed
        described = tok.startswith("~")
        entries.append({"name": name, "source": source, "described": described,
                        "tokens": _number(tok[1:]) if described else None})
    if not entries:
        return None
    if skills_tokens < sum(e["tokens"] or 0 for e in entries):
        return None                           # the table contradicts the category total
    return {"skills_tokens": skills_tokens, "entries": entries,
            "context_window": _number(window.group(1)) if window else None}


def cache_dir() -> Path:
    return Path.home() / ".claude" / "skill_router_cache" / "listing"


def _path(key: str) -> Path:
    return cache_dir() / f"{key}.json"


def save(key: str, data: dict) -> Path:
    """Atomic write, mode 600."""
    d = cache_dir()
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(d, 0o700)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, _path(key))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return _path(key)


def load(project_key: str) -> Optional[dict]:
    try:
        data = json.loads(_path(project_key).read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        return None
    return data


def measure(project: Path, runner: Callable = subprocess.run) -> dict:
    """Run the probe in `project`, cache and return the parsed measurement."""
    project = Path(project).resolve()
    env = dict(os.environ, SKILL_ROUTER_OFF="1")
    try:
        proc = runner(COMMAND, cwd=str(project), env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                      timeout=TIMEOUT_S)
    except FileNotFoundError:
        raise ProbeError("the `claude` command was not found on PATH") from None
    except subprocess.TimeoutExpired:
        raise ProbeError(f"claude did not answer within {TIMEOUT_S} s") from None
    except OSError as e:
        raise ProbeError(f"could not start claude: {e.strerror or e}") from None
    if getattr(proc, "returncode", 1) != 0:
        raise ProbeError(f"claude exited with status {proc.returncode}")
    parsed = parse_context(proc.stdout or "")
    if parsed is None:
        raise ProbeError("unrecognised /context output (format changed?)")
    key = project_usage.project_key(project)
    parsed.update(measured_at=time.time(), project_key=key)
    save(key, parsed)
    return parsed


def age_days(m: dict, now: Optional[float] = None) -> int:
    now = time.time() if now is None else now
    try:
        return max(0, int((now - float(m.get("measured_at") or 0)) // 86400))
    except (TypeError, ValueError):
        return 10**6


def is_fresh(m: Optional[dict], days: int = FRESH_DAYS) -> bool:
    return bool(m) and age_days(m) < days


def counts(m: dict) -> tuple[int, int]:
    """(described, total entries)."""
    ents = m.get("entries") or []
    return sum(1 for e in ents if e.get("described")), len(ents)


def line(m: dict) -> str:
    """Counts-only summary; labelled stale after FRESH_DAYS."""
    d, n = counts(m)
    win = m.get("context_window")
    text = (f"measured: {d} of {n} entries described; Skills ~{m.get('skills_tokens', 0)} tokens"
            + (f"; context window {win}" if win else ""))
    age = age_days(m)
    return text + (f" (stale: {age} days old, re-run `python3 scripts/cli.py listing measure`)"
                   if not is_fresh(m) else f" ({age} days old)")


def fresh_for(project: Path) -> Optional[dict]:
    m = load(project_usage.project_key(Path(project)))
    return m if is_fresh(m) else None


def main_measure(project: Path) -> int:
    print("Starting one Claude Code session to read /context (about a minute, uses your normal subscription)...")
    try:
        m = measure(project)
    except ProbeError as e:
        print(f"error: {e}")
        return 1
    print(line(m))
    return 0


def main_show(project: Path) -> int:
    m = load(project_usage.project_key(Path(project)))
    print(line(m) if m else "not measured — python3 scripts/cli.py listing measure")
    return 0
