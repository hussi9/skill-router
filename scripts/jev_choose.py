#!/usr/bin/env python3
"""
jev_choose.py — Jev (TypeSafe System One) picks the skill from the WHOLE index.

Why this exists (measured 2026-09-21 on 66 real prompt -> Skill pairs, see
docs/jev-eval-2026-09-21/):
  - lexical rank -> Gemini over the top 8 scored 10/66. The model was never the
    problem: the right skill was in the lexical top 30 only 23/66 times,
    because the prompts are misspelled and token matching dies on typos.
  - Jev over the same top 8 scored 13/66. Same ceiling, same cause.
  - Jev over every indexed skill in one call, no pre-filter, scored 39/66 at
    ~390 ms and ~14.5k input tokens ($0.0006). At confidence >= 0.8 it
    answered 37/66 and was right 84% of the time.

So there is no retrieval stage here. One request carries independent Choice
questions over the same state:

    domain_N   which skill's subject matter fits (index entries whose kind is
               not "process"), plus "none". A Choice caps at 255 options, so a
               bigger index is split across domain_0, domain_1, ... rather
               than truncated.
    process    which working method fits (kind == "process"), plus "none".
    path       BROKEN | BUILD | OPERATE | QUESTION — used by the router only
               when its own regex triage is silent.
    tier       light | standard | heavy — how much model the work needs. The
               router prints it on the card and task_brief.py turns it into
               the `model` of a sub-agent dispatch (light → haiku, standard →
               sonnet, heavy → inherit). Same 0.8 gate: below it, inherit.
               `tier_only()` asks just this question (no index) so the Task
               hook can judge a sub-agent prompt in one small call.

Options are sent as opaque keys (d0.., p0..) and mapped back here. A returned
key this module did not send is dropped; a returned *name* is never trusted.

Jev cannot generate text, so build_index.py enrichment stays on Gemini/Haiku.

The key comes from the environment, then from
~/.claude/skill_router_cache/env.json (refresh_env.py, SessionStart). Nothing
here calls Doppler. Answers are cached under skill_router_cache/jev/ by
prompt + context + index fingerprint. Every failure returns None — the router
then falls back to the lexical + Gemini path. Nothing raises.

    choose(prompt, context="") -> Choice | None
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

HOME = Path.home()
CACHE_ROOT = Path(os.environ.get("SKILL_ROUTER_CACHE_DIR") or HOME / ".claude" / "skill_router_cache")
ENV_FILE = CACHE_ROOT / "env.json"
CACHE = CACHE_ROOT / "jev"

URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"                 # pinned: thresholds below were measured on it
KEY_NAME = "TYPESAFE_API_KEY"
# Latency is bimodal, not slow: 194 calls on 2026-09-21 afternoon were either
# 250-550 ms or 2.6-6.8 s (35 % of them), with nothing in between; the morning
# run had 0 of 126 over 2 s. 1.2 s keeps every fast answer and stops waiting
# for a slow one 0.8 s sooner than the original 2 s did.
TIMEOUT_S = float(os.environ.get("SKILL_ROUTER_JEV_TIMEOUT", "1.2"))
LAST_FAILURE = ""                    # "" | "timeout" | "error" — why the last choose() gave None

ROUTE_AT = 0.8                       # >= : route
SUGGEST_AT = 0.5                     # >= : show as a suggestion; below: silent
MAX_OPTIONS = 255                    # Choice limit, "none" included
# Not offered as options. Agents are not Skill targets. Slash commands are typed
# by the user, never routed to — offering them cost 6 of 33 process hits on the
# eval set (debug vs systematic-debugging, feature-dev vs brainstorming).
# Builtins (artifact-design, dataviz, loop ...) are advertised by the harness in
# every session with their own triggers; routing to them added wrong confident
# routes and no right ones (29/32 -> 31/34 right at >= 0.8 without them). A user
# skill that shares a builtin's name is indexed as a skill and stays routable.
NOT_ROUTABLE = ("agent", "command", "plugin-command", "builtin")
SELF_SKILL = "skill-router"          # the router never routes to its own documentation
CACHE_VERSION = 2
PROMPT_CHARS = 16000
CONTEXT_CHARS = 4000
# The previous assistant turn is sent only for short prompts. Measured on 63
# real pairs: <= 15 words it lifted hits 13 -> 15 and confident routes from
# 9/11 to 13/14 right ("yes" -> prove-idea at 1.00); on longer prompts it cost
# 2 hits of 36 by pulling the choice toward whatever was being discussed.
CONTEXT_MAX_WORDS = 15

PATHS = ("BROKEN", "BUILD", "OPERATE", "QUESTION")
NONE = "none"

# Work tier → model. Conservative on purpose: a wrong downgrade costs quality
# the user cannot see, a wrong "inherit" costs only money. So only a >= 0.8
# light/standard answer moves off the session model, and "heavy" is the default.
TIERS = ("light", "standard", "heavy")
TIER_MODEL = {"light": "haiku", "standard": "sonnet", "heavy": "inherit"}
TIER_CRITERIA = {
    "light": ("Mechanical work with one right answer and no design judgment: find, list or "
              "grep files; read and summarise; rename or move; reformat; apply one fixed rule "
              "across many files; run a command and report its output; answer a lookup."),
    "standard": ("Routine implementation against a clear spec where the approach is obvious: "
                 "a small feature or endpoint, a known fix, a unit test, a doc section, a config "
                 "or dependency change. Some judgment, little ambiguity."),
    "heavy": ("Judgment-heavy or open-ended: architecture and design decisions, root-cause "
              "debugging, security or auth, data migrations, ambiguous or multi-part "
              "requirements, reviews, anything where a wrong call is expensive to undo."),
}


def model_for(pick: "Optional[Pick]") -> str:
    """The model a tier Pick earns: haiku / sonnet at >= ROUTE_AT, else inherit."""
    if pick is None or pick.tier != "route" or pick.name not in TIER_MODEL:
        return "inherit"
    return TIER_MODEL[pick.name]

_TYPO_NOTE = "The request may contain spelling mistakes; judge the intended meaning."
_CONTEXT_NOTE = (" When `previous_assistant_message` is present and the request is a short reply "
                 "to it (yes, continue, do it), judge what that reply asks to proceed with.")
TIER_INSTRUCTIONS = ("How much reasoning the developer's `request` needs from the model that "
                     "does the work. Pick `heavy` whenever unsure. " + _TYPO_NOTE)


@dataclass(frozen=True)
class Pick:
    """One Choice answer. name None means Jev chose "none" (or nothing usable)."""
    name: Optional[str]
    confidence: float
    tier: str                        # route | suggest | silent


@dataclass(frozen=True)
class Choice:
    domain: Pick
    process: Pick
    path: str                        # one of PATHS, or "" when unusable
    path_confidence: float
    ms: int
    tokens: int
    cached: bool = False
    work: Pick = Pick(None, 0.0, "silent")   # tier: name in TIERS, or None

    @property
    def model(self) -> str:
        """haiku / sonnet / inherit — what the work tier earns."""
        return model_for(self.work)


def enabled() -> bool:
    return os.environ.get("SKILL_ROUTER_JEV", "1") not in ("0", "off", "false")


def tier(confidence: float) -> str:
    if confidence >= ROUTE_AT:
        return "route"
    if confidence >= SUGGEST_AT:
        return "suggest"
    return "silent"


def _key() -> str:
    if os.environ.get(KEY_NAME):
        return os.environ[KEY_NAME]
    try:
        return str(json.loads(ENV_FILE.read_text(encoding="utf-8")).get(KEY_NAME) or "")
    except (OSError, json.JSONDecodeError, AttributeError):
        return ""


def load_entries() -> list[dict]:
    """Skill entries from the router's index (agents are not Skill options)."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import index_match  # type: ignore[import-not-found]
        path = Path(index_match.INDEX_FILE)
    except ImportError:
        path = HOME / ".claude" / "skill_index.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    return [e for e in entries or [] if isinstance(e, dict) and e.get("name")]


