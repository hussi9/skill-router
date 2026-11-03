#!/usr/bin/env python3
"""
skill_audit.py — what do your installed skills cost, and which are dead weight?

A read-only audit over data the router already keeps:

  catalog     ~/.claude/skill_index.json            names, descriptions, type, kind
              ~/.claude/skill_router_catalog.json   SKILL.md paths (for install age)
  usage       ~/.claude/skill_router_learned.json   per_skill invocation counts
              ~/.claude/skill_usage.log             invocations (timestamp + name)
  outcomes    outcomes.per_skill()                  shown vs loaded per skill
  skills.sh   ~/.claude/skill_router_skills_sh.json public audit verdicts

Only names, descriptions, counts and timestamps are read — never prompt text.
The audit recommends; it never installs, removes, disables or edits a skill.

Overlap detection has two modes. Without a key, two skills of the same kind
overlap when their description token sets have Jaccard >= 0.6. With a Jev
callable (TYPESAFE_API_KEY), candidate pairs with Jaccard >= 0.3 (at most 200,
batched <= 20 per call) are judged by Jev and count at probability >= 0.8; on
failure or when the time budget runs out the remaining pairs fall back to the
keyword rule.

"Unused" is narrow on purpose: usage is logged Skill-tool calls only, so
slash-command and other-client use is invisible. A skill counts as unused only
when it is a standalone skill (plugin skills are reported per plugin in
plugin_usage instead, since they cannot be archived one by one), installed at
least 90 days ago (SKILL.md mtime; unknown age is excluded and counted in
age_unknown), with at least 30 days of history.

`flagged` lists installed skills sharing a name with a public skills.sh skill
whose audit flagged it. A name collision is not evidence, so it costs no score.

Score = 100 - min(30, unused share * 60) - min(25, overlaps * 3)
            - listing term (simulated) min(20, round(descriptions_dropped / max(1, skills_with_descriptions) * 40))
              or, with a fresh /context measurement (listing_probe),
              round(20 * actionable_dropped / max(1, actionable_entries)
                    * min(1, (skills_tokens / context_window) / 0.05))
              where actionable entries have source User, Project or Plugin (claude.ai and
              Built-in entries cannot be changed by the user) and the scaling factor is 1.0
              when the context window is unknown.

Listing the harness sends: Claude Code does not send the raw listing. Per its published
docs (https://code.claude.com/docs/en/skills.md) the listing gets 1% of the context window
(`skillListingBudgetFraction`, default 0.01; env SLASH_COMMAND_TOOL_CHAR_BUDGET overrides it in
characters), each skill's description is cut to `skillListingMaxDescChars` (default 1536), and
on overflow the descriptions of the least-used skills are dropped first while names are kept.
The defaults here mirror those docs and may drift from the harness. The context size is an
ASSUMPTION (200000 tokens, `--context-tokens`), since the audit cannot see the live window.
Settings are read from ~/.claude/settings.json, then the project's .claude/settings.json and
.claude/settings.local.json, later wins. listing_tokens stays the raw "uncapped" sum, shown for
information only; the score charges for descriptions the harness drops, not for raw size.

Paths are overridable by parameter or env (SKILL_AUDIT_INDEX, SKILL_AUDIT_LEARNED,
SKILL_AUDIT_USAGE_LOG, SKILL_AUDIT_CATALOG, SKILL_AUDIT_SETTINGS; the outcomes log follows
SKILL_ROUTER_OUTCOMES_FILE and the skills.sh cache SKILL_ROUTER_SKILLS_SH_FILE).
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import index_match  # type: ignore[import-not-found]  # noqa: E402
import jev_choose  # type: ignore[import-not-found]  # noqa: E402
import outcomes as outcomes_mod  # type: ignore[import-not-found]  # noqa: E402

JevFn = Callable[[list[tuple[str, str]]], dict[tuple[str, str], float]]

SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.-]{0,99}")
NON_ROUTABLE = ("builtin", "plugin-command", "command", "agent")
KEYWORD_OVERLAP_AT = 0.6
# Boilerplate descriptions reduce to a couple of shared tokens and score Jaccard 1.0 without
# being duplicates; Jev rejected 29 of the 31 pairs flagged at >= 0.6 (30 of them were identical boilerplate at >= 0.9). Chosen from that data.
KEYWORD_OVERLAP_MIN_TOKENS = 5
JEV_CANDIDATE_AT = 0.3
JEV_OVERLAP_AT = 0.8
JEV_MAX_PAIRS = 200
JEV_BATCH = 20
MIN_HISTORY_DAYS = 30
UNUSED_DAYS = 90
SHOWN_MIN = 5
JEV_CALL_TIMEOUT_S = 5.0
STATE_DESC_CHARS = 500
DEFAULT_CONTEXT_TOKENS = 200_000          # assumption; the live window is not visible here
DEFAULT_BUDGET_FRACTION = 0.01            # mirrors Claude Code docs (skills.md)
DEFAULT_DESC_CAP = 1536                   # mirrors Claude Code docs (skills.md)
CHARS_PER_TOKEN = 4

CLAUDE_DIR = Path.home() / ".claude"


@dataclass(frozen=True)
class AuditReport:
    skills: int
    listing_tokens: int            # sum(len(name) + len(description)) // 4 over routable skills
    unused_90d: list[str]          # empty when history_days < 30
    history_days: int              # span of usage history available
    shown_never_loaded: list[str]  # outcomes: shown >= 5 times, loaded 0 (empty without outcomes)
    overlaps: list[tuple[str, str, float, str]]  # (a, b, score, "jev"|"keyword")
    flagged: list[str]             # skills.sh audit "flagged" for installed names
    score: int                     # 0-100
    top_actions: list[str] = field(default_factory=list)  # <= 3 one-line recommendations
    age_unknown: int = 0           # standalone skills left out of unused_90d: install age unknown
    plugin_usage: list[tuple[str, int, int]] = field(default_factory=list)  # (plugin, used 90d, total)
    listing_budget_tokens: int = 0  # the harness listing budget, in tokens (assumed context size)
    descriptions_dropped: int = 0   # skills whose description does not fit the budget
    listing_sent_tokens: int = 0    # listing tokens after the per-skill cap and the drops
    thin_descriptions: int = 0      # routable skills with < KEYWORD_OVERLAP_MIN_TOKENS tokens
    measured_entries: int = 0       # listing entries Claude Code reported (0 = not measured)
    measured_described: int = 0     # of those, entries sent with a description
    measured_tokens: int = 0        # the /context "Skills" token figure
    measured_actionable: int = 0    # measured entries the user can change (User/Project/Plugin)
    measured_actionable_dropped: int = 0   # of those, listed by name only


# ---- helpers -----------------------------------------------------------------
ACTIONABLE_SOURCES = ("user", "project", "plugin")
LISTING_SHARE_FULL = 0.05       # listing share of the window at which the measured term is full


def _routable(catalog: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for e in catalog or []:
        if not isinstance(e, dict):
            continue
        name = e.get("name")
        if not isinstance(name, str) or not name or name in seen:
            continue
        if e.get("type") in NON_ROUTABLE:
            continue
        seen.add(name)
        out.append(e)
    return out


def _bare(name: str) -> str:
    return name.split(":")[-1]


def _desc(e: dict) -> str:
    d = e.get("description")
    return d if isinstance(d, str) else ""


def _used(name: str, usage: dict[str, int], bare_used: set[str]) -> bool:
    return usage.get(name, 0) > 0 or _bare(name) in bare_used


def _installed_at(e: dict) -> Optional[float]:
    """Install time: an explicit `installed_at`, else the SKILL.md (or skill dir) mtime."""
    t = e.get("installed_at")
    if isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t):
        return float(t)
    raw = e.get("path") or e.get("source_path")
    if not isinstance(raw, str) or not raw:
        return None
    path = Path(raw).expanduser()
    for candidate in ((path / "SKILL.md", path) if path.is_dir() else (path,)):
        try:
            return candidate.stat().st_mtime
        except OSError:
            continue
    return None


def _plugin_usage(skills: list[dict], usage: dict[str, int],
                  bare_used: set[str]) -> list[tuple[str, int, int]]:
    groups: dict[str, list[str]] = {}
    for e in skills:
        if e.get("type") == "plugin-skill":
            plugin = e["name"].split(":", 1)[0] if ":" in e["name"] else "unknown"
            groups.setdefault(plugin, []).append(e["name"])
    return sorted(((p, sum(_used(n, usage, bare_used) for n in names), len(names))
                   for p, names in groups.items()), key=lambda x: (x[1] / x[2], x[0]))


def _num(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def listing_budget(settings: dict, context_tokens: int = DEFAULT_CONTEXT_TOKENS) -> int:
    """Characters the harness gives the skill listing.

    SLASH_COMMAND_TOOL_CHAR_BUDGET wins; else skillListingBudgetFraction (default 0.01)
    x context_tokens x 4. Defaults mirror Claude Code's docs; context size is an assumption.
    """
    try:
        env = float(os.environ.get("SLASH_COMMAND_TOOL_CHAR_BUDGET", "").strip())
    except ValueError:
        env = 0.0
    if math.isfinite(env) and env >= 1:
        return int(env)
    frac = _num((settings or {}).get("skillListingBudgetFraction")) or DEFAULT_BUDGET_FRACTION
    return int(frac * context_tokens * CHARS_PER_TOKEN)


def desc_cap(settings: dict) -> int:
    """Per-skill description cap in characters (skillListingMaxDescChars, default 1536)."""
    cap = _num((settings or {}).get("skillListingMaxDescChars"))
    return int(cap) if cap else DEFAULT_DESC_CAP


def load_settings(project_dir: Optional[Path] = None, user_path: Optional[Path] = None) -> dict:
    """Merged Claude Code settings: user, then project settings.json, then settings.local.json."""
    root = Path(project_dir) if project_dir is not None else Path.cwd()
    merged: dict = {}
    for path in (_path(user_path, "SKILL_AUDIT_SETTINGS", CLAUDE_DIR / "settings.json"),
                 root / ".claude" / "settings.json", root / ".claude" / "settings.local.json"):
        merged.update(_json(Path(path)) or {})
    return merged


def fit_listing_detail(skills: list[dict], usage: dict[str, int], budget: int, cap: int,
                       name_only: frozenset[str] | set[str] = frozenset(),
                       names_in_budget: bool = True) -> tuple[int, set[str]]:
    """(chars sent, names that KEEP their description) for one simulated listing.

    Each description is cut to `cap`; skills in `name_only` contribute their name only; on
    overflow the least-used descriptions are dropped first (ties by name). `usage` stands in
    for the harness's own usage stats, which are not visible here, so this is a simulation.
    Name + description length only; the harness's per-entry formatting overhead is not modelled.
    `names_in_budget` (default True, the audit's model) charges names against the budget; the
    docs do not confirm it. When False only description characters count; names are free and
    always present. The returned chars always include names.
    """
    costs = {e["name"]: (len(e["name"]), 0 if e["name"] in name_only else min(len(_desc(e)), cap))
             for e in skills}
    names = sum(n for n, _ in costs.values())
    total = sum(n + d for n, d in costs.values())
    free = 0 if names_in_budget else names
    kept = {n for n, (_, d) in costs.items() if d}
    if total - free > budget:
        order = sorted(kept, key=lambda n: (max(usage.get(n, 0), usage.get(_bare(n), 0)), n))
        for n in order:
            if total - free <= budget:
                break
            total -= costs[n][1]
            kept.discard(n)
    return total, kept


def _fit_listing(skills: list[dict], usage: dict[str, int],
                 budget: int, cap: int) -> tuple[int, int]:
    """(chars sent, descriptions dropped): cap each description, then drop least-used first."""
    total, kept = fit_listing_detail(skills, usage, budget, cap)
    with_desc = sum(1 for e in skills if min(len(_desc(e)), cap))
    return total, with_desc - len(kept)


def _jaccard_pairs(skills: list[dict], threshold: float) -> list[tuple[str, str, float]]:
    """Same-kind pairs with description-token Jaccard >= threshold, best first."""
    by_kind: dict[str, list[tuple[str, frozenset]]] = {}
    for e in skills:
        toks = frozenset(index_match.tokenize(_desc(e)))
        if len(toks) >= KEYWORD_OVERLAP_MIN_TOKENS:
            by_kind.setdefault(str(e.get("kind") or ""), []).append((e["name"], toks))
    out: list[tuple[str, str, float]] = []
    for group in by_kind.values():
        group.sort(key=lambda x: len(x[1]))
        for i, (na, ta) in enumerate(group):
            la = len(ta)
            for nb, tb in group[i + 1:]:
                lb = len(tb)
                if la < threshold * lb:        # sorted by size: no later b can reach it
                    break
                inter = len(ta & tb)
                if not inter:
                    continue
                j = inter / (la + lb - inter)
                if j >= threshold:
                    a, b = sorted((na, nb))
                    out.append((a, b, j))
    out.sort(key=lambda p: (-p[2], p[0], p[1]))
    return out


def _valid_prob(value: object) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    return v if math.isfinite(v) and 0 <= v <= 1 else None


def _overlaps(skills: list[dict], jev: Optional[JevFn],
              budget_s: float) -> list[tuple[str, str, float, str]]:
    candidates = _jaccard_pairs(skills, JEV_CANDIDATE_AT if jev else KEYWORD_OVERLAP_AT)
    judged: dict[tuple[str, str], float] = {}
    if jev is not None:
        start = time.monotonic()
        todo = [(a, b) for a, b, _ in candidates[:JEV_MAX_PAIRS]]
        for i in range(0, len(todo), JEV_BATCH):
            remaining = budget_s - (time.monotonic() - start)
            if remaining <= 0:
                break
            batch = todo[i:i + JEV_BATCH]
            try:
                if getattr(jev, "takes_timeout", False):
                    answer = jev(batch, timeout=min(JEV_CALL_TIMEOUT_S, remaining)) or {}  # type: ignore[call-arg]
                else:
                    answer = jev(batch) or {}
            except Exception:                  # noqa: BLE001 — any Jev failure → keyword fallback
                break
            if not isinstance(answer, dict):
                break
            for pair in batch:
                p = _valid_prob(answer.get(pair))
                if p is not None:
                    judged[pair] = p
    out: list[tuple[str, str, float, str]] = []
    for a, b, j in candidates:
        if (a, b) in judged:
            if judged[(a, b)] >= JEV_OVERLAP_AT:
                out.append((a, b, round(judged[(a, b)], 3), "jev"))
        elif j >= KEYWORD_OVERLAP_AT:
            out.append((a, b, round(j, 3), "keyword"))
    return out


def _flagged(skills: list[dict], skills_sh: Optional[dict]) -> list[str]:
    if not isinstance(skills_sh, dict):
        return []
    catalogs = skills_sh.get("catalogs")
    rows = catalogs.get("skills.sh") if isinstance(catalogs, dict) else None
    bad = {r["name"] for r in rows or [] if isinstance(r, dict)
           and isinstance(r.get("name"), str) and r.get("audit") == "flagged"}
    meta = skills_sh.get("metadata")
    for f in (meta.get("flagged") if isinstance(meta, dict) else None) or []:
        if isinstance(f, dict) and isinstance(f.get("skill"), str) and f["skill"]:
            bad.add(f["skill"])
    return sorted(e["name"] for e in skills if e["name"] in bad or _bare(e["name"]) in bad)


# ---- audit -------------------------------------------------------------------

def audit(catalog: list[dict], usage: dict[str, int], history_days: int,
          outcomes: dict | None, skills_sh: dict | None,
          jev: Optional[JevFn] = None,
          budget_s: float = 10.0, now: Optional[float] = None,
          settings: Optional[dict] = None,
          context_tokens: int = DEFAULT_CONTEXT_TOKENS,
          measured: Optional[dict] = None) -> AuditReport:
    """Audit the installed skills. Pure apart from calling `jev`.

    Args:
        catalog: index entries (name, description, type, kind).
        usage: invocation counts by skill name (full or bare).
        history_days: span of usage history available.
        outcomes: {skill: {"shown": int, "loaded": int}}, or None.
        skills_sh: the skills.sh cache document, or None.
        jev: judges name pairs → probability "same job"; None for keyword only.
        budget_s: wall-clock budget for all Jev calls.
        now: reference time for install age (default: time.time()).
        settings: merged Claude Code settings for the listing budget (default: none, so defaults).
        context_tokens: assumed context window for the listing budget.
        measured: a fresh listing_probe measurement; its dropped count and entry total replace
            the simulation in the score's listing term.
    """
    skills = _routable(catalog)
    usage = {k: v for k, v in (usage or {}).items() if isinstance(v, int) and v > 0}
    bare_used = {_bare(k) for k in usage}
    listing_tokens = sum(len(e["name"]) + len(_desc(e)) for e in skills) // 4
    budget = listing_budget(settings or {}, context_tokens)
    sent_chars, dropped = _fit_listing(skills, usage, budget, desc_cap(settings or {}))
    with_desc = sum(bool(_desc(e)) for e in skills)
    m_entries = m_desc = m_tokens = m_act = m_act_drop = 0
    m_share: Optional[float] = None
    m_penalty: Optional[int] = None
    if measured and measured.get("entries"):
        m_entries = len(measured["entries"])
        m_desc = sum(1 for e in measured["entries"] if e.get("described"))
        m_tokens = int(measured.get("skills_tokens") or 0)
        dropped, with_desc = m_entries - m_desc, m_entries
        act = [e for e in measured["entries"]
               if str(e.get("source", "")).strip().lower().startswith(ACTIONABLE_SOURCES)]
        m_act, m_act_drop = len(act), sum(1 for e in act if not e.get("described"))
        win = measured.get("context_window")
        m_share = (m_tokens / win) if isinstance(win, (int, float)) and win > 0 else None
        scale = 1.0 if m_share is None else min(1.0, m_share / LISTING_SHARE_FULL)
        m_penalty = round(20 * m_act_drop / max(1, m_act) * scale)
    thin = sum(len(set(index_match.tokenize(_desc(e)))) < KEYWORD_OVERLAP_MIN_TOKENS
               for e in skills)

    now = time.time() if now is None else now
    old_enough = now - UNUSED_DAYS * 86400
    unused: list[str] = []
    age_unknown = 0
    for e in skills:
        if e.get("type") == "plugin-skill":
            continue
        installed = _installed_at(e)
        if installed is None:
            age_unknown += 1
        elif (history_days >= MIN_HISTORY_DAYS and installed <= old_enough
              and not _used(e["name"], usage, bare_used)):
            unused.append(e["name"])
    unused.sort()

    shown_never: list[str] = []
    if isinstance(outcomes, dict):
        shown_never = sorted(
            n for n, c in outcomes.items() if isinstance(c, dict)
            and int(c.get("shown") or 0) >= SHOWN_MIN and int(c.get("loaded") or 0) == 0)

    overlaps = _overlaps(skills, jev, budget_s)
    flagged = _flagged(skills, skills_sh)

    share = len(unused) / len(skills) if skills else 0.0
    safe = lambda xs: [n for n in xs if isinstance(n, str) and SAFE_NAME.fullmatch(n)]  # noqa: E731
    unused, flagged, shown_never = safe(unused), safe(flagged), safe(shown_never)
    overlaps = [o for o in overlaps if SAFE_NAME.fullmatch(o[0]) and SAFE_NAME.fullmatch(o[1])]
    deductions = [
        (min(30.0, share * 60),
         f"{len(unused)} skills show no logged Skill-tool use in {UNUSED_DAYS} days "
         "(slash-command and other-client use is not tracked) — review before archiving; "
         "archiving frees listing room so more of the remaining descriptions fit."),
        (min(25.0, len(overlaps) * 3.0),
         f"Merge or retire one side of {len(overlaps)} overlapping skill pairs."),
        ((float(m_penalty),
          f"{m_act_drop} of {m_act} of your skills are listed by name only; the listing uses "
          + (f"~{m_share * 100:.1f}% of the context window" if m_share is not None
             else "an unknown share of the context window")
          + " — archive unused skills or set rarely used ones to name-only per project "
          "(skillOverrides).")
         if m_penalty is not None else
         (min(20.0, round(dropped / max(1, with_desc) * 40)),
          f"{dropped} of {with_desc} skills lose their description in the skill listing — "
          "archive unused skills or set rarely used ones to name-only per project "
          "(skillOverrides).")),
    ]
    score = max(0, min(100, round(100 - sum(d for d, _ in deductions))))
    actions = [text for d, text in sorted(deductions, key=lambda x: -x[0]) if d > 0][:3]
    if flagged and len(actions) < 3:
        actions.append(f"{len(flagged)} skills have the same name as a flagged public skill — "
                       "check its source: " + ", ".join(flagged[:3])
                       + (" ..." if len(flagged) > 3 else "") + ".")
    if shown_never and len(actions) < 3:
        actions.append(f"Reword or retire {len(shown_never)} skills shown {SHOWN_MIN}+ times "
                       "but never loaded.")
    return AuditReport(skills=len(skills), listing_tokens=listing_tokens, unused_90d=unused,
                       history_days=int(history_days), shown_never_loaded=shown_never,
                       overlaps=overlaps, flagged=flagged, score=score, top_actions=actions,
                       age_unknown=age_unknown,
                       plugin_usage=_plugin_usage(skills, usage, bare_used),
                       listing_budget_tokens=budget // CHARS_PER_TOKEN,
                       descriptions_dropped=dropped,
                       listing_sent_tokens=sent_chars // CHARS_PER_TOKEN,
                       thin_descriptions=thin, measured_entries=m_entries,
                       measured_described=m_desc, measured_tokens=m_tokens,
                       measured_actionable=m_act, measured_actionable_dropped=m_act_drop)


# ---- default Jev judge ---------------------------------------------------------

PAIR_INSTRUCTIONS = ("Do these two agent skills do the same job? Compare the skill "
                     "descriptions in state.pairs.{qid}.a and state.pairs.{qid}.b. Judge the "
                     "action and deliverable, not shared vocabulary or subject.")


def make_jev(catalog: list[dict], timeout: float = JEV_CALL_TIMEOUT_S) -> Optional[JevFn]:
    """A Jev pair judge over `catalog` descriptions, or None without TYPESAFE_API_KEY."""
    key = jev_choose._key()
    if not key:
        return None
    desc = {e["name"]: _desc(e)[:STATE_DESC_CHARS] for e in _routable(catalog)}

    def judge(pairs: list[tuple[str, str]],
              timeout: float = timeout) -> dict[tuple[str, str], float]:
        pairs = list(pairs)[:JEV_BATCH]
        qids = {f"p{i}": p for i, p in enumerate(pairs)}
        body = {
            "model": jev_choose.MODEL,
            "state": {"pairs": {q: {"a": desc.get(a, ""), "b": desc.get(b, "")}
                                for q, (a, b) in qids.items()}},
            "questions": {q: {"type": "noul",
                              "instructions": PAIR_INSTRUCTIONS.format(qid=q),
                              "criteria": {
                                  "true": "Both skills produce the same kind of deliverable for "
                                          "the same action; installing both is redundant.",
                                  "false": "The skills serve different actions or deliverables, "
                                           "even if they share a subject or keywords."}}
                          for q in qids},
        }
        data = jev_choose._deadline(lambda: jev_choose._post(body, key, timeout), timeout)
        answers = data.get("answers") if isinstance(data, dict) else None
        if not isinstance(answers, dict):
            raise RuntimeError("jev: no answers")
        out: dict[tuple[str, str], float] = {}
        for q, pair in qids.items():
            a = answers.get(q)
            if isinstance(a, dict) and a.get("type") == "noul":
                p = _valid_prob(a.get("noul"))
                if p is not None:
                    out[pair] = p
        return out

    judge.takes_timeout = True  # type: ignore[attr-defined]  # audit passes min(5 s, budget left)
    return judge


# ---- inputs ------------------------------------------------------------------

def _path(given: Optional[Path], env: str, default: Path) -> Path:
    if given is not None:
        return Path(given)
    return Path(os.environ[env]) if os.environ.get(env) else default


def _json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _usage_log(path: Path) -> list[tuple[float, str]]:
    """(timestamp, skill) rows: 'YYYY-MM-DD HH:MM:SS<TAB or space>skill'."""
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return []
    out: list[tuple[float, str]] = []
    for line in lines:
        line = line.strip()
        if len(line) < 21:
            continue
        try:
            t = datetime.strptime(line[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            continue
        skill = line[19:].strip()
        if skill:
            out.append((t, skill))
    return out


def load_inputs(*, index_path: Optional[Path] = None, learned_path: Optional[Path] = None,
                usage_log: Optional[Path] = None, skills_sh_path: Optional[Path] = None,
                catalog_path: Optional[Path] = None, since_days: int = UNUSED_DAYS,
                ) -> tuple[list[dict], dict[str, int], int, dict | None, dict | None]:
    """(catalog, usage, history_days, outcomes, skills_sh); each missing input → empty/None."""
    index = _json(_path(index_path, "SKILL_AUDIT_INDEX", CLAUDE_DIR / "skill_index.json"))
    entries = index.get("entries") if index else None
    catalog = [dict(e) for e in entries or [] if isinstance(e, dict) and e.get("name")]
    full = _json(_path(catalog_path, "SKILL_AUDIT_CATALOG",
                       CLAUDE_DIR / "skill_router_catalog.json"))
    paths = {e["name"]: e["source_path"] for e in (full or {}).get("entries") or []
             if isinstance(e, dict) and isinstance(e.get("name"), str)
             and isinstance(e.get("source_path"), str) and e["source_path"]}
    for e in catalog:
        if e["name"] in paths and not e.get("path"):
            e["path"] = paths[e["name"]]

    usage: dict[str, int] = {}
    learned = _json(_path(learned_path, "SKILL_AUDIT_LEARNED",
                          CLAUDE_DIR / "skill_router_learned.json"))
    per = learned.get("per_skill") if learned else None
    for name, stats in (per or {}).items():
        n = stats.get("invocations") if isinstance(stats, dict) else None
        if isinstance(name, str) and isinstance(n, int) and not isinstance(n, bool) and n > 0:
            usage[name] = n

    rows = _usage_log(_path(usage_log, "SKILL_AUDIT_USAGE_LOG", CLAUDE_DIR / "skill_usage.log"))
    now = time.time()
    cutoff = now - since_days * 86400
    recent: dict[str, int] = {}
    for t, skill in rows:
        if t >= cutoff:
            recent[skill] = recent.get(skill, 0) + 1
    for skill, n in recent.items():          # learner window is shorter; take the larger count
        usage[skill] = max(usage.get(skill, 0), n)
    history_days = max(0, round((now - min(t for t, _ in rows)) / 86400)) if rows else 0

    try:
        per_skill = outcomes_mod.per_skill(since_days)
    except Exception:                          # noqa: BLE001 — a broken log means no outcomes input
        per_skill = {}
    skills_sh = _json(_path(skills_sh_path, "SKILL_ROUTER_SKILLS_SH_FILE",
                            CLAUDE_DIR / "skill_router_skills_sh.json"))
    return catalog, usage, history_days, (per_skill or None), skills_sh
