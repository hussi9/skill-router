#!/usr/bin/env python3
"""
catalog_match.py — rank installed skills against a prompt, lexically.

The routing table hard-codes ~20 process skills (debug, plan, review, deploy).
This machine has ~380 invokable ones, and the other ~360 are exactly the
domain specialists that make a task go faster: `youtube-manager` for a video,
`seo-technical` for a crawl audit, `scrollbook-deploy` for a TestFlight push.
The table can never enumerate them — new skills land weekly — so the router
ranks them at query time instead.

Why lexical and not embeddings: the embedder daemon (embedder_daemon.py) needs
fastembed + ONNX + an 80-second corpus build + a live Unix socket, and it was
dead on this machine for months, taking the whole semantic layer with it. BM25
over 380 short documents is ~5ms of pure stdlib with no daemon, no model
download, and no failure mode beyond a missing catalog file. The embedder
stays an optional boost, not a dependency.

Precision is the product. A wrong specialist is worse than no specialist,
because the user stops trusting the line and starts ignoring the whole
announcement. Three gates enforce that:

  1. MIN_SCORE      — absolute floor; weak keyword overlap is silence.
  2. MIN_MARGIN     — the winner must beat the runner-up, so a prompt that
                      matches nine SEO skills equally well returns nothing
                      (the user meant the hub skill, or nothing at all).
  3. MIN_RARE_HITS  — at least one *distinctive* token must match. Without
                      this, "review the page" scores on 'review' and 'page'
                      alone and every UI skill is a coin flip.

Public API:
    rank(prompt, limit=5, invokable_only=True) -> list[Match]
    best(prompt, exclude=()) -> Match | None

CLI:
    python3 scripts/catalog_match.py "add stripe webhooks to checkout"
    python3 scripts/catalog_match.py --all "ship the youtube video"
"""
from __future__ import annotations

import argparse
import functools
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Sequence

CATALOG_FILE = Path.home() / ".claude" / "skill_router_catalog.json"

# --- Tuning ------------------------------------------------------------------
# Calibrated against tests/test_catalog_match.py. Raise MIN_SCORE for fewer,
# surer matches; lower MIN_MARGIN to allow near-ties through.
MIN_SCORE = 3.2      # absolute floor on the winner's normalized score
MIN_MARGIN = 0.18    # winner must exceed runner-up by this fraction of its score
MIN_RARE_HITS = 1    # how many distinctive tokens the winner must match

# Rarity is a share of the corpus, resolved to a document count at index time,
# with a floor of 1. Two earlier formulations both broke on small corpora: an
# absolute IDF cutoff is unreachable when N is small (no token can score 2.0 at
# N=5), and a bare ratio excludes the singleton case (1 of 4 documents is 25%,
# yet a token in exactly one document is as distinctive as a token can be).
# Taking max(1, ceil(ratio * N)) keeps the intended meaning at N=380 and stays
# correct all the way down to a handful of fixtures.
RARE_DF_RATIO = 0.14        # distinctive: in at most 14% of skills
VERY_RARE_DF_RATIO = 0.02   # a project name: in at most 2% of skills

# Field weights. A token in the skill's *name* is the strongest possible
# signal ("youtube" in `youtube-manager`); body text is weak because the
# excerpt is only the first ~600 chars of prose.
W_NAME = 3.0
W_DESC = 1.0
W_BODY = 0.35

# Penalty for a distinctive term in the skill's own name that the prompt never
# mentions. This is what separates `ios-fix` from a generic "fix the failing
# test": both match 'fix', but `ios-fix` is *about* iOS and the prompt says
# nothing about iOS, so its defining term went unclaimed. Only the single most
# distinctive missing term is charged, so a long descriptive name isn't
# punished repeatedly for words the prompt had no reason to use.
W_NAME_MISS = 0.9

