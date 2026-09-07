#!/usr/bin/env python3
"""
index_match.py — rank skills against a prompt using the enriched index.

Replaces catalog_match.py's role in routing. Same idea (weighted IDF overlap,
pure stdlib, milliseconds) but over fields that say *when* a skill applies:

    name        3.0     use_when   2.5     projects   3.5
    keywords    1.5     description 1.0    body       0.3

plus query-side synonym expansion, so "restarted" reaches a skill indexed on
"kernel panic" and "post" reaches one indexed on "linkedin".

Public API:
    rank(prompt, limit=5)            -> list[Match]
    classify(prompt)                 -> Result(path, primary, candidates, confidence)
    detect_path(prompt)              -> BROKEN | BUILD | OPERATE | QUESTION | NONE

`classify` never raises. Missing index → confidence "none", empty candidates.
"""
from __future__ import annotations

import functools
import json
import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

INDEX_FILE = Path.home() / ".claude" / "skill_index.json"

W_NAME, W_USE_WHEN, W_PROJECT, W_KEYWORD, W_DESC, W_BODY = 3.0, 2.5, 1.5, 1.5, 1.0, 0.3
W_NAME_MISS = 0.9        # dock for a distinctive name token the prompt did not say
HUB_BONUS = 1.25         # a project's first-listed skill wins ties inside that project
MIN_SCORE = 3.5          # winner floor (normalized by sqrt(query length))
MIN_MARGIN = 0.35        # winner must beat runner-up by this fraction; below → LLM stage

# Multi-word phrases the user types that a skill indexes as one token.
PHRASES: dict[str, tuple[str, ...]] = {
    "row level security": ("rls", "policy", "policies"),
    "kernel panic": ("panic", "restart"),
    "app store": ("appstore", "ios", "testflight"),
    "play store": ("android", "playstore"),
    "b roll": ("broll", "b-roll"),
    "cold email": ("outreach", "email"),
    "pull request": ("pr", "review"),
    "landing page": ("landing", "copy"),
    "edge function": ("edge", "function", "supabase"),
}
RARE_DF_RATIO = 0.14
VERY_RARE_DF_RATIO = 0.03

# Query-side expansion. Small and deliberate: every entry here is a word the
# user actually types that a skill's vocabulary might not contain verbatim.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "restarted": ("restart", "reboot", "panic", "crash"),
    "restarts": ("restart", "reboot", "panic", "crash"),
    "reboot": ("restart", "panic"),
    "crashes": ("crash", "error", "exception"),
    "crashed": ("crash", "error"),
    "fails": ("failing", "failed", "error", "broken"),
    "failing": ("failed", "error", "broken"),
    "sleeping": ("sleep", "wake", "power"),
    "post": ("linkedin", "article", "content"),
    "posts": ("linkedin", "article", "content"),
    "short": ("shorts", "video", "youtube"),
    "shorts": ("short", "video", "youtube"),
    "video": ("youtube", "clip"),
    "ship": ("deploy", "release", "publish"),
    "shipping": ("deploy", "release"),
    "release": ("ship", "deploy", "build"),
    "deal": ("deals", "price", "discount"),
    "deals": ("deal", "price", "discount"),
    "resume": ("cv", "job", "application"),
    "cv": ("resume", "job"),
    "outreach": ("email", "lead", "prospect"),
    "prospects": ("lead", "outreach"),
    "dentists": ("dentist", "lead", "outreach"),
    "competitors": ("competitor", "competitive", "research"),
    "competitor": ("competitors", "competitive"),
    "b-roll": ("broll", "clip", "video"),
    "broll": ("b-roll", "clip", "video"),
    "kling": ("video", "generation"),
    "design": ("ui", "visual", "layout"),
    "redesign": ("design", "ui", "visual"),
    "compact": ("dense", "spacing", "layout", "ui"),
    "mac": ("macos", "macbook"),
    "macbook": ("mac", "macos"),
    "mini": ("mac",),
    "testflight": ("ios", "app", "store"),
    "fastlane": ("ios", "build", "app"),
    "rls": ("policy", "security", "supabase"),
    "bug": ("error", "broken", "debug"),
    "slow": ("performance", "perf"),
    "thumbnail": ("youtube", "image"),
}

