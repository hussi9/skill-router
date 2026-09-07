#!/usr/bin/env python3
"""
learn.py — turn routing history into a personal overlay the router consults.

The routing table and the catalog matcher are generic: they know what kind of
work a prompt is and which installed skill is about that domain. They do not
know how *you* work — which skills you actually reach for on which prompts,
what you run after what, which sequences repeat, which announcements you
ignore. That is learnable from two logs this machine already writes, and this
script learns it.

Everything learned lands in ONE file outside the repo:

    ~/.claude/skill_router_learned.json

The engine is versioned and shared; the overlay is personal and regenerated.
Nothing here edits SKILL.md, SKILL.personal.md, or any file under version
control. Earlier tooling appended learned chains into SKILL.personal.md, which
made the personal file half hand-written and half machine-written and meant
every relearn was a merge. This replaces that: hand-curated routes stay in the
personal file, learned suggestions live here, and either can be regenerated or
edited without touching the other.

What gets learned (all with support and precision thresholds — a pattern seen
twice is a coincidence):

  per_skill     announcement → invocation follow rate, per skill. Read by the
                router to refuse rescues for skills you routinely ignore.
  triggers      prompt keyword → skill associations, from prompts where you
                invoked a skill. The router consults these only when the table
                and the catalog matcher are both silent, as an advisory line.
  handovers     what you run after what, within a session. Powers the nudge
                after a Skill call ("you usually follow X with Y").
  chains        sequences of 2–4 skills that recur. Shown at announcement time
                so the whole flow is visible up front, not one step at a time.
  discovered    skills that appeared or vanished since the last run, so a new
                install is announced at the next session start.
  online        uninstalled skills from the online catalog that fit your recent
                prompts, ranked against your own keyword profile rather than
                offered as a 2,400-item list.

Inputs (read-only):
  ~/.claude/skill_router_log.jsonl      prompt / chain-start / invoke events
  ~/.claude/skill_usage.log             legacy invocations (no session ids)
  ~/.claude/skill_router_catalog.json   what is installed now
  ~/.claude/skill_router_online_catalog.json

Privacy: the router logs prompt *keywords*, never prompt text. The overlay
contains keyword→skill statistics, skill names, and timestamps. Set
SKILL_ROUTER_NO_LEARN=1 to stop the hooks writing prompt events at all.

Usage:
    python3 scripts/learn.py                 # relearn, print a summary
    python3 scripts/learn.py --quiet         # relearn silently (SessionStart)
    python3 scripts/learn.py --show          # print the current overlay
    python3 scripts/learn.py --refresh-online   # also refetch the online catalog if stale
    python3 scripts/learn.py --compact       # also drop dead embedder dumps from the log
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

HOME = Path.home()
ROUTER_LOG = HOME / ".claude" / "skill_router_log.jsonl"
USAGE_LOG = HOME / ".claude" / "skill_usage.log"
CATALOG = HOME / ".claude" / "skill_router_catalog.json"
ONLINE_CATALOG = HOME / ".claude" / "skill_router_online_catalog.json"
LEARNED = HOME / ".claude" / "skill_router_learned.json"

# ---- Tuning ------------------------------------------------------------------

WINDOW_DAYS = 60          # how far back the statistics look
FOLLOW_WINDOW_SEC = 120   # legacy: an invoke this soon after an announce counts as following it
SESSION_GAP_MIN = 45      # legacy: a silence this long splits a session

# A learned association must be seen this many times AND be this reliable.
TRIGGER_MIN_SUPPORT = 3
TRIGGER_MIN_PRECISION = 0.6
HANDOVER_MIN_SUPPORT = 3
HANDOVER_MIN_PROB = 0.35
CHAIN_MIN_SUPPORT = 3
CHAIN_MAX_LEN = 4

ONLINE_STALE_DAYS = 7
ONLINE_TOP_N = 10
ONLINE_MIN_MATCHED = 3    # profile keywords an online skill must hit
ONLINE_MIN_PROMPTS = 20   # prompt events required before suggesting anything
DISCOVERY_RECENT_DAYS = 14

# Meta-skills never become a learning target: routing to the router is circular,
# and the two generic loaders say nothing about the task.
EXCLUDED_SKILLS = frozenset({"skill-router", "superpowers:using-superpowers"})

# Dead-daemon noise. These events carry 800-byte neighbour dumps from an
# embedder that has not run in months; they are 56% of the log by lines and
# ~90% by bytes and inform nothing.
COMPACTABLE_TYPES = frozenset({"embedding-skip", "embedding-route"})


# ---- Log reading -------------------------------------------------------------

def _ts(value: str) -> Optional[float]:
    """Parse the ISO-ish timestamps this tool writes; None if unparseable."""
    if not value:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return time.mktime(time.strptime(value[:19], fmt))
        except ValueError:
            continue
    return None


def read_router_log(since: float) -> list[dict]:
    if not ROUTER_LOG.is_file():
        return []
    out: list[dict] = []
    with ROUTER_LOG.open("r", errors="ignore") as f:
        for line in f:
            try:
                e = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if not isinstance(e, dict):
                continue
            t = _ts(e.get("ts", ""))
            if t is None or t < since:
                continue
            e["_t"] = t
            out.append(e)
    out.sort(key=lambda e: e["_t"])
    return out


def read_usage_log(since: float) -> list[dict]:
    """Legacy invocations: 'YYYY-MM-DD HH:MM:SS<TAB>skill'. No session ids."""
    if not USAGE_LOG.is_file():
        return []
    out: list[dict] = []
    with USAGE_LOG.open("r", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if "\t" in line:
                ts_str, _, skill = line.partition("\t")
            else:
                parts = line.rsplit(" ", 1)
                if len(parts) != 2:
                    continue
                ts_str, skill = parts
            t = _ts(ts_str.strip())
            skill = skill.strip()
            if t is None or t < since or not skill:
                continue
            out.append({"_t": t, "skill": skill})
    out.sort(key=lambda e: e["_t"])
    return out


# ---- Events → sessions -------------------------------------------------------

def invocations(events: list[dict], legacy: list[dict]) -> list[dict]:
    """All invocations in the window, preferring structured `invoke` events.

    The structured events carry session_id and prompt_id; the legacy usage log
    does not. For any minute covered by structured events, the legacy line is
    the same call recorded twice, so it is dropped. Legacy lines survive only
    for the period before structured logging existed.
    """
    structured = [e for e in events if e.get("type") == "invoke" and e.get("skill")]
    if not structured:
        return [dict(e, session_id=None, prompt_id=None) for e in legacy]
    first_structured = min(e["_t"] for e in structured)
    old_legacy = [dict(e, session_id=None, prompt_id=None)
                  for e in legacy if e["_t"] < first_structured]
    return sorted(old_legacy + structured, key=lambda e: e["_t"])


def sessionize(invs: list[dict]) -> list[list[dict]]:
    """Group invocations into sessions.

    With session ids this is exact. Without them (legacy), a gap longer than
    SESSION_GAP_MIN splits a session — the same heuristic the old history
    miner used, kept so the two eras of data compare.
    """
    by_id: dict[str, list[dict]] = defaultdict(list)
    legacy: list[dict] = []
    for inv in invs:
        if inv.get("session_id"):
            by_id[inv["session_id"]].append(inv)
        else:
            legacy.append(inv)
    sessions = list(by_id.values())
    current: list[dict] = []
    for inv in legacy:
        if current and inv["_t"] - current[-1]["_t"] > SESSION_GAP_MIN * 60:
            sessions.append(current)
            current = []
        current.append(inv)
    if current:
        sessions.append(current)
    return [sorted(s, key=lambda e: e["_t"]) for s in sessions]


def _dedupe_consecutive(skills: list[str]) -> list[str]:
    out: list[str] = []
    for s in skills:
        if s in EXCLUDED_SKILLS:
            continue
        if not out or out[-1] != s:
            out.append(s)
    return out


# ---- Learners ----------------------------------------------------------------

def learn_follow_rates(events: list[dict], invs: list[dict]) -> dict:
    """Per skill: how often an announcement was actually followed.

    Joins on (session_id, prompt_id) when both sides have them — that is
    exact. Falls back to the legacy time window otherwise. Each invocation
    satisfies at most one announcement.
    """
    announces = [e for e in events if e.get("type") == "chain-start" and e.get("steps")]
    stamped = [a for a in announces if a.get("session_id")]
    if stamped:
        # Same era rule as invocations(): once announcements carry session
        # ids, an unstamped one from after that point was written by a test,
        # a doctor smoke prompt or a manual probe — not by a turn you had —
        # and counting it as "announced, never followed" punishes the skill
        # for the suite's diligence.
        first_stamped = min(a["_t"] for a in stamped)
        announces = [a for a in announces if a.get("session_id") or a["_t"] < first_stamped]
    consumed = [False] * len(invs)
    per: dict[str, dict] = defaultdict(lambda: {
        "announcements": 0, "invocations": 0, "followed": 0,
        "last_invoked": None, "last_announced": None,
    })
    for inv in invs:
        s = inv["skill"]
        per[s]["invocations"] += 1
        per[s]["last_invoked"] = max(per[s]["last_invoked"] or 0, inv["_t"])

    for ann in announces:
        primary = ann["steps"][0]
        p = per[primary]
        p["announcements"] += 1
        p["last_announced"] = max(p["last_announced"] or 0, ann["_t"])
        match = None
        for i, inv in enumerate(invs):
            if consumed[i] or inv["skill"] != primary:
                continue
            if ann.get("session_id") and inv.get("session_id"):
                if inv["session_id"] == ann["session_id"] and (
                        inv.get("prompt_id") == ann.get("prompt_id")
                        or 0 <= inv["_t"] - ann["_t"] <= FOLLOW_WINDOW_SEC * 5):
                    match = i
                    break
            elif 0 <= inv["_t"] - ann["_t"] <= FOLLOW_WINDOW_SEC:
                match = i
                break
        if match is not None:
            consumed[match] = True
            p["followed"] += 1

    out: dict[str, dict] = {}
    for skill, p in per.items():
        if skill in EXCLUDED_SKILLS:
            continue
        rate = (p["followed"] / p["announcements"]) if p["announcements"] else None
        out[skill] = {
            "announcements": p["announcements"],
            "invocations": p["invocations"],
            "follow_rate": round(rate, 3) if rate is not None else None,
            "last_invoked": _iso(p["last_invoked"]),
            "last_announced": _iso(p["last_announced"]),
        }
    return out


def learn_triggers(events: list[dict], invs: list[dict]) -> list[dict]:
    """Prompt keyword → skill, from prompts where a skill was then invoked.

    A prompt event carries the keyword set the router extracted. An invoke
    event carries (session_id, prompt_id, skill). Joining them says: when the
    user wrote these words, they reached for this skill. Precision is the
    share of prompts containing the keyword on which that skill was invoked,
    so a keyword like "page" that precedes ten different skills never clears
    the bar, while "testflight" preceding scrollbook-deploy every time does.
    """
    prompts = {(e.get("session_id"), e.get("prompt_id")): e
               for e in events if e.get("type") == "prompt" and e.get("tokens")}
    if not prompts:
        return []
    token_total: Counter = Counter()
    token_skill: Counter = Counter()
    skills_by_prompt: dict[tuple, set[str]] = defaultdict(set)
    for inv in invs:
        key = (inv.get("session_id"), inv.get("prompt_id"))
        if key in prompts and inv["skill"] not in EXCLUDED_SKILLS:
            skills_by_prompt[key].add(inv["skill"])

    for key, e in prompts.items():
        tokens = set(e["tokens"])
        for tok in tokens:
            token_total[tok] += 1
        for skill in skills_by_prompt.get(key, ()):
            for tok in tokens:
                token_skill[(tok, skill)] += 1

    out: list[dict] = []
    for (tok, skill), n in token_skill.items():
        if n < TRIGGER_MIN_SUPPORT:
            continue
        precision = n / token_total[tok]
        if precision < TRIGGER_MIN_PRECISION:
            continue
        out.append({"token": tok, "skill": skill, "n": n, "precision": round(precision, 3)})
    out.sort(key=lambda t: (-t["n"] * t["precision"], t["token"]))
    return out


def learn_handovers(sessions: list[list[dict]]) -> dict[str, list[dict]]:
    """What follows what, within a session.

    For each ordered pair (a, b) of consecutive distinct skills in a session,
    count it and note the gap. The probability is P(next = b | current = a),
    so a skill that is followed by many different things yields no nudge —
    only a genuinely habitual next step does.
    """
    pair_n: Counter = Counter()
    pair_gaps: dict[tuple, list[float]] = defaultdict(list)
    from_n: Counter = Counter()
    for sess in sessions:
        seq = [(inv["skill"], inv["_t"]) for inv in sess if inv["skill"] not in EXCLUDED_SKILLS]
        prev: Optional[tuple] = None
        for skill, t in seq:
            if prev and prev[0] != skill:
                pair_n[(prev[0], skill)] += 1
                pair_gaps[(prev[0], skill)].append((t - prev[1]) / 60)
                from_n[prev[0]] += 1
            prev = (skill, t)

    out: dict[str, list[dict]] = defaultdict(list)
    for (a, b), n in pair_n.items():
        if n < HANDOVER_MIN_SUPPORT:
            continue
        p = n / from_n[a]
        if p < HANDOVER_MIN_PROB:
            continue
        out[a].append({
            "to": b, "n": n, "p": round(p, 3),
            "median_min": round(statistics.median(pair_gaps[(a, b)]), 1),
        })
    for a in out:
        out[a].sort(key=lambda h: (-h["p"], -h["n"]))
    return dict(out)


def learn_chains(sessions: list[list[dict]]) -> list[dict]:
    """Recurring sequences of 2..CHAIN_MAX_LEN distinct skills within a session.

    Counted as n-grams over the de-duplicated skill sequence, so
    plan → tdd → verify seen in five sessions is one chain with n=5, not
    three separate pairs. A longer chain is only kept if it recurs on its own
    merits — every 3-gram also contributes to its 2-gram prefixes, which is
    correct: the prefix really did happen every time the whole chain did.
    """
    n_by_chain: Counter = Counter()
    last_seen: dict[tuple, float] = {}
    for sess in sessions:
        seq = _dedupe_consecutive([inv["skill"] for inv in sess])
        if len(seq) < 2:
            continue
        seen_here: set[tuple] = set()
        for length in range(2, CHAIN_MAX_LEN + 1):
            for i in range(0, len(seq) - length + 1):
                gram = tuple(seq[i:i + length])
                if gram in seen_here:
                    continue  # count once per session
                seen_here.add(gram)
                n_by_chain[gram] += 1
                last_seen[gram] = max(last_seen.get(gram, 0), sess[-1]["_t"])
    out = [{"steps": list(g), "n": n, "last_seen": _iso(last_seen[g])}
           for g, n in n_by_chain.items() if n >= CHAIN_MIN_SUPPORT]
    out.sort(key=lambda c: (-c["n"], -len(c["steps"])))
    return out


# ---- Discovery ---------------------------------------------------------------

UNUSED_DAYS = 90


def learn_soft_skips(events: list[dict]) -> dict[str, int]:
    """Soft routes the model declined at turn end, per skill. A skill that is
    declined again and again is one the router should stop leading with."""
    out: Counter = Counter()
    for e in events:
        if e.get("type") != "soft-skip":
            continue
        for s in e.get("skills") or []:
            if isinstance(s, str):
                out[s] += 1
    return dict(out)


def learn_unused(invs: list[dict]) -> list[str]:
    """Invokable skills with no invocation in UNUSED_DAYS — the next archive pass."""
    try:
        cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    names = {e.get("name") for e in cat.get("entries", [])
             if e.get("invokable") and (e.get("source") in ("user", "project"))}
    cutoff = time.time() - UNUSED_DAYS * 86400
    recent = {i["skill"] for i in invs if i.get("_t", 0) >= cutoff}
    try:
        usage = USAGE_LOG.read_text(encoding="utf-8").splitlines()
    except OSError:
        usage = []
    for line in usage:
        parts = line.split("\t")
        if len(parts) >= 2:
            ts = _ts(parts[0].replace(" ", "T"))
            if ts and ts >= cutoff:
                recent.add(parts[1].strip().split(":")[-1])
                recent.add(parts[1].strip())
    return sorted(n for n in names if n and n not in recent and n.split(":")[-1] not in recent)


def _catalog_names() -> tuple[set[str], set[str]]:
    """(invokable skill names, all names incl. install-candidates).

    Both sets include the bare name of every namespaced plugin skill, so
    `superpowers:brainstorming` also registers as `brainstorming`. The online
    catalogs list skills by bare name; without this, every plugin you already
    have would be recommended back to you as an install.
    """
    if not CATALOG.is_file():
        return set(), set()
    try:
        data = json.loads(CATALOG.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return set(), set()
    inv, allnames = set(), set()
    for e in data.get("entries", []):
        name = e.get("name")
        if not name:
            continue
        variants = {name, name.split(":")[-1]}
        allnames |= variants
        if e.get("invokable"):
            inv |= variants
    # Installed plugins are also not install candidates, even when they are
    # hubs rather than skills (the plugin `superpowers` itself).
    for name in inv.copy():
        if ":" in name:
            inv.add(name.split(":")[0])
            allnames.add(name.split(":")[0])
    return inv, allnames


def _installed_canonical() -> set[str]:
    """Catalog entry names as written, for the discovery snapshot.

    Deliberately not _catalog_names(): that adds bare-name aliases for plugin
    skills so the online deduper works, and diffing an aliased set against a
    snapshot taken without aliases reported a hundred "new" skills that were
    nothing of the kind. Internals prefixed with `_` are skipped — they are
    not skills a person installs.
    """
    if not CATALOG.is_file():
        return set()
    try:
        data = json.loads(CATALOG.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return set()
    out = set()
    for e in data.get("entries", []):
        name = e.get("name") or ""
        if e.get("invokable") and name and not name.split(":")[-1].startswith("_"):
            out.add(name)
    return out


def learn_discovery(previous: dict, now_iso: str) -> dict:
    """Diff the installed catalog against the last run's snapshot.

    `new` keeps a first_seen date so the session brief can announce a skill
    once, when it is actually new, and stop after DISCOVERY_RECENT_DAYS.
    """
    invokable = _installed_canonical()
    prev_snapshot = set(previous.get("catalog_snapshot") or [])
    prev_new = {d["name"]: d for d in previous.get("discovered", {}).get("new", [])
                if isinstance(d, dict) and d.get("name")}
    cutoff = time.time() - DISCOVERY_RECENT_DAYS * 86400

    new: list[dict] = []
    for name in sorted(invokable):
        if name in prev_new:
            entry = prev_new[name]
            if (_ts(entry.get("first_seen", "")) or 0) >= cutoff:
                new.append(entry)
        elif prev_snapshot and name not in prev_snapshot:
            new.append({"name": name, "first_seen": now_iso})
    still_here = invokable | {n.split(":")[-1] for n in invokable} | {n.split(":")[0] for n in invokable if ":" in n}
    removed = sorted(n for n in prev_snapshot - invokable if n not in still_here) if prev_snapshot else []
    return {
        "catalog_snapshot": sorted(invokable),
        "discovered": {"new": new, "removed": removed, "checked_at": now_iso},
    }


# Tokens that describe how a skill is packaged rather than what it is about.
# They dominate skill names ("superpowers:test-driven-development" is three
# such words and one real one) and would make every framework-shaped skill
# online look like a fit for everyone.
PACKAGING_TOKENS = frozenset("""
superpowers anthropic claude plugin plugins skill skills agent agents
development driven based guide guidelines workflow workflows tool tools
best practices practice expert specialist assistant helper manager
""".split())


def _profile(events: list[dict], invs: list[dict]) -> Counter:
    """What you have been working on: keywords from recent prompts, plus the
    name tokens of skills you actually invoked (weighted higher — an
    invocation is a stronger statement of interest than a word).

    Plugin namespaces and packaging words are dropped: they say nothing about
    the subject of your work, and because they recur in hundreds of skill
    names they otherwise swamp the real signal."""
    try:
        from catalog_match import tokenize  # type: ignore[import-not-found]
    except ImportError:
        return Counter()
    prof: Counter = Counter()
    for e in events:
        if e.get("type") == "prompt":
            for tok in set(e.get("tokens") or []):
                if tok not in PACKAGING_TOKENS:
                    prof[tok] += 1
    for inv in invs:
        if inv["skill"] in EXCLUDED_SKILLS:
            continue
        bare = inv["skill"].split(":")[-1]
        for tok in tokenize(bare):
            if tok not in PACKAGING_TOKENS:
                prof[tok] += 3
    return prof


def learn_online(events: list[dict], invs: list[dict]) -> list[dict]:
    """Uninstalled skills from the online catalog that fit your recent work.

    Ranked by IDF-weighted overlap between each entry's description and your
    keyword profile. Entries already present anywhere on this machine — even
    as an install candidate under ~/.agent/skills — are marked so the
    suggestion says "copy it in" rather than "go and fetch it".
    """
    if not ONLINE_CATALOG.is_file():
        return []
    try:
        from catalog_match import tokenize  # type: ignore[import-not-found]
        data = json.loads(ONLINE_CATALOG.read_text(encoding="utf-8", errors="replace"))
    except (ImportError, json.JSONDecodeError, OSError):
        return []
    prompt_events = sum(1 for e in events if e.get("type") == "prompt" and e.get("tokens"))
    if prompt_events < ONLINE_MIN_PROMPTS:
        # Before prompt keywords exist the only profile source is skill names,
        # and a profile made of "debugging", "plans" and "router" recommends
        # frameworks to everyone. Better to say nothing until there is signal.
        return []
    profile = _profile(events, invs)
    if len(profile) < ONLINE_MIN_MATCHED:
        return []
    invokable, on_disk = _catalog_names()

    entries: list[dict] = []
    for source, items in (data.get("catalogs") or {}).items():
        for it in items or []:
            name = (it.get("name") or "").strip()
            desc = (it.get("description") or "").strip()
            if not name or not desc or name in invokable:
                continue
            entries.append({
                "name": name, "description": desc, "source": source,
                "url": it.get("source_url") or it.get("url") or "",
                "tokens": frozenset(tokenize(f"{name} {desc}")),
                "on_disk": name in on_disk,
            })
    if not entries:
        return []
    df: Counter = Counter()
    for e in entries:
        df.update(e["tokens"])
    n = len(entries)
    idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}

    # "Common" needs both a share and a floor. On the real corpus (~2,400
    # entries) 5% is 120 documents — a word that generic carries no signal. On
    # a small corpus every word clears 5%, so without the floor nothing could
    # ever match; the same trap the catalog matcher fell into at N=4.
    common_min_docs = max(3, int(0.05 * n))
    common = {t for t, c in df.items() if c >= common_min_docs} | PACKAGING_TOKENS
    scored: list[dict] = []
    for e in entries:
        hits = [t for t in e["tokens"] if t in profile and t not in common]
        if len(hits) < ONLINE_MIN_MATCHED:
            continue
        score = sum(idf.get(t, 0.0) * math.log1p(profile[t]) for t in hits)
        scored.append({
            "name": e["name"], "source": e["source"], "url": e["url"],
            "on_disk": e["on_disk"], "score": round(score, 2),
            "matched": sorted(hits, key=lambda t: -profile[t])[:6],
            "description": " ".join(e["description"].split())[:140],
        })
    scored.sort(key=lambda s: -s["score"])
    return scored[:ONLINE_TOP_N]


def refresh_online_if_stale(quiet: bool) -> None:
    """Refetch the online catalog when it is older than ONLINE_STALE_DAYS.

    Network, so never on the hot path: this runs only from the backgrounded
    SessionStart job or an explicit --refresh-online, and a failure leaves
    the previous catalog in place.
    """
    age = (time.time() - ONLINE_CATALOG.stat().st_mtime) / 86400 if ONLINE_CATALOG.is_file() else 1e9
    if age < ONLINE_STALE_DAYS:
        return
    fetcher = HERE / "online_catalog_fetcher.py"
    if not fetcher.is_file():
        return
    try:
        subprocess.run([sys.executable, str(fetcher)], capture_output=True,
                       text=True, timeout=120)
        if not quiet:
            print(f"[learn] refreshed online catalog ({age:.0f} days old)")
    except (subprocess.TimeoutExpired, OSError) as exc:
        if not quiet:
            print(f"[learn] online refresh skipped: {exc}", file=sys.stderr)


# ---- Log compaction ----------------------------------------------------------

def compact_log(keep_days: int = 7) -> int:
    """Drop dead-embedder dump events older than keep_days. Returns lines removed.

    Atomic rewrite via a temp file. Every other event type is preserved
    verbatim, so nothing the learner or the statusline reads is lost.
    """
    if not ROUTER_LOG.is_file():
        return 0
    cutoff = time.time() - keep_days * 86400
    kept: list[str] = []
    removed = 0
    with ROUTER_LOG.open("r", errors="ignore") as f:
        for line in f:
            try:
                e = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                kept.append(line)
                continue
            if isinstance(e, dict) and e.get("type") in COMPACTABLE_TYPES:
                t = _ts(e.get("ts", "")) or 0
                if t < cutoff:
                    removed += 1
                    continue
            kept.append(line)
    if removed:
        tmp = ROUTER_LOG.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(kept))
        tmp.replace(ROUTER_LOG)
    return removed


# ---- Assemble ----------------------------------------------------------------

def _iso(t: Optional[float]) -> Optional[str]:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)) if t else None


def load_previous() -> dict:
    if not LEARNED.is_file():
        return {}
    try:
        data = json.loads(LEARNED.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def build() -> dict:
    since = time.time() - WINDOW_DAYS * 86400
    events = read_router_log(since)
    legacy = read_usage_log(since)
    invs = invocations(events, legacy)
    sessions = sessionize(invs)
    now_iso = _iso(time.time()) or ""
    previous = load_previous()

    overlay = {
        "generated_at": now_iso,
        "version": 1,
        "window_days": WINDOW_DAYS,
        "counts": {
            "events": len(events),
            "invocations": len(invs),
            "sessions": len(sessions),
            "prompts_with_tokens": sum(1 for e in events if e.get("type") == "prompt" and e.get("tokens")),
        },
        "per_skill": learn_follow_rates(events, invs),
        "triggers": learn_triggers(events, invs),
        "handovers": learn_handovers(sessions),
        "chains": learn_chains(sessions),
        "online": learn_online(events, invs),
        "soft_skips": learn_soft_skips(events),
        "unused_90d": learn_unused(invs),
    }
    overlay.update(learn_discovery(previous, now_iso))
    return overlay


def write(overlay: dict) -> None:
    LEARNED.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEARNED.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(overlay, indent=1) + "\n")
    tmp.replace(LEARNED)


def summarize(o: dict) -> str:
    c = o.get("counts", {})
    disc = o.get("discovered", {})
    lines = [
        f"skill-router learned overlay — {o.get('generated_at')}",
        f"  window: {o.get('window_days')} days · {c.get('invocations', 0)} invocations "
        f"in {c.get('sessions', 0)} sessions · {c.get('prompts_with_tokens', 0)} prompts with keywords",
        f"  per_skill: {len(o.get('per_skill', {}))} · triggers: {len(o.get('triggers', []))} · "
        f"handovers: {sum(len(v) for v in o.get('handovers', {}).values())} · "
        f"chains: {len(o.get('chains', []))} · online suggestions: {len(o.get('online', []))}",
    ]
    if disc.get("new"):
        lines.append("  new since last run: " + ", ".join(d["name"] for d in disc["new"][:8])
                     + (" …" if len(disc["new"]) > 8 else ""))
    if disc.get("removed"):
        lines.append(f"  removed since last run: {len(disc['removed'])}")
    for h_from, hs in list(o.get("handovers", {}).items())[:5]:
        lines.append(f"  after {h_from} → {hs[0]['to']} ({hs[0]['p']:.0%}, n={hs[0]['n']})")
    for ch in o.get("chains", [])[:3]:
        lines.append(f"  chain ×{ch['n']}: " + " → ".join(ch["steps"]))
    for s in o.get("online", [])[:3]:
        where = "on disk, not installed" if s["on_disk"] else s["source"]
        lines.append(f"  install candidate: {s['name']} [{where}] ← {', '.join(s['matched'][:4])}")
    if not o.get("triggers") and c.get("prompts_with_tokens", 0) < TRIGGER_MIN_SUPPORT:
        lines.append("  (trigger learning starts once prompt events accumulate — "
                     "they are written by the router hook from now on)")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Learn a personal routing overlay from history.")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--show", action="store_true", help="print the current overlay and exit")
    ap.add_argument("--refresh-online", action="store_true",
                    help="refetch the online catalog first if it is stale")
    ap.add_argument("--compact", action="store_true",
                    help="drop dead embedder dumps from the router log")
    args = ap.parse_args()

    if args.show:
        print(json.dumps(load_previous(), indent=1))
        return 0
    if args.compact:
        removed = compact_log()
        if not args.quiet:
            print(f"[learn] compacted router log: {removed} dead events removed")
    if args.refresh_online:
        refresh_online_if_stale(args.quiet)

    overlay = build()
    write(overlay)
    if not args.quiet:
        print(summarize(overlay))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # a learner must never break a session start
        print(f"[skill-router-warn] learn: {exc}", file=sys.stderr)
        sys.exit(0)
