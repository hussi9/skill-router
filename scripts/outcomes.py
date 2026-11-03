#!/usr/bin/env python3
"""
outcomes.py — did the lines the router showed actually help?

The router prints a route card and, below its bar, a shortlist of possibly
relevant skills. Until now nothing recorded what happened next, so there was no
way to say whether a shown line was followed, or whether following it ended in
green tests or in the user saying "no, not that". This module is the ledger.

Events, one JSON object per line, append-only, in
~/.claude/skill_router_outcomes.jsonl (mode 600; override with
SKILL_ROUTER_OUTCOMES_FILE). Keys, hashes and skill names only — never prompt
text, commands, output or file paths. Every string is clipped to 200 chars.

  turn    {session_id, prompt_id}                          one per user prompt
  shown   {session_id, prompt_id, card, shortlist, source} what was printed
  signal  {session_id, prompt_id, kind, skill?}            what happened after
  edits   {session_id, prompt_id, files}                   hash of a file set

Signal kinds:
  loaded       a Skill call in that turn (skill_invoked.py)
  tests_green  a test command (pytest|vitest|jest|npm test|go test|cargo test)
  tests_red    passed / failed (PostToolUse Bash: `outcomes.py --post-bash`;
               PostToolUseFailure Bash: `outcomes.py --post-bash-failure`)
  correction   the session's next prompt opens with a negation (router.py)
  rework       same file set edited again within 24 h of a green turn
               (detect_rework, computed from `edits` events). Rework detection
               exists but is not recorded live yet (needs an Edit/Write hook).

Every hook path is fail-soft: nothing here may raise into a turn.
SKILL_ROUTER_NO_LEARN=1 disables writes, as it does for the router log.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Literal, Optional

Kind = Literal["loaded", "tests_green", "tests_red", "correction", "rework"]
KINDS = ("loaded", "tests_green", "tests_red", "correction", "rework")
SOURCES = ("jev", "keyless")

MAX_VALUE = 200          # no logged value may be longer: keeps prompt text out by construction
MAX_NAMES = 10
TAIL_BYTES = 256 * 1024  # enough history to find a session's latest turn
REWORK_WINDOW_S = 24 * 3600

# One list: the regex below and the installer's shell pre-filter are both built from it.
TEST_RUNNERS = ("pytest", "vitest", "jest", "npm test", "go test", "cargo test")
TEST_CMD_RE = re.compile("|".join(re.escape(r) for r in TEST_RUNNERS))
CORRECTION_RE = re.compile(r"^(no|wrong|not that|undo|revert|that's not|stop)\b", re.IGNORECASE)


def outcomes_path() -> Path:
    override = os.environ.get("SKILL_ROUTER_OUTCOMES_FILE")
    return Path(override) if override else Path.home() / ".claude" / "skill_router_outcomes.jsonl"


def _clip(value: Any) -> str:
    return str(value if value is not None else "")[:MAX_VALUE]


def _names(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for v in values or ():
        name = _clip(v).strip()
        if name and name not in out:
            out.append(name)
        if len(out) == MAX_NAMES:
            break
    return out


def _append(event: dict) -> None:
    if os.environ.get("SKILL_ROUTER_NO_LEARN") == "1":
        return
    try:
        path = outcomes_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 3), **event}) + "\n")
    except (OSError, ValueError, TypeError):
        pass


def _read_events(tail_only: bool = False) -> list[dict]:
    try:
        path = outcomes_path()
        with path.open("rb") as f:
            if tail_only:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - TAIL_BYTES))
            raw = f.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return []
    events = []
    for line in raw.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue  # includes a partial first line after a tail seek
        if isinstance(e, dict):
            events.append(e)
    return events


# ---- writers ----------------------------------------------------------------

def project_of(payload: Any) -> Optional[str]:
    """12-hex project key from a hook payload's cwd; None when absent. Never the raw path."""
    try:
        cwd = payload.get("cwd") if isinstance(payload, dict) else None
        if not isinstance(cwd, str) or not cwd.strip():
            return None
        here = str(Path(__file__).resolve().parent)
        if here not in sys.path:
            sys.path.insert(0, here)
        from project_usage import project_key  # type: ignore[import-not-found]
        return project_key(Path(cwd))
    except Exception:
        return None