STOPWORDS = frozenset("""
a an the and or but if then else for to of in on at by with from into onto over under
is are was were be been being do does did doing have has had having can could should
would will shall may might must this that these those it its you your we our us me my
i he she they them their there here what which who why how when where all any both
each few more most other some such no nor not only own same so than too very just now
also about above after again against below between during before further once out off
up down one two three get got make made take please help need want like use used using
useful uses way ways thing things stuff skill skills agent agents claude code file files
run running runs add adds added adding fix fixes fixed fixing update updates updated
updating remove removes removed change changes changed changing new let lets check
again last night tonight today yesterday next can you without within more less some
something anything everything nothing still already really quite pretty much many
level levels row rows
""".split())
_WORD_RE = re.compile(r"[a-z0-9@][a-z0-9+#.@-]*")

# Words that are rare *in the index* but common in developer English. Alone
# they are never evidence for a skill: "flow" must not pick ux-flow for a
# ReferenceError, "launch" must not pick debugging-capacitor for any crash.
GENERIC = frozenset("""
flow flows app apps launch screen screens endpoint endpoints component components
dashboard design designs review reviews page pages function functions email emails
file files folder folders code api apis service services module modules user users
data model models pipeline pipelines image images process processes branch branches
feature features deploy ship coverage auth login test tests testing build builds
write writing create new clean cleanup tidy helper helpers dead legacy state machine
order orders payment payments checkout notification notifications sms bank linking
docs documentation article articles content post posts generate generation plan
plans project projects task tasks work workflow workflows tool tools setup config
settings integration integrations connect analytics invoice invoices ios android
mobile web site website server client database table tables schema query queries
""".split())


def phrase_tokens(text: str) -> list[str]:
    low = re.sub(r"[-_]", " ", text.lower())
    out: list[str] = []
    for phrase, syns in PHRASES.items():
        if phrase in low:
            out.extend(s for s in syns if s not in out)
    return out


def tokenize(text: str) -> list[str]:
    out: list[str] = []
    for raw in _WORD_RE.findall(text.lower()):
        raw = raw.strip(".")
        parts = [raw]
        if "-" in raw or "_" in raw:
            parts += raw.replace("_", "-").split("-")
        if raw.startswith("@"):
            parts.append(raw[1:])
        for p in parts:
            if len(p) < 3 or p in STOPWORDS or p.isdigit():
                continue
            out.append(p)
    return out


def expand(tokens: Sequence[str], text: str = "") -> list[str]:
    """Query tokens + synonyms. Phrase synonyms are exact ("row level
    security" *is* rls) so they are appended to `tokens` at full weight by
    the caller through `phrase_tokens`; word synonyms get half credit."""
    seen = list(dict.fromkeys(tokens))
    extra: list[str] = []
    for s in phrase_tokens(text):
        if s not in seen and s not in extra:
            extra.append(s)
    for t in seen:
        for s in SYNONYMS.get(t, ()):
            if s not in seen and s not in extra:
                extra.append(s)
    return seen + extra


# ---- index ------------------------------------------------------------------

@dataclass(frozen=True)
class _Doc:
    name: str
    kind: str
    owner: str
    description: str
    use_when: tuple[str, ...]
    projects: tuple[str, ...]
    gates: tuple[str, ...]
    memory: tuple[str, ...]
    hub: bool
    name_tokens: frozenset[str]
    use_when_tokens: frozenset[str]
    project_tokens: frozenset[str]
    keyword_tokens: frozenset[str]
    desc_tokens: frozenset[str]


@dataclass(frozen=True)
class _Index:
    docs: tuple[_Doc, ...]
    idf: dict[str, float]
    df: dict[str, int]
    rare_max: int
    very_rare_max: int
    # project alias token -> project name, from the personal `projects:` block
    alias_project: dict[str, str] = field(default_factory=dict)
    # project name -> its alias tokens
    project_aliases: dict[str, frozenset[str]] = field(default_factory=dict)

    def is_rare(self, t: str) -> bool:
        return self.df.get(t, 0) <= self.rare_max

    def is_very_rare(self, t: str) -> bool:
        return self.df.get(t, 0) <= self.very_rare_max