def _summary(e: dict, n: int) -> str:
    if isinstance(e.get("routing_summary"), str) and e["routing_summary"]:
        return f"{e['name']}: {e['routing_summary']}"
    use_when = "; ".join(str(u) for u in (e.get("use_when") or [])[:2])
    return f"{e['name']}: {str(e.get('description') or '')[:n]} | {use_when}"[: n + 90]


def build_questions(entries: Sequence[dict]) -> tuple[dict, dict[str, dict[str, str]]]:
    """(questions, keymap). keymap[question_id][option_key] -> index name."""
    skills = [e for e in entries if e.get("type") not in NOT_ROUTABLE and e.get("name")
              and e["name"] != SELF_SKILL]
    domain = [e for e in skills if e.get("kind") != "process"]
    process = [e for e in skills if e.get("kind") == "process"][: MAX_OPTIONS - 1]
    questions: dict = {}
    keymap: dict[str, dict[str, str]] = {}
    per = MAX_OPTIONS - 1
    chunks = [domain[i:i + per] for i in range(0, len(domain), per)] or [[]]
    n = 0
    for ci, chunk in enumerate(chunks):
        qid = f"domain_{ci}"
        keys = {f"d{n + i}": e["name"] for i, e in enumerate(chunk)}
        n += len(chunk)
        crit = {k: _summary(e, 150) for k, e in zip(keys, chunk)}
        crit[NONE] = "No listed skill fits; this is general work or conversation"
        questions[qid] = {
            "type": "choice",
            "instructions": ("Which installed skill's subject matter matches what the developer's "
                             "`request` is about. " + _TYPO_NOTE + _CONTEXT_NOTE),
            "criteria": crit}
        keymap[qid] = keys
    pkeys = {f"p{i}": e["name"] for i, e in enumerate(process)}
    pcrit = {k: _summary(e, 200) for k, e in zip(pkeys, process)}
    pcrit[NONE] = "No process discipline applies; a direct answer or a small direct action"
    questions["process"] = {
        "type": "choice",
        "instructions": ("Which working method the developer's `request` calls for. "
                         + _TYPO_NOTE + _CONTEXT_NOTE),
        "criteria": pcrit}
    keymap["process"] = pkeys
    questions["path"] = {
        "type": "choice",
        "instructions": "What kind of turn the developer's `request` is. " + _TYPO_NOTE + _CONTEXT_NOTE,
        "criteria": {
            "BROKEN": "Something is failing, erroring, crashing or behaving wrongly and needs fixing",
            "BUILD": "Create something new: a feature, page, component, script, skill or document",
            "OPERATE": "Improve, review, audit, refactor, ship, deploy, research or configure existing work",
            "QUESTION": "The developer wants an answer, an explanation or a chat reply, not work done"}}
    questions["tier"] = tier_question()
    return questions, keymap


