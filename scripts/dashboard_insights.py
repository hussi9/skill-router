#!/usr/bin/env python3
"""
dashboard_insights.py — turn the live dashboard's numbers into plain sentences and a to-do list.

Pure functions over the `/api/state` aggregate (scripts/live_dashboard.py) and the
outcomes-style events it already reads. Deterministic templates: no LLM or Jev call.

    badge(n)                       "too early" (n < 10) | "early signal" (10-29) | "solid" (>= 30)
    insights(state)                one sentence per panel, each with its numbers and sample badge
    ignored(events, days)          skills shown >= 3 times and never loaded
    missed(events, days)           skills Claude loaded although skill-router suggested other skills or nothing
    actions(state, ignored, missed) ranked to-do list, each with the exact next step (never performed)
    confidence(state)              per metric: sample size and badge

Events are outcomes-log dicts: `shown` (card, shortlist, held_out, session_id, prompt_id, ts)
and `signal` with kind "loaded" (skill, session_id, prompt_id, ts). Router-log `invoke`
loads are passed in the same shape. Only skill names and counts leave this module.
"""
from __future__ import annotations

import time
from typing import Any, Iterable, Optional

EARLY_AT = 10
SOLID_AT = 30
IGNORED_MIN_SHOWN = 3
LIST_MAX = 8
ACTIONS_MAX = 6
PER_SKILL_ACTIONS = 1     # top ignored and top missed skill only; the panel lists the rest
AUDIT_CMD = "python3 scripts/cli.py audit"
NAME_MAX = 80


def badge(n: int) -> str:
    """Sample-size label: below 10 too early to conclude, 10-29 an early signal, 30+ solid."""
    n = int(n or 0)
    if n < EARLY_AT:
        return "too early"
    if n < SOLID_AT:
        return "early signal"
    return "solid"


# ---- helpers --------------------------------------------------------------------

def _n(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v)


def _i(v: Any) -> int:
    x = _n(v)
    return int(x) if x is not None else 0


def _pct(part: float, whole: float) -> str:
    return f"{round(100 * part / whole)}%" if whole else "0%"


def _name(v: Any) -> Optional[str]:
    return v[:NAME_MAX] if isinstance(v, str) and v else None


def _names(values: Any) -> list[str]:
    return [n for n in (_name(v) for v in (values or [])) if n]


def _key(e: dict) -> tuple:
    return (e.get("session_id"), e.get("prompt_id"))


def _window(events: Iterable[dict], days: int, now: Optional[float]) -> list[dict]:
    cutoff = (time.time() if now is None else now) - max(1, int(days)) * 86400
    return [e for e in events if isinstance(e, dict) and (_n(e.get("ts")) or 0.0) >= cutoff]


def _bare(name: str) -> str:
    return name.split(":")[-1]


def _join(names: list[str], limit: int = 3, total: Optional[int] = None) -> str:
    more = (len(names) if total is None else total) - min(limit, len(names))
    return ", ".join(names[:limit]) + (f" (+{more} more)" if more > 0 else "")


# ---- ignored / missed -----------------------------------------------------------

def ignored(events: Iterable[dict], days: int, min_shown: int = IGNORED_MIN_SHOWN,
            now: Optional[float] = None, jev_conf: Optional[dict] = None) -> list[dict]:
    """Skills shown on >= min_shown printed lines and loaded 0 times in the window.

    shown counts non-held-out `shown` events naming the skill in card or shortlist;
    loaded counts `loaded` signals for the skill on any prompt (a plugin prefix is ignored:
    "plugin:x" loaded counts for a shown "x"). avg_jev is the mean
    Jev confidence of the prompts that showed it (None when unknown). Most-shown first, max 8.
    """
    rows = _window(events, days, now)
    shown: dict[str, int] = {}
    confs: dict[str, list[float]] = {}
    loaded: dict[str, int] = {}
    for e in rows:
        if e.get("type") == "shown" and not e.get("held_out"):
            c = (jev_conf or {}).get(_key(e))
            for n in set(_names(e.get("card")) + _names(e.get("shortlist"))):
                shown[n] = shown.get(n, 0) + 1
                if _n(c) is not None:
                    confs.setdefault(n, []).append(float(c))
        elif e.get("type") == "signal" and e.get("kind") == "loaded":
            n = _name(e.get("skill"))
            if n:
                loaded[_bare(n)] = loaded.get(_bare(n), 0) + 1
    out = []
    for n, k in shown.items():
        if k >= min_shown and not loaded.get(_bare(n)):
            cs = confs.get(n) or []
            out.append({"skill": n, "shown": k, "loaded": 0,
                        "avg_jev": round(sum(cs) / len(cs), 2) if cs else None})
    out.sort(key=lambda x: (-x["shown"], x["skill"]))
    return out[:LIST_MAX]