def record_shown(session_id: str, prompt_id: str, card: list[str], shortlist: list[str],
                 source: str, *, held_out: bool = False, chars: Optional[int] = None,
                 project: Optional[str] = None) -> None:
    """Record exactly which skill names the router printed for this prompt.

    held_out=True means the router would have printed them but the random
    holdout suppressed the output; summarize() counts those apart. chars is the
    length of the text that was (or would have been) printed, for cost reports.
    """
    try:
        card_names, short_names = _names(card), _names(shortlist)
        if not session_id or not (card_names or short_names):
            return
        event = {"type": "shown", "session_id": _clip(session_id), "prompt_id": _clip(prompt_id),
                 "card": card_names, "shortlist": short_names,
                 "source": source if source in SOURCES else "keyless"}
        if project:
            event["project"] = _clip(project)
        if held_out:
            event["held_out"] = True
        if isinstance(chars, int) and not isinstance(chars, bool) and chars >= 0:
            event["chars"] = chars
        _append(event)
    except Exception:
        pass


def record_signal(session_id: str, prompt_id: str, kind: Kind, skill: Optional[str] = None,
                  project: Optional[str] = None) -> None:
    """Record something that happened after a prompt. Unknown kinds are dropped."""
    try:
        if kind not in KINDS or not session_id:
            return
        event = {"type": "signal", "session_id": _clip(session_id),
                 "prompt_id": _clip(prompt_id), "kind": kind}
        if skill:
            event["skill"] = _clip(skill).strip()
        if project:
            event["project"] = _clip(project)
        _append(event)
    except Exception:
        pass


def record_edits(session_id: str, prompt_id: str, paths: Iterable[str]) -> None:
    """Record the hash of a turn's edited file set (never the paths themselves)."""
    try:
        paths = [p for p in paths if p]
        if not session_id or not paths:
            return
        _append({"type": "edits", "session_id": _clip(session_id),
                 "prompt_id": _clip(prompt_id), "files": files_hash(paths)})
    except Exception:
        pass


def latest_prompt_id(session_id: str) -> str:
    """The most recent turn recorded for this session, or ""."""
    if not session_id:
        return ""
    sid = _clip(session_id)
    for e in reversed(_read_events(tail_only=True)):
        if e.get("type") == "turn" and e.get("session_id") == sid:
            return str(e.get("prompt_id") or "")
    return ""


def on_prompt(session_id: str, prompt_id: str, prompt: str) -> None:
    """UserPromptSubmit bookkeeping: score the previous turn, then open this one.

    A prompt opening with a negation ("no", "wrong", "undo" ...) is a
    correction of whatever the previous turn did, so it is attributed to the
    previous prompt_id. The prompt text itself is only matched, never stored.
    """
    try:
        if not session_id:
            return
        previous = latest_prompt_id(session_id)
        if previous and previous != _clip(prompt_id) and is_correction(prompt):
            record_signal(session_id, previous, "correction")
        _append({"type": "turn", "session_id": _clip(session_id), "prompt_id": _clip(prompt_id)})
    except Exception:
        pass


# ---- pure classifiers -------------------------------------------------------

def is_correction(prompt: str) -> bool:
    return bool(CORRECTION_RE.match((prompt or "").strip()))


def is_test_command(command: str) -> bool:
    return bool(TEST_CMD_RE.search(command or ""))


def exit_code(tool_response: Any) -> Optional[int]:
    """Exit status for a Bash call that reached PostToolUse, or None with no response.

    Live PostToolUse Bash responses carry stdout/stderr/interrupted and no exit
    field; failing commands arrive on PostToolUseFailure instead. So: an explicit
    exit field wins; else interrupted / is_error means failure; else success.
    """
    if tool_response is None:
        return None
    if not isinstance(tool_response, dict):
        return 0
    for key in ("exit_code", "exitCode", "returncode", "return_code"):
        value = tool_response.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
    if (tool_response.get("interrupted") is True or tool_response.get("is_error") is True
            or tool_response.get("isError") is True):
        return 1
    return 0


def test_signal(command: str, tool_response: Any) -> Optional[str]:
    """tests_green / tests_red for a test command with a known exit status, else None."""
    if not is_test_command(command):
        return None
    code = exit_code(tool_response)
    if code is None:
        return None
    return "tests_green" if code == 0 else "tests_red"


test_signal.__test__ = False  # type: ignore[attr-defined]  # not a pytest test


def files_hash(paths: Iterable[str]) -> str:
    joined = "\n".join(sorted({str(p) for p in paths if p}))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def detect_rework(events: list[dict], window_s: float = REWORK_WINDOW_S) -> list[tuple[str, str]]:
    """Green turns whose file set was edited again by a later turn within the window.

    Returns (session_id, prompt_id) of each such green turn, in log order.
    """
    green = {(e.get("session_id"), e.get("prompt_id")) for e in events
             if e.get("type") == "signal" and e.get("kind") == "tests_green"}
    edits = [e for e in events if e.get("type") == "edits" and e.get("files")]
    out: list[tuple[str, str]] = []
    for i, first in enumerate(edits):
        key = (first.get("session_id"), first.get("prompt_id"))
        if key not in green or key in out:
            continue
        t0 = float(first.get("ts") or 0)
        for later in edits[i + 1:]:
            if (later.get("session_id") == key[0] and later.get("prompt_id") != key[1]
                    and later.get("files") == first.get("files")
                    and 0 <= float(later.get("ts") or 0) - t0 <= window_s):
                out.append(key)  # type: ignore[arg-type]
                break
    return out