def tier_question() -> dict:
    return {"type": "choice", "instructions": TIER_INSTRUCTIONS, "criteria": dict(TIER_CRITERIA)}


def _tier_pick(answers: dict) -> Pick:
    a = answers.get("tier")
    if not isinstance(a, dict) or a.get("choice") not in TIERS:
        return Pick(None, 0.0, "silent")
    conf = _conf(a)
    return Pick(a["choice"], conf, tier(conf))


def _post(body: dict, key: str, timeout: float) -> Optional[dict]:
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json",
                                          "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _deadline(fn: Callable[[], Optional[dict]], seconds: float) -> Optional[dict]:
    """urllib's timeout is per socket operation; a hook needs a wall-clock one."""
    box: list[Optional[dict]] = [None]

    def run() -> None:
        try:
            box[0] = fn()
        except Exception:                                  # noqa: BLE001 — never raise into a hook
            box[0] = None
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(seconds)
    return None if t.is_alive() else box[0]


def _conf(a: dict) -> float:
    try:
        value = float(a.get("confidence", 0.0))
        return value if math.isfinite(value) and 0 <= value <= 1 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _bounded(value: str, limit: int) -> tuple[str, dict]:
    value = (value or "").strip()
    truncated = len(value) > limit
    marker = "\n[... input omitted ...]\n"
    keep = limit - len(marker)
    sent = value if not truncated else value[:keep // 2] + marker + value[-(keep - keep // 2):]
    return sent, {"original_chars": len(value), "sent_chars": len(sent),
                  "truncated": truncated,
                  "sha256": hashlib.sha256(value.encode()).hexdigest()}


def _log_event(event: dict) -> None:
    """Append one bounded JSON record; never include request text or provider errors."""
    try:
        path = CACHE.parent / "jev-decisions.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, (json.dumps(event, allow_nan=False) + "\n").encode())
        finally:
            os.close(fd)
    except (OSError, TypeError, ValueError):
        pass


def _usage(data: dict) -> int:
    usage = data.get("usage")
    try:
        return max(0, int(usage.get("input_tokens") or 0)) if isinstance(usage, dict) else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def _safe_work(pick: Pick, truncated: bool) -> Pick:
    return Pick("heavy", 0.0, "silent") if truncated else pick


def _cached_pick(value: dict) -> Pick:
    conf = _conf(value)
    return Pick(value.get("name"), conf, tier(conf))


def _pick(answers: dict, qids: Sequence[str], keymap: dict[str, dict[str, str]]) -> Optional[Pick]:
    """Fold one or more chunked Choice answers into a Pick. None = malformed."""
    named: list[tuple[float, str]] = []
    none_conf: list[float] = []
    for qid in qids:
        a = answers.get(qid)
        if not isinstance(a, dict) or not isinstance(a.get("choice"), str):
            return None
        choice, conf = a["choice"], _conf(a)
        if choice == NONE:
            none_conf.append(conf)
        elif choice in keymap[qid]:
            named.append((conf, keymap[qid][choice]))
        # anything else is a key we never sent: dropped, contributes nothing
    if named:
        named.sort(reverse=True)
        conf, name = named[0]
        # Two halves of a split index each naming a skill is a disagreement the
        # probabilities cannot settle (they were normalised separately).
        if len(named) > 1 and named[1][0] >= SUGGEST_AT:
            conf = min(conf, ROUTE_AT - 0.01)
        return Pick(name, conf, tier(conf))
    if none_conf and len(none_conf) == len(qids):
        conf = min(none_conf)
        return Pick(None, conf, tier(conf))
    return Pick(None, 0.0, "silent")


def _fingerprint(questions: dict) -> str:
    return hashlib.sha1(json.dumps(questions, sort_keys=True).encode()).hexdigest()[:12]


def _from_cache(path: Path) -> Optional[Choice]:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        work = _cached_pick(d["work"]) if isinstance(d.get("work"), dict) else Pick(None, 0.0, "silent")
        return Choice(_cached_pick(d["domain"]), _cached_pick(d["process"]), d["path"],
                      d["path_confidence"], d["ms"], d["tokens"], cached=True, work=work)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return None


def _run(prompt: str, context: str, entries: Optional[Sequence[dict]],
         timeout: Optional[float], operation: str, session_id: Optional[str],
         prompt_id: Optional[str]):
    global LAST_FAILURE
    LAST_FAILURE = ""
    started = time.monotonic()
    request, request_meta = _bounded(prompt, PROMPT_CHARS)
    previous, context_meta = _bounded(context, CONTEXT_CHARS)
    state = {"request": request}
    if previous and operation == "choose" and len((prompt or "").split()) <= CONTEXT_MAX_WORDS:
        state["previous_assistant_message"] = previous
    else:
        context_meta = {**context_meta, "sent_chars": 0, "truncated": False}
    truncated = request_meta["truncated"] or context_meta["truncated"]
    event = {"schema_version": 1, "timestamp": time.time(), "operation": operation,
             "provider": "typesafe", "provider_model": MODEL, "cache_version": CACHE_VERSION,
             "session_id": session_id or os.environ.get("SKILL_ROUTER_SESSION_ID", ""),
             "prompt_id": prompt_id or os.environ.get("SKILL_ROUTER_PROMPT_ID", ""),
             "input": {"request": request_meta, "context": context_meta},
             "cache_hit": False, "provider_attempted": False, "input_tokens": 0,
             "model_requested": "inherit", "failure": ""}

    def finish(result=None, failure=""):
        global LAST_FAILURE
        if failure:
            LAST_FAILURE = failure
        event["failure"] = failure
        event["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        work = result.work if isinstance(result, Choice) else result
        if isinstance(work, Pick):
            event["confidence"] = work.confidence
            event["work_tier"] = work.name
            event["model_requested"] = model_for(work)
        if isinstance(result, Choice):
            event["confidence_by_question"] = {"domain": result.domain.confidence,
                "process": result.process.confidence, "path": result.path_confidence,
                "tier": result.work.confidence}
        _log_event(event)
        return result

    if not request:
        return finish(failure="empty_input")
    if not enabled():
        return finish(failure="disabled")
    key = _key()
    if not key:
        return finish(failure="no_key")
    if operation == "choose":
        entries = load_entries() if entries is None else entries
        if not entries:
            return finish(failure="empty_index")
        questions, keymap = build_questions(entries)
    else:
        questions, keymap = {"tier": tier_question()}, {}
    fingerprint = _fingerprint(questions)
    event["index_fingerprint"] = fingerprint
    # Include full input hashes, so omitted middle text cannot alias another request.
    h = hashlib.sha256(json.dumps([CACHE_VERSION, MODEL, operation, state,
        request_meta["sha256"], context_meta["sha256"], fingerprint], sort_keys=True).encode()).hexdigest()
    cpath = CACHE / f"{h}.json"
    hit = None
    if operation == "choose":
        hit = _from_cache(cpath)
    else:
        try:
            d = json.loads(cpath.read_text(encoding="utf-8"))
            if isinstance(d.get("work"), dict):
                hit = _cached_pick(d["work"])
        except (OSError, ValueError, TypeError, AttributeError):
            pass
    if hit is not None:
        event["cache_hit"] = True
        if isinstance(hit, Choice):
            event["cached_input_tokens"] = hit.tokens
        return finish(hit)
    secs = TIMEOUT_S if timeout is None else timeout
    event["provider_attempted"] = True
    call_started = time.monotonic()
    data = _deadline(lambda: _post({"model": MODEL, "state": state, "questions": questions},
                                   key, secs), secs)
    ms = int((time.monotonic() - call_started) * 1000)
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        return finish(failure="timeout" if data is None and ms >= secs * 1000 * .95 else "error")
    event["input_tokens"] = _usage(data)
    work = _safe_work(_tier_pick(answers), truncated)
    if operation == "choose":
        domain = _pick(answers, [q for q in questions if q.startswith("domain_")], keymap)
        process = _pick(answers, ["process"], keymap)
        if domain is None or process is None:
            return finish(failure="malformed_answers")
        # Keep raw chunk decisions separate from the capped aggregate pick.
        # Only supplied option names are exposed, never request/criteria text.
        chunks = []
        named_confidences = []
        for qid in questions:
            if not qid.startswith("domain_"):
                continue
            raw = answers[qid]
            selected = keymap[qid].get(raw.get("choice"))
            confidence = _conf(raw)
            chunks.append({"question_id": qid, "selected_skill": selected,
                           "confidence": confidence})
            if selected is not None:
                named_confidences.append(confidence)
        named_confidences.sort(reverse=True)
        event["domain_chunks"] = chunks
        event["chunk_conflict"] = (len(named_confidences) > 1
                                   and named_confidences[1] >= SUGGEST_AT)
        pa = answers.get("path")
        path, pconf = "", 0.0
        if isinstance(pa, dict) and pa.get("choice") in PATHS:
            path, pconf = pa["choice"], _conf(pa)
        out = Choice(domain, process, path, pconf, ms, event["input_tokens"], work=work)
        cached = {k: v for k, v in asdict(out).items() if k != "cached"}
    else:
        if not isinstance(answers.get("tier"), dict) or answers["tier"].get("choice") not in TIERS:
            return finish(failure="malformed_answers")
        out, cached = work, {"work": asdict(work)}
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        cpath.write_text(json.dumps(cached, allow_nan=False))
    except (OSError, ValueError):
        pass
    return finish(out)


def choose(prompt: str, context: str = "", entries: Optional[Sequence[dict]] = None,
           timeout: Optional[float] = None, *, session_id: Optional[str] = None,
           prompt_id: Optional[str] = None) -> Optional[Choice]:
    """Route complete bounded input; oversized head/tail input never earns a downgrade."""
    return _run(prompt, context, entries, timeout, "choose", session_id, prompt_id)


def tier_only(prompt: str, timeout: Optional[float] = None, *,
              session_id: Optional[str] = None, prompt_id: Optional[str] = None) -> Optional[Pick]:
    """Judge a bounded request. Any omitted input forces inherited execution model."""
    return _run(prompt, "", None, timeout, "tier", session_id, prompt_id)


def status() -> dict:
    entries = load_entries()
    questions, _ = build_questions(entries) if entries else ({}, {})
    return {"enabled": enabled(), "key": bool(_key()), "model": MODEL,
            "index_entries": len(entries),
            "domain_questions": sum(q.startswith("domain_") for q in questions),
            "cache_entries": len(list(CACHE.glob("*.json"))) if CACHE.is_dir() else 0}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--status":
        print(json.dumps(status(), indent=1))
    else:
        got = choose(" ".join(sys.argv[1:]) or sys.stdin.read())
        print(json.dumps(asdict(got) if got else None, indent=1))