@functools.lru_cache(maxsize=2)
def load_index(path: Optional[str] = None) -> Optional[_Index]:
    p = Path(path) if path else INDEX_FILE
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    entries = raw.get("entries") if isinstance(raw, dict) else None
    if not entries:
        return None
    docs: list[_Doc] = []
    for e in entries:
        name = (e.get("name") or "").strip()
        if not name:
            continue
        use_when = tuple(e.get("use_when") or ())
        projects = tuple(e.get("projects") or ())
        docs.append(_Doc(
            name=name, kind=e.get("kind") or "domain", owner=e.get("owner") or "user",
            description=e.get("description") or "",
            use_when=use_when, projects=projects,
            gates=tuple(e.get("gates") or ()), memory=tuple(e.get("memory") or ()),
            hub=bool(e.get("hub")),
            name_tokens=frozenset(tokenize(name)),
            use_when_tokens=frozenset(tokenize(" ".join(use_when))),
            project_tokens=frozenset(tokenize(" ".join(projects))),
            keyword_tokens=frozenset(tokenize(" ".join(e.get("keywords") or ()))),
            desc_tokens=frozenset(tokenize(e.get("description") or "")),
        ))
    if not docs:
        return None
    df: dict[str, int] = {}
    for d in docs:
        for t in (d.name_tokens | d.use_when_tokens | d.project_tokens | d.keyword_tokens | d.desc_tokens):
            df[t] = df.get(t, 0) + 1
    n = len(docs)
    idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}
    alias_project: dict[str, str] = {}
    project_aliases: dict[str, frozenset[str]] = {}
    for pname, p in (raw.get("projects") or {}).items():
        toks = frozenset(tokenize(" ".join(p.get("aliases") or [pname])))
        project_aliases[pname] = toks
        for t in toks:
            alias_project.setdefault(t, pname)
    return _Index(tuple(docs), idf, df,
                  rare_max=max(1, math.ceil(RARE_DF_RATIO * n)),
                  very_rare_max=max(1, math.ceil(VERY_RARE_DF_RATIO * n)),
                  alias_project=alias_project, project_aliases=project_aliases)


def prompt_projects(tokens: Sequence[str], idx: _Index) -> set[str]:
    """Projects the prompt names, via their alias tokens. Generic aliases
    ('post', 'deal') only count when a distinctive alias also matched."""
    named = {idx.alias_project[t] for t in tokens if t in idx.alias_project}
    strong = {idx.alias_project[t] for t in tokens
              if t in idx.alias_project and idx.is_very_rare(t)}
    return strong or named


# ---- scoring ----------------------------------------------------------------

@dataclass(frozen=True)
class Match:
    name: str
    score: float
    kind: str
    owner: str
    description: str
    hits: tuple[str, ...]
    rare_hits: int
    very_rare_hits: int
    project_hit: bool
    gates: tuple[str, ...] = ()
    memory: tuple[str, ...] = ()
    strong_hits: int = 0      # rare tokens matched in name / use_when / keywords


def _score(doc: _Doc, q: Sequence[str], original: set[str], idx: _Index) -> Match:
    total = 0.0
    hits: list[str] = []
    rare = very_rare = strong = 0
    project_hit = False
    project_bonus = 0.0
    for t in q:
        w = 0.0
        strong_field = False
        if t in doc.name_tokens:
            w = W_NAME
            strong_field = True
        elif t in doc.use_when_tokens:
            w = W_USE_WHEN
            strong_field = True
        elif t in doc.keyword_tokens:
            w = W_KEYWORD
            strong_field = True
        elif t in doc.desc_tokens:
            w = W_DESC
        # A project alias is a flat, once-per-skill bonus, not a per-token
        # field: otherwise every skill of a project ties on the alias list
        # and the skill's own vocabulary never gets to decide.
        if t in doc.project_tokens and t in original:
            project_hit = True
            project_bonus = max(project_bonus, W_PROJECT * idx.idf.get(t, math.log(2)))
        if w == 0.0:
            continue
        if t not in original:          # synonym-expanded token: half credit
            w *= 0.5
        total += w * idx.idf.get(t, math.log(2))
        hits.append(t)
        if idx.is_rare(t):
            rare += 1
            if strong_field and t in original and t not in GENERIC:
                strong += 1
        if idx.is_very_rare(t):
            very_rare += 1
    total += project_bonus
    # Partly-claimed name: 'scrollbook' matched but 'deploy' did not, so
    # `scrollbook-deploy` is probably the wrong scrollbook skill. Dock it by
    # the most distinctive name token the prompt never said.
    if doc.name_tokens & set(q):
        missed = [idx.idf.get(t, 0.0) for t in doc.name_tokens
                  if t not in q and idx.is_rare(t) and t not in doc.project_tokens]
        if missed:
            total -= W_NAME_MISS * max(missed)
    if project_hit and doc.hub:
        total *= HUB_BONUS
    # Process skills are the second leg of a route, not the answer to "which of
    # my skills fits this". Keep them rankable but never let them win on
    # generic vocabulary.
    if doc.kind in ("process", "meta"):
        total *= 0.6
    return Match(doc.name, total, doc.kind, doc.owner, doc.description, tuple(hits),
                 rare, very_rare, project_hit, doc.gates, doc.memory, strong)