# ---- summary ----------------------------------------------------------------

def _empty() -> dict:
    return {"shown": 0, "loaded_from_card": 0, "loaded_from_shortlist": 0,
            "followed_rate": 0.0, "tests_green_after": 0, "corrections_after": 0}


def summarize(since_days: int = 30) -> dict:
    """Follow and success counts for shown lines, overall and per routing mode."""
    try:
        cutoff = time.time() - since_days * 86400
        events = [e for e in _read_events() if float(e.get("ts") or 0) >= cutoff]
    except Exception:
        events = []
    shown: dict[tuple, dict] = {}
    held: set[tuple] = set()
    for e in events:
        if e.get("type") == "shown":
            key = (e.get("session_id"), e.get("prompt_id"))
            if e.get("held_out"):
                held.add(key)   # nothing was printed: not a shown line, not a follow
            else:
                shown[key] = e
    loads: dict[tuple, set] = {}
    flags: dict[tuple, set] = {}
    for e in events:
        if e.get("type") != "signal":
            continue
        key = (e.get("session_id"), e.get("prompt_id"))
        if key not in shown:
            continue
        if e.get("kind") == "loaded" and e.get("skill"):
            loads.setdefault(key, set()).add(e["skill"])
        else:
            flags.setdefault(key, set()).add(e.get("kind"))

    total, modes = _empty(), {m: _empty() for m in SOURCES}
    followed = {"all": 0, **{m: 0 for m in SOURCES}}
    for key, e in shown.items():
        mode = e.get("source") if e.get("source") in SOURCES else "keyless"
        card, short = set(e.get("card") or ()), set(e.get("shortlist") or ())
        got = loads.get(key, set())
        from_card = len(got & card)
        from_short = len(got & short - card)
        kinds = flags.get(key, set())
        for bucket in (total, modes[mode]):
            bucket["shown"] += 1
            bucket["loaded_from_card"] += from_card
            bucket["loaded_from_shortlist"] += from_short
            bucket["tests_green_after"] += int("tests_green" in kinds)
            bucket["corrections_after"] += int("correction" in kinds)
        if from_card or from_short:
            followed["all"] += 1
            followed[mode] += 1
    total["held_out"] = len(held)
    total["followed_rate"] = followed["all"] / total["shown"] if total["shown"] else 0.0
    for m in SOURCES:
        b = modes[m]
        b["followed_rate"] = followed[m] / b["shown"] if b["shown"] else 0.0
    total["by_mode"] = modes
    return total


def _window(since_days: int) -> list[dict]:
    try:
        cutoff = time.time() - since_days * 86400
        return [e for e in _read_events() if float(e.get("ts") or 0) >= cutoff]
    except Exception:
        return []


def lift(since_days: int = 30) -> dict:
    """Turn success for routed vs held-out turns.

    A turn is known when it has any test signal (tests_green or tests_red) or a
    correction; otherwise it is unknown and counted on neither side. A known
    turn succeeds when it has tests_green, no tests_red and no correction.
    Events with an empty prompt_id are skipped (they cannot be told apart).
    lift = routed rate - held-out rate, None when either side has no known turns.
    """
    events = [e for e in _window(since_days) if e.get("prompt_id")]
    routed: set[tuple] = set()
    held: set[tuple] = set()
    for e in events:
        if e.get("type") == "shown":
            (held if e.get("held_out") else routed).add((e.get("session_id"), e.get("prompt_id")))
    held -= routed
    kinds: dict[tuple, set] = {}
    for e in events:
        if e.get("type") == "signal":
            kinds.setdefault((e.get("session_id"), e.get("prompt_id")), set()).add(e.get("kind"))

    def tally(keys: set) -> dict:
        success = known = 0
        for k in keys:
            ks = kinds.get(k, set())
            if ks & {"tests_green", "tests_red", "correction"}:
                known += 1
                success += int("tests_green" in ks and not ks & {"tests_red", "correction"})
        return {"success": success, "known": known}

    r, h = tally(routed), tally(held)
    value = (r["success"] / r["known"] - h["success"] / h["known"]) if r["known"] and h["known"] else None
    return {"routed": r, "held_out": h, "lift": value}