# Words that carry no routing signal. Beyond ordinary English stopwords this
# includes the verbs every software prompt contains ("add", "fix", "update")
# and the nouns every skill description contains ("skill", "use", "when") —
# both would otherwise dominate the score with near-zero IDF.
STOPWORDS = frozenset("""
a an the and or but if then else for to of in on at by with from into onto over under
is are was were be been being do does did doing have has had having can could should
would will shall may might must this that these those it its it's you your we our us
me my i he she they them their there here what which who whom whose why how when where
all any both each few more most other some such no nor not only own same so than too
very just now also about above after again against below between during before further
once out off up down again very s t don now new one two three get got make made take
please help need want like use used using useful uses way ways thing things stuff
skill skills agent agents claude code file files run running runs
add adds added adding fix fixes fixed fixing update updates updated updating
remove removes removed change changes changed changing
""".split())

# Tokens that name a project or product but exist in no skill description are
# useless to score and expensive to keep; the IDF handles that automatically,
# so no explicit list is needed here.

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9+#.-]*")


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, stopwords and 1-2 char noise removed.

    Hyphenated skill names are also split, so a prompt saying "youtube" hits
    `youtube-manager` and a prompt saying "test driven development" hits
    `test-driven-development`.
    """
    out: list[str] = []
    for raw in _WORD_RE.findall(text.lower()):
        for part in ([raw] + raw.replace("_", "-").split("-") if "-" in raw or "_" in raw else [raw]):
            if len(part) < 3 or part in STOPWORDS:
                continue
            out.append(part)
    return out


@dataclass(frozen=True)
class Match:
    name: str
    score: float
    description: str
    type: str
    invokable: bool
    hits: tuple[str, ...]
    very_rare_hits: int = 0

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Match {self.name} {self.score:.2f} {list(self.hits)}>"


@dataclass(frozen=True)
class _Doc:
    name: str
    description: str
    type: str
    invokable: bool
    name_tokens: frozenset[str]
    desc_tokens: frozenset[str]
    body_tokens: frozenset[str]


@dataclass(frozen=True)
class _Index:
    docs: tuple[_Doc, ...]
    idf: dict[str, float]
    df: dict[str, int]
    rare_max: int
    very_rare_max: int

    def is_rare(self, token: str) -> bool:
        return self.df.get(token, 0) <= self.rare_max

    def is_very_rare(self, token: str) -> bool:
        return self.df.get(token, 0) <= self.very_rare_max


@functools.lru_cache(maxsize=1)
def load_index(path: Optional[str] = None) -> Optional[_Index]:
    """Build the inverted-document-frequency index from the catalog on disk.

    Returns None when the catalog is missing or malformed — every caller
    treats that as "no specialist layer", never as an error, so a stale or
    absent catalog degrades the router to its table-only behavior instead of
    breaking the turn.
    """
    p = Path(path) if path else CATALOG_FILE
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return None
    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        return None

    docs: list[_Doc] = []
    for e in entries:
        name = (e.get("name") or "").strip()
        if not name:
            continue
        desc = (e.get("description") or "").strip()
        docs.append(_Doc(
            name=name,
            description=desc,
            type=e.get("type") or "skill",
            invokable=bool(e.get("invokable", True)),
            name_tokens=frozenset(tokenize(name)),
            desc_tokens=frozenset(tokenize(desc)),
            body_tokens=frozenset(tokenize(e.get("body") or "")),
        ))
    if not docs:
        return None

    # IDF is computed over the *invokable* population only. Including the ~870
    # install-candidate skills would flatten the IDF of exactly the terms that
    # distinguish installed specialists from each other.
    corpus = [d for d in docs if d.invokable] or docs
    n = len(corpus)
    df: dict[str, int] = {}
    for d in corpus:
        for tok in (d.name_tokens | d.desc_tokens | d.body_tokens):
            df[tok] = df.get(tok, 0) + 1
    idf = {tok: math.log((n + 1) / (c + 0.5)) for tok, c in df.items()}
    return _Index(
        docs=tuple(docs),
        idf=idf,
        df=df,
        rare_max=max(1, math.ceil(RARE_DF_RATIO * n)),
        very_rare_max=max(1, math.ceil(VERY_RARE_DF_RATIO * n)),
    )


def _score(doc: _Doc, q: Sequence[str],
           index: _Index) -> tuple[float, list[str], int, int]:
    """Weighted IDF overlap between a query and one document.

    Returns (raw_score, matched_tokens, rare_hits, very_rare_hits). A token is
    counted once at its best-weighted field so a word appearing in both the
    name and the description doesn't double-score. The score is then docked
    for the most distinctive unmatched term in the doc's own name — see
    W_NAME_MISS.
    """
    idf = index.idf
    total = 0.0
    hits: list[str] = []
    rare = 0
    very_rare = 0
    qset = set(q)
    for tok in q:
        weight = 0.0
        if tok in doc.name_tokens:
            weight = W_NAME
        elif tok in doc.desc_tokens:
            weight = W_DESC
        elif tok in doc.body_tokens:
            weight = W_BODY
        if weight == 0.0:
            continue
        token_idf = idf.get(tok, math.log(2))
        total += weight * token_idf
        hits.append(tok)
        if index.is_rare(tok):
            rare += 1
        if index.is_very_rare(tok):
            very_rare += 1

    # The penalty only applies when the name was *partly* claimed. Missing half
    # a compound name is the signal ('fix' matched, 'ios' did not, so `ios-fix`
    # is probably the wrong skill). Missing all of it is not: `dataviz` matched
    # on 'chart' and 'visualization' from its description and never claimed its
    # name at all, which is an ordinary description-driven match. Charging both
    # cases alike is what made every single-word built-in unrankable.
    claimed_name = bool(doc.name_tokens & qset)
    if claimed_name:
        missed = [idf.get(t, 0.0) for t in doc.name_tokens
                  if t not in qset and index.is_rare(t)]
        if missed:
            total -= W_NAME_MISS * max(missed)
    return total, hits, rare, very_rare


def rank(prompt: str, limit: int = 5, invokable_only: bool = True,
         catalog_path: Optional[str] = None) -> list[Match]:
    """Return the best-matching skills for `prompt`, highest score first.

    Scores are normalized by sqrt(len(query)) so a long prompt doesn't
    out-score a short one purely by having more tokens to match.
    """
    index = load_index(catalog_path)
    if index is None:
        return []
    q = list(dict.fromkeys(tokenize(prompt)))  # dedupe, keep order
    if not q:
        return []
    norm = math.sqrt(len(q))

    scored: list[tuple[float, _Doc, list[str], int]] = []
    for doc in index.docs:
        if invokable_only and not doc.invokable:
            continue
        raw, hits, rare, very_rare = _score(doc, q, index)
        if raw <= 0 or rare < MIN_RARE_HITS:
            continue
        scored.append((raw / norm, doc, hits, very_rare))

    scored.sort(key=lambda t: (-t[0], t[1].name))
    return [
        Match(name=d.name, score=round(s, 3), description=d.description,
              type=d.type, invokable=d.invokable, hits=tuple(h),
              very_rare_hits=vr)
        for s, d, h, vr in scored[:limit]
    ]


def _same_family(a: str, b: str) -> bool:
    """True when two skill names sit in the same domain family.

    `seo-technical` and `cory-seo-audit` both answer an SEO question; a tie
    between them is not the kind of ambiguity worth staying silent over,
    because either lands the user in the right neighborhood. A tie between
    `market-launch` and `debugging-capacitor` is. Family is decided by a
    shared distinctive name token, ignoring author prefixes that carry no
    domain meaning.
    """
    prefixes = {"cory", "claude", "plugin", "superpowers", "vercel", "anthropic"}
    ta = set(tokenize(a)) - prefixes
    tb = set(tokenize(b)) - prefixes
    return bool(ta & tb)


def best(prompt: str, exclude: Iterable[str] = (),
         catalog_path: Optional[str] = None) -> Optional[Match]:
    """Return the single confident specialist for `prompt`, or None.

    None is the common and correct answer. Three conditions must all hold, and
    they exist so the router stays silent rather than guessing — an unhelpful
    specialist line trains the user to skim past the whole announcement.

      floor     the winner clears MIN_SCORE
      strength  it matched two distinct terms, or one term distinctive enough
                to be a project name ('scrollbook', 'jobhunt', 'testflight').
                A lone common word like 'launch' is not evidence.
      decisive  it beat the runner-up by MIN_MARGIN, or the runner-up is a
                sibling in the same family (see _same_family)
    """
    excluded = {e.lower() for e in exclude}
    results = [m for m in rank(prompt, limit=6, catalog_path=catalog_path)
               if m.name.lower() not in excluded]
    if not results:
        return None
    top = results[0]

    if top.score < MIN_SCORE:
        return None
    if len(top.hits) < 2 and top.very_rare_hits < 1:
        return None
    if len(results) > 1:
        runner_up = results[1]
        margin = (top.score - runner_up.score) / top.score if top.score > 0 else 0.0
        if margin < MIN_MARGIN and not _same_family(top.name, runner_up.name):
            return None
    return top


def suggest_install(prompt: str, catalog_path: Optional[str] = None) -> Optional[Match]:
    """Best *uninstalled* skill for the prompt, when no installed one fits.

    These live in ~/.agent/skills and ~/.composio-skills: present on disk but
    invisible to the Skill tool. Announcing one as a route would deadlock the
    IRON RULE on a name Claude cannot invoke, so this is strictly a
    'you could install this' hint held to a higher bar than `best()`.
    """
    index = load_index(catalog_path)
    if index is None:
        return None
    q = list(dict.fromkeys(tokenize(prompt)))
    if not q:
        return None
    norm = math.sqrt(len(q))
    scored: list[tuple[float, _Doc, list[str]]] = []
    for doc in index.docs:
        if doc.invokable:
            continue
        raw, hits, rare, _vr = _score(doc, q, index)
        if raw <= 0 or rare < MIN_RARE_HITS + 1 or len(hits) < 2:
            continue
        scored.append((raw / norm, doc, hits))
    if not scored:
        return None
    scored.sort(key=lambda t: (-t[0], t[1].name))
    s, d, h = scored[0]
    if s < MIN_SCORE * 1.5:
        return None
    return Match(name=d.name, score=round(s, 3), description=d.description,
                 type=d.type, invokable=False, hits=tuple(h))


def main() -> int:
    ap = argparse.ArgumentParser(description="Rank installed skills against a prompt.")
    ap.add_argument("prompt", nargs="*", help="prompt text (or read stdin)")
    ap.add_argument("--all", action="store_true", help="include non-invokable skills")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    text = " ".join(args.prompt) if args.prompt else sys.stdin.read()
    text = text.strip()
    if not text:
        print("no prompt", file=sys.stderr)
        return 2

    results = rank(text, limit=args.limit, invokable_only=not args.all)
    chosen = best(text)

    if args.json:
        print(json.dumps({
            "best": chosen.name if chosen else None,
            "matches": [m.__dict__ for m in results],
        }, indent=1, default=list))
        return 0

    if not results:
        print("(no match)")
        return 0
    width = max(len(m.name) for m in results)
    for m in results:
        mark = "*" if chosen and m.name == chosen.name else " "
        flag = "" if m.invokable else "  [not installed]"
        print(f"{mark} {m.name:<{width}}  {m.score:6.2f}  {','.join(m.hits[:6])}{flag}")
    if not chosen:
        print("\n(no confident winner — router stays silent)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