def rank(prompt: str, limit: int = 5, index_path: Optional[str] = None,
         kinds: Optional[Sequence[str]] = None) -> list[Match]:
    idx = load_index(index_path)
    if idx is None:
        return []
    base = list(dict.fromkeys(tokenize(prompt)))
    if not base:
        return []
    q = expand(base, prompt)
    full_credit = set(base) | set(phrase_tokens(prompt))
    norm = math.sqrt(len(base))
    named = prompt_projects(base, idx)
    out: list[Match] = []
    for d in idx.docs:
        if kinds and d.kind not in kinds:
            continue
        m = _score(d, q, full_credit, idx)
        if m.score <= 0 or (m.rare_hits < 1 and not m.project_hit):
            continue
        score = m.score
        # Project conflict: the prompt names project X, this skill belongs to
        # project Y only. "push deenunlock to testflight" must not land on
        # scrollbook-deploy just because both ship to TestFlight.
        if named and d.project_tokens:
            mine = {idx.alias_project[t] for t in d.project_tokens if t in idx.alias_project}
            if mine and not (mine & named):
                score *= 0.35
        out.append(Match(m.name, round(score / norm, 3), m.kind, m.owner, m.description,
                         m.hits, m.rare_hits, m.very_rare_hits, m.project_hit, m.gates, m.memory,
                         m.strong_hits))
    out.sort(key=lambda m: (-m.score, m.name))
    return out[:limit]


# ---- path ---------------------------------------------------------------------

_BROKEN = re.compile(
    r"\b(error|errors|crash\w*|exception|traceback|panic\w*|fail\w*|broken|bug|regress\w*|"
    r"doesn'?t work|not working|won'?t (start|build|load|open)|restart\w*|reboot\w*|"
    r"keeps? (sleeping|crashing|restarting|failing)|typeerror|referenceerror|"
    r"5\d\d\b|down\b|wrong|hang\w*|freez\w*|stuck)\b", re.IGNORECASE)
_QUESTION = re.compile(
    r"^\s*(what|why|how|when|where|which|who|is|are|does|do|did|can|could|should|would|will)\b.*\?\s*$"
    r"|^\s*(what does|what is|how does|how do i|explain|tell me|show me|is there|do you think|"
    r"what do you think|should (i|we)|would you|any (thoughts|ideas|concerns))\b"
    r"|\b(walk me through|talk me through|tell me about|explain (?:to me )?(?:how|why|what)|"
    r"what do you think|your (?:take|opinion|view)|do you (?:agree|think|see))\b", re.IGNORECASE)
_BUILD = re.compile(
    r"\b(build|create|add|implement|integrate|scaffold|set ?up|generate|write|draft|design|"
    r"make|new|launch|tailor|produce|compose|turn .{0,40} into)\b", re.IGNORECASE)
_OPERATE = re.compile(
    r"\b(refactor|clean ?up|tidy|simplify|deploy|ship|release|publish|review|audit|merge|"
    r"push|migrate|upgrade|update|optimi[sz]e|research|analy[sz]e|find|check|diagnose|"
    r"investigate|compare|tune|configure|automate|schedule|archive)\b", re.IGNORECASE)
_OPERATE_LEAD = re.compile(
    r"^\s*(?:please\s+|can you\s+|could you\s+|let'?s\s+)?(review|audit|refactor|deploy|ship|"
    r"release|publish|research|diagnose|investigate|compare|migrate|upgrade|optimi[sz]e|"
    r"analy[sz]e|tune|clean ?up|tidy|simplify)\b", re.IGNORECASE)
_NOISE = re.compile(
    r"^\s*(\[skill-router\]|PreToolUse:|PostToolUse:|Stop hook|<task-notification>|"
    r"This session is being continued|The user (sent|ran|just)|Hook (blocking|denied))",
    re.IGNORECASE)