def per_skill(since_days: int = 90) -> dict[str, dict[str, int]]:
    """Per skill name: how many printed lines showed it, and how often it was loaded.

    shown counts non-held-out `shown` events whose card or shortlist named the
    skill (held-out turns printed nothing). loaded counts every `loaded` signal
    for the skill in the window, shown or not, so "shown often, loaded never"
    is a strict claim. {} when there is no log.
    """
    out: dict[str, dict[str, int]] = {}
    for e in _window(since_days):
        if e.get("type") == "shown" and not e.get("held_out"):
            for name in set(e.get("card") or ()) | set(e.get("shortlist") or ()):
                if isinstance(name, str) and name:
                    out.setdefault(name, {"shown": 0, "loaded": 0})["shown"] += 1
        elif e.get("type") == "signal" and e.get("kind") == "loaded":
            name = e.get("skill")
            if isinstance(name, str) and name:
                out.setdefault(name, {"shown": 0, "loaded": 0})["loaded"] += 1
    return out


def median_chars(since_days: int = 30) -> Optional[float]:
    """Median printed length per shown line (events that recorded chars), else None."""
    import statistics
    vals = [e["chars"] for e in _window(since_days)
            if e.get("type") == "shown" and not e.get("held_out")
            and isinstance(e.get("chars"), int) and not isinstance(e.get("chars"), bool)]
    return float(statistics.median(vals)) if vals else None


# ---- payload shape capture ---------------------------------------------------

SHAPES_MAX_RECORDS = 50
SHAPES_MAX_DEPTH = 4


def shapes_path() -> Path:
    override = os.environ.get("SKILL_ROUTER_SHAPES_FILE")
    return Path(override) if override else (
        Path.home() / ".claude" / "skill_router_cache" / "payload-shapes.jsonl")


def _type_name(value: Any) -> str:
    if isinstance(value, list):
        return f"list[{_type_name(value[0]) if value else 'empty'}]"
    return type(value).__name__


def payload_shape(value: Any, prefix: str = "", depth: int = 1,
                  out: Optional[dict] = None) -> dict:
    """Map each key path to a type name. Values are never read into the result."""
    out = {} if out is None else out
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(child, dict) and child and depth < SHAPES_MAX_DEPTH:
                payload_shape(child, path, depth + 1, out)
            else:
                out[path] = _type_name(child)
    return out


def capture_shape(payload: Any, event: str) -> None:
    """With SKILL_ROUTER_CAPTURE_SHAPES=1, append the payload's key-path/type map
    (no values), at most SHAPES_MAX_RECORDS records in total. Never raises."""
    if os.environ.get("SKILL_ROUTER_CAPTURE_SHAPES") != "1" or not isinstance(payload, dict):
        return
    try:
        path = shapes_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, encoding="utf-8") as f:
                if sum(1 for _ in f) >= SHAPES_MAX_RECORDS:
                    return
        except FileNotFoundError:
            pass
        record = {"event": str(payload.get("hook_event_name") or event),
                  "tool": str(payload.get("tool_name") or ""),
                  "shape": payload_shape(payload)}
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception:
        pass


# ---- hook entry -------------------------------------------------------------

def post_bash(payload: dict, failed: bool = False) -> None:
    """PostToolUse (or, with failed=True, PostToolUseFailure) Bash: record
    tests_green / tests_red against the current turn."""
    if not isinstance(payload, dict) or payload.get("agent_id"):
        return
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return
    command = str(tool_input.get("command") or "")
    if failed:
        kind = "tests_red" if is_test_command(command) else None
    else:
        kind = test_signal(command, payload.get("tool_response"))
    if kind is None:
        return
    sid = str(payload.get("session_id") or "")
    pid = str(payload.get("prompt_id") or "") or latest_prompt_id(sid)
    if sid and pid:
        record_signal(sid, pid, kind, project=project_of(payload))  # type: ignore[arg-type]


def main(argv: list[str]) -> int:
    if os.environ.get("SKILL_ROUTER_OFF") == "1":
        return 0
    if "--post-bash" in argv or "--post-bash-failure" in argv:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
        except (ValueError, OSError):
            return 0
        failed = "--post-bash-failure" in argv
        capture_shape(payload, "PostToolUseFailure" if failed else "PostToolUse")
        post_bash(payload, failed=failed)
        return 0
    if "--summary" in argv:
        print(json.dumps(summarize(), indent=1))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as exc:  # a hook must never fail the turn
        print(f"[skill-router-warn] outcomes: {exc}", file=sys.stderr)
        sys.exit(0)