def missed(events: Iterable[dict], days: int, now: Optional[float] = None) -> list[dict]:
    """Skills Claude loaded on a prompt where skill-router showed nothing or only other skills.

    Each (prompt, skill) load counts once. Held-out prompts are left out (nothing was
    printed on purpose), as are loads without a prompt id. A plugin prefix is ignored when
    matching a load to the names shown. Most frequent first, max 8.
    """
    rows = _window(events, days, now)
    shown: dict[tuple, dict] = {}
    for e in rows:
        if e.get("type") == "shown":
            shown[_key(e)] = e
    seen: set[tuple] = set()
    times: dict[str, int] = {}
    for e in rows:
        if e.get("type") != "signal" or e.get("kind") != "loaded" or not e.get("prompt_id"):
            continue
        n = _name(e.get("skill"))
        k = _key(e)
        if not n or (k, n) in seen:
            continue
        seen.add((k, n))
        s = shown.get(k)
        if s is not None and s.get("held_out"):
            continue
        if s is not None and _bare(n) in {_bare(x) for x in _names(s.get("card")) + _names(s.get("shortlist"))}:
            continue
        times[n] = times.get(n, 0) + 1
    out = [{"skill": n, "times": t} for n, t in times.items()]
    out.sort(key=lambda x: (-x["times"], x["skill"]))
    return out[:LIST_MAX]


# ---- confidence -----------------------------------------------------------------

def _holdout_n(state: dict) -> int:
    lift = ((state.get("outcomes") or {}).get("lift")) or {}
    r = _i((lift.get("routed") or {}).get("known"))
    h = _i((lift.get("held_out") or {}).get("known"))
    return min(r, h)


def confidence(state: dict) -> dict:
    """Per metric: the sample it rests on and its badge."""
    jev = state.get("jev") or {}
    rt = state.get("routing") or {}
    oc = ((state.get("outcomes") or {}).get("summary")) or {}
    ns = {
        "jev": _i(jev.get("decisions")),
        "routing": sum(_i(rt.get(k)) for k in ("cards", "shortlists", "silent", "held_out")),
        "outcomes": _i(oc.get("shown")),
        "holdout": _holdout_n(state),
    }
    return {k: {"n": n, "badge": badge(n)} for k, n in ns.items()}


# ---- insights -------------------------------------------------------------------

def _jev_sentence(j: Optional[dict]) -> str:
    if not j:
        return "No Jev decisions logged in this window yet."
    n = _i(j.get("decisions"))
    rr = _n(j.get("route_rate"))
    ms = _n(j.get("median_ms"))
    s = f"Jev answered {n:,} prompts"
    if rr is not None:
        s += f"; {round(rr * 100)}% were sure enough (0.80+) to name one skill"
    if ms is not None:
        s += f"; typical answer {round(ms):,} ms"
    fails = _i(j.get("failures"))
    if fails:
        s += f"; {fails} failed"
    return s + f" — {badge(n)}."


def _routing_sentence(r: Optional[dict]) -> str:
    if not r:
        return "No prompts logged in this window yet."
    c, sl, q, h = (_i(r.get(k)) for k in ("cards", "shortlists", "silent", "held_out"))
    n = c + sl + q + h
    return (f"Of {n} prompts: one skill named on {c} ({_pct(c, n)}), a shortlist on {sl} "
            f"({_pct(sl, n)}), nothing on {q}; {h} held out for comparison — {badge(n)}.")


def _outcomes_sentence(o: Optional[dict]) -> str:
    if not o:
        return "No suggestion lines shown in this window yet."
    m = o.get("summary") or {}
    n = _i(m.get("shown"))
    card, short = _i(m.get("loaded_from_card")), _i(m.get("loaded_from_shortlist"))
    rate = _n(m.get("followed_rate")) or 0.0
    return (f"Claude used {round(rate * 100)}% of suggested skills ({round(rate * n)} of {n} lines): "
            f"{card} from routes, {short} from shortlists — {badge(n)}.")


def _listing_sentence(L: Optional[dict]) -> str:
    if not L:
        return "Listing not measured yet — run python3 scripts/cli.py listing measure."
    tot, desc = _i(L.get("entries")), _i(L.get("described"))
    return (f"Claude sees descriptions for {desc} of {tot} listed skills ({_pct(desc, tot)}); "
            f"{tot - desc} it can match by name only.")


def _audit_sentence(a: Optional[dict]) -> str:
    if not a:
        return "The skill audit has not finished its first run yet."
    return (f"Health {_i(a.get('score'))}/100: {_i(a.get('unused'))} of {_i(a.get('skills'))} skills "
            f"unused in 90 days, {_i(a.get('overlaps'))} overlapping pairs, "
            f"{_i(a.get('thin'))} descriptions too short.")


def _visibility_sentence(v: Optional[dict]) -> str:
    if not v:
        return "No visibility plans yet."
    return (f"{_i(v.get('worthwhile'))} of {_i(v.get('projects'))} project visibility plans "
            "would free listing room if applied (python3 scripts/cli.py visibility plan).")