_IMPERATIVE = re.compile(
    r"^\s*(?:please\s+|can you\s+|could you\s+|would you\s+|let'?s\s+|help me\s+|"
    r"i need (?:you )?to\s+|i want (?:you )?to\s+|go ahead and\s+|now\s+|next[,:]?\s+)?"
    r"(?:(?:just|also|quickly|first|then|maybe|kindly|ok|okay)\s+)?"
    r"(build|create|add|implement|integrate|scaffold|set ?up|generate|write|draft|design|"
    r"make|launch|tailor|produce|compose|turn|refactor|clean|tidy|simplify|deploy|ship|"
    r"release|publish|review|audit|merge|push|migrate|upgrade|update|optimi[sz]e|research|"
    r"analy[sz]e|find|check|diagnose|investigate|compare|tune|configure|automate|schedule|"
    r"archive|fix|debug|run|test|convert|move|rename|extract|split|wire|hook|connect|"
    r"prepare|record|post|send|open|cut|bump|redesign|polish|improve|rework|rewrite)\b",
    re.IGNORECASE)
_REQUEST_MARKERS = re.compile(
    r"\b(please|can you|could you|would you|let'?s|help me|i need|i want|go ahead|"
    r"make sure|we need|we should|you should|i'?d like)\b", re.IGNORECASE)


def is_request(text: str) -> bool:
    """A request for work, as opposed to a statement or a musing.
    'tidy up the helpers folder' → yes. 'the recent refactor broke the auth
    flow' → no (BROKEN still catches it through its own signal words)."""
    return bool(_IMPERATIVE.match(text) or _REQUEST_MARKERS.search(text))


def detect_path(prompt: str) -> str:
    text = prompt.strip()
    if not text or _NOISE.search(text):
        return "NONE"
    if _QUESTION.search(text) and not _BROKEN.search(text):
        return "QUESTION"
    if _BROKEN.search(text):
        return "BROKEN"
    if not is_request(text):
        return "NONE"
    # "review the design", "audit the landing page": the operation verb is
    # the intent; the noun that happens to be a BUILD word is its object.
    if _OPERATE_LEAD.match(text):
        return "OPERATE"
    if _BUILD.search(text):
        return "BUILD"
    if _OPERATE.search(text):
        return "OPERATE"
    if len(tokenize(text)) < 2:
        return "NONE"
    return "OPERATE"


# ---- classify --------------------------------------------------------------------

@dataclass
class Result:
    path: str
    primary: Optional[Match]
    candidates: list[Match] = field(default_factory=list)
    confidence: str = "none"        # high | low | none
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "primary": self.primary.name if self.primary else None,
            "confidence": self.confidence,
            "candidates": [(m.name, m.score) for m in self.candidates],
            "reason": self.reason,
        }


def classify(prompt: str, index_path: Optional[str] = None) -> Result:
    path = detect_path(prompt)
    cands = rank(prompt, limit=5, index_path=index_path)
    domain = [m for m in cands if m.kind not in ("process", "meta")]
    if not domain:
        return Result(path, None, cands, "none", "no domain candidate")
    top = domain[0]
    runner = domain[1].score if len(domain) > 1 else 0.0
    margin_ok = runner == 0.0 or (top.score - runner) / max(top.score, 1e-9) >= MIN_MARGIN
    # Evidence, not vocabulary overlap: a project name, or a distinctive token
    # the skill claims in its *name, triggers or keywords* — a rare word that
    # merely appears in a description is how `ux-flow` won a ReferenceError
    # prompt on the word "flow". Two common words ('page', 'without') are how
    # wseller "won" a settings-page prompt.
    strong = top.project_hit or (top.strong_hits >= 1 and (top.very_rare_hits >= 1 or top.rare_hits >= 2))
    # A specific, non-generic token in a strong field is what makes a match
    # *about* this skill. Without one there is nothing for the model stage to
    # confirm either — hand the prompt to the process table.
    if not top.project_hit and top.strong_hits == 0:
        return Result(path, None, cands, "none", "no distinctive evidence")
    if top.score >= MIN_SCORE and margin_ok and strong:
        return Result(path, top, cands, "high", "lexical")
    if top.score >= MIN_SCORE * 0.6 and strong:
        return Result(path, top, cands, "low", "weak margin or score")
    return Result(path, None, cands, "none", "below floor")


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", nargs="+")
    ap.add_argument("--index", default=None)
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    prompt = " ".join(a.prompt)
    r = classify(prompt, index_path=a.index)
    print(json.dumps(r.as_dict(), indent=1))
    if a.all:
        for m in rank(prompt, limit=10, index_path=a.index):
            print(f"{m.score:6.2f} {m.kind:8} {m.name:36} hits={list(m.hits)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
