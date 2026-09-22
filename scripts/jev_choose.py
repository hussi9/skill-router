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
PROMPT_CHARS = 600
CONTEXT_CHARS = 300
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
        return max(0.0, min(1.0, float(a.get("confidence", 0.0))))
    except (TypeError, ValueError):
        return 0.0


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
        work = Pick(**d["work"]) if isinstance(d.get("work"), dict) else Pick(None, 0.0, "silent")
        return Choice(Pick(**d["domain"]), Pick(**d["process"]), d["path"],
                      d["path_confidence"], d["ms"], d["tokens"], cached=True, work=work)
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None


def choose(prompt: str, context: str = "", entries: Optional[Sequence[dict]] = None,
           timeout: Optional[float] = None) -> Optional[Choice]:
    """Ask Jev which domain skill, which process skill and which path fit `prompt`.

    Args:
        prompt: the user's request, as typed.
        context: tail of the previous assistant message, so "yes, continue"
            can be judged against what it answers. Empty to omit.
        entries: index entries; defaults to the router's index on disk.
        timeout: wall-clock seconds; defaults to SKILL_ROUTER_JEV_TIMEOUT (1.2).

    Returns:
        A Choice, or None on any failure (disabled, no key, empty index,
        timeout, HTTP error, malformed body). None means "fall back".
    """
    prompt = (prompt or "").strip()
    if not prompt or not enabled():
        return None
    key = _key()
    if not key:
        return None
    entries = load_entries() if entries is None else entries
    if not entries:
        return None
    questions, keymap = build_questions(entries)
    state = {"request": prompt[:PROMPT_CHARS]}
    context = (context or "").strip()[-CONTEXT_CHARS:]
    if context and len(prompt.split()) <= CONTEXT_MAX_WORDS:
        state["previous_assistant_message"] = context
    h = hashlib.sha1(json.dumps([MODEL, state, _fingerprint(questions)],
                                sort_keys=True).encode()).hexdigest()
    cpath = CACHE / f"{h}.json"
    if cpath.is_file():
        hit = _from_cache(cpath)
        if hit is not None:
            return hit
    body = {"model": MODEL, "state": state, "questions": questions}
    t0 = time.time()
    secs = TIMEOUT_S if timeout is None else timeout
    global LAST_FAILURE
    LAST_FAILURE = ""
    data = _deadline(lambda: _post(body, key, secs), secs)
    ms = int((time.time() - t0) * 1000)
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        LAST_FAILURE = "timeout" if data is None and ms >= secs * 1000 * 0.95 else "error"
        return None
    domain = _pick(answers, [q for q in questions if q.startswith("domain_")], keymap)
    process = _pick(answers, ["process"], keymap)
    if domain is None or process is None:
        return None
    pa = answers.get("path")
    path, pconf = "", 0.0
    if isinstance(pa, dict) and pa.get("choice") in PATHS:
        path, pconf = pa["choice"], _conf(pa)
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    out = Choice(domain, process, path, pconf, ms, int(usage.get("input_tokens") or 0),
                 work=_tier_pick(answers))
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        cpath.write_text(json.dumps({k: v for k, v in asdict(out).items() if k != "cached"}))
    except OSError:
        pass
    return out


def tier_only(prompt: str, timeout: Optional[float] = None) -> Optional[Pick]:
    """Just the work tier for `prompt` — the sub-agent dispatch hook's call.

    No index, ~200 input tokens, cached by prompt. None on any failure, and
    the caller treats None exactly like "heavy": leave the model alone.
    """
    prompt = (prompt or "").strip()
    if not prompt or not enabled():
        return None
    key = _key()
    if not key:
        return None
    questions = {"tier": tier_question()}
    state = {"request": prompt[:PROMPT_CHARS]}
    h = hashlib.sha1(json.dumps([MODEL, "tier", state, _fingerprint(questions)],
                                sort_keys=True).encode()).hexdigest()
    cpath = CACHE / f"{h}.json"
    try:
        d = json.loads(cpath.read_text(encoding="utf-8"))
        if isinstance(d.get("work"), dict):
            return Pick(**d["work"])
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    secs = TIMEOUT_S if timeout is None else timeout
    data = _deadline(lambda: _post({"model": MODEL, "state": state, "questions": questions},
                                   key, secs), secs)
    answers = data.get("answers") if isinstance(data, dict) else None
    if not isinstance(answers, dict):
        return None
    pick = _tier_pick(answers)
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        cpath.write_text(json.dumps({"work": asdict(pick)}))
    except OSError:
        pass
    return pick


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