def insights(state: dict) -> dict[str, str]:
    """One plain sentence per panel, with its numbers and the sample badge where it matters."""
    return {
        "jev": _jev_sentence(state.get("jev")),
        "routing": _routing_sentence(state.get("routing")),
        "outcomes": _outcomes_sentence(state.get("outcomes")),
        "listing": _listing_sentence(state.get("listing")),
        "audit": _audit_sentence(state.get("audit")),
        "visibility": _visibility_sentence(state.get("visibility")),
    }


# ---- actions --------------------------------------------------------------------

def _days_to(n: int, target: int, days: int) -> Optional[int]:
    """Days until n reaches target at the window's rate; None when nothing was collected."""
    if n <= 0:
        return None
    rate = n / max(1, days)
    return max(1, round((target - n) / rate))


def actions(state: dict, ignored_list: list[dict], missed_list: list[dict]) -> list[dict]:
    """Ranked to-do list (max 6). Each item names the exact next step; nothing is performed."""
    out: list[dict] = []
    a = state.get("audit") or {}
    days = _i((state.get("header") or {}).get("days")) or 7

    for x in ignored_list[:PER_SKILL_ACTIONS]:
        jev = f", average Jev confidence {x['avg_jev']:.2f}" if x.get("avg_jev") is not None else ""
        out.append({"title": f"Check why {x['skill']} is never loaded",
                    "why": (f"Shown {x['shown']} times, loaded 0{jev}; its description may not "
                            "match, or it's a routing miss."),
                    "how": f"edit the description in {x['skill']}/SKILL.md, or review in {AUDIT_CMD}",
                    "weight": 50 + x["shown"]})
    for x in missed_list[:PER_SKILL_ACTIONS]:
        out.append({"title": f"Help Jev find {x['skill']}",
                    "why": (f"Claude loaded it {x['times']} time{'s' if x['times'] != 1 else ''} "
                            "on prompts where skill-router suggested something else or nothing."),
                    "how": f"improve the description in {x['skill']}/SKILL.md",
                    "weight": 45 + 5 * x["times"]})

    dupes = [d for d in (a.get("duplicates") or []) if isinstance(d, (list, tuple)) and len(d) == 2]
    if dupes:
        pairs = "; ".join(f"{p} ~ {q}" for p, q in dupes[:2]) + (
            f" (+{len(dupes) - 2} more)" if len(dupes) > 2 else "")
        out.append({"title": f"Merge or retire one side of {len(dupes)} duplicate pair"
                             + ("s" if len(dupes) != 1 else ""),
                    "why": f"Jev judged these to do the same job: {pairs}.",
                    "how": f"review in {AUDIT_CMD}, then archive one of each pair",
                    "weight": 40 + 5 * len(dupes)})

    thin_names = [n for n in (a.get("thin_names") or []) if isinstance(n, str)]
    thin = _i(a.get("thin")) or len(thin_names)
    if thin:
        out.append({"title": f"Lengthen {thin} short skill description" + ("s" if thin != 1 else ""),
                    "why": "Under 5 distinct words, so Jev and the keyword match have little to go on.",
                    "how": ("edit the SKILL.md description: " + _join(thin_names, total=thin)) if thin_names
                           else f"list them with {AUDIT_CMD}",
                    "weight": 35 + min(thin, 25) * 0.4})

    unused = _i(a.get("unused"))
    if unused:
        L = state.get("listing") or {}
        room = (f"; {_i(L.get('name_only'))} of {_i(L.get('entries'))} listed skills already lose "
                "their description") if L.get("entries") else ""
        out.append({"title": f"Review {unused} skills unused in 90 days",
                    "why": f"They take listing room{room}.",
                    "how": f"review with {AUDIT_CMD}",
                    "weight": 30 + min(unused, 200) * 0.05})

    conf = confidence(state)
    oc = conf["outcomes"]["n"]
    if oc < SOLID_AT:
        more = _days_to(oc, SOLID_AT, days)
        when = f"~{more} more days" if more else "a few more days"
        out.append({"title": f"Keep using it {when}",
                    "why": f"Only {oc} suggestion lines so far — {badge(oc)}; 30 are needed for a solid follow rate.",
                    "how": "nothing to run; the numbers fill in as you work",
                    "weight": 70 if oc < EARLY_AT else 38})
    ho = conf["holdout"]["n"]
    if ho < EARLY_AT:
        more = _days_to(ho, EARLY_AT, days)
        when = f"~{more} more days" if more else "until tests run on held-out prompts"
        out.append({"title": f"Holdout lift needs {when}",
                    "why": (f"Only {ho} held-out prompt{'s' if ho != 1 else ''} with a test result "
                            f"— too early; 10 are needed before the lift means anything."),
                    "how": "keep running tests in your sessions; check back in python3 scripts/cli.py stats",
                    "weight": 20})

    out.sort(key=lambda x: -x["weight"])
    for x in out:
        x["weight"] = round(float(x["weight"]), 1)
    return out[:ACTIONS_MAX]
