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
MISSED_MIN = 2            # a missed skill earns a to-do only after 2 first loads
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
            now: Optional[float] = None, jev_conf: Optional[dict] = None,
            limit: Optional[int] = LIST_MAX) -> list[dict]:
    """Skills shown in >= min_shown printed lines and loaded 0 times in the window.

    shown counts non-held-out `shown` lines naming the skill; card counts the lines where
    it was the route, shortlist the lines where it was only in the shortlist. A shortlist
    mention is a weaker suggestion, so ranking weighs it half (card + shortlist / 2).
    loaded counts `loaded` signals for the skill on any prompt (a plugin prefix is ignored:
    "plugin:x" loaded counts for a shown "x"). avg_jev is the mean Jev confidence of the
    prompts that showed it (None when unknown). limit=None returns every row.
    """
    rows = _window(events, days, now)
    card: dict[str, int] = {}
    short: dict[str, int] = {}
    confs: dict[str, list[float]] = {}
    loaded: dict[str, int] = {}
    for e in rows:
        if e.get("type") == "shown" and not e.get("held_out"):
            c = (jev_conf or {}).get(_key(e))
            cs = set(_names(e.get("card")))
            for n in cs | set(_names(e.get("shortlist"))):
                bucket = card if n in cs else short
                bucket[n] = bucket.get(n, 0) + 1
                if _n(c) is not None:
                    confs.setdefault(n, []).append(float(c))
        elif e.get("type") == "signal" and e.get("kind") == "loaded":
            n = _name(e.get("skill"))
            if n:
                loaded[_bare(n)] = loaded.get(_bare(n), 0) + 1
    out = []
    for n in set(card) | set(short):
        c, sl = card.get(n, 0), short.get(n, 0)
        if c + sl >= min_shown and not loaded.get(_bare(n)):
            cs = confs.get(n) or []
            out.append({"skill": n, "shown": c + sl, "card": c, "shortlist": sl, "loaded": 0,
                        "avg_jev": round(sum(cs) / len(cs), 2) if cs else None})
    out.sort(key=lambda x: (-(x["card"] + x["shortlist"] / 2), -x["shown"], x["skill"]))
    return out if limit is None else out[:limit]


def missed(events: Iterable[dict], days: int, now: Optional[float] = None,
           limit: Optional[int] = LIST_MAX) -> list[dict]:
    """Skills Claude loaded first on a prompt where skill-router suggested other skills or nothing.

    Only prompts skill-router actually routed count: they carry a router `prompt` event or a
    `shown` event. A typed /skill command or the escape hatch makes the router stand down
    before it logs either, so those prompts drop out. Only the first load of each prompt
    counts — later loads are usually one skill chaining into another. Held-out prompts are
    left out (nothing was printed on purpose), as are loads without a prompt id. A plugin
    prefix is ignored when matching a load to the names shown. limit=None returns every row.
    """
    rows = _window(events, days, now)
    shown: dict[tuple, dict] = {}
    routed: set[tuple] = set()
    for e in rows:
        if e.get("type") == "shown":
            shown[_key(e)] = e
            routed.add(_key(e))
        elif e.get("type") == "prompt":
            routed.add(_key(e))
    first: dict[tuple, tuple[float, str]] = {}
    for e in rows:
        if e.get("type") != "signal" or e.get("kind") != "loaded" or not e.get("prompt_id"):
            continue
        n, k, t = _name(e.get("skill")), _key(e), _n(e.get("ts")) or 0.0
        if n and k in routed and (k not in first or t < first[k][0]):
            first[k] = (t, n)
    times: dict[str, int] = {}
    for k, (_, n) in first.items():
        s = shown.get(k)
        if s is not None and s.get("held_out"):
            continue
        if s is not None and _bare(n) in {_bare(x) for x in _names(s.get("card")) + _names(s.get("shortlist"))}:
            continue
        times[n] = times.get(n, 0) + 1
    out = [{"skill": n, "times": t} for n, t in times.items()]
    out.sort(key=lambda x: (-x["times"], x["skill"]))
    return out if limit is None else out[:limit]


# ---- confidence -----------------------------------------------------------------

_ORDER = ("too early", "early signal", "solid")


def _holdout_n(state: dict) -> int:
    lift = ((state.get("outcomes") or {}).get("lift")) or {}
    r = _i((lift.get("routed") or {}).get("known"))
    h = _i((lift.get("held_out") or {}).get("known"))
    return min(r, h)


def _loads(summary: dict) -> int:
    return _i(summary.get("loaded_from_card")) + _i(summary.get("loaded_from_shortlist"))


def _outcomes_badge(shown: int, loads: int) -> str:
    """A follow rate rests on its rarer side: the loads. Under 10 loads it is at best an early signal."""
    if loads >= EARLY_AT:
        return badge(min(shown, loads))
    return _ORDER[min(_ORDER.index(badge(shown)), 1)]


def confidence(state: dict) -> dict:
    """Per metric: the sample it rests on and its badge (outcomes: lines and loads)."""
    jev = state.get("jev") or {}
    rt = state.get("routing") or {}
    oc = ((state.get("outcomes") or {}).get("summary")) or {}
    out = {k: {"n": n, "badge": badge(n)} for k, n in {
        "jev": _i(jev.get("decisions")),
        "routing": sum(_i(rt.get(k)) for k in ("cards", "shortlists", "silent", "held_out")),
        "holdout": _holdout_n(state),
    }.items()}
    shown, loads = _i(oc.get("shown")), _loads(oc)
    out["outcomes"] = {"n": shown, "loads": loads, "badge": _outcomes_badge(shown, loads)}
    return out


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
    followed = _i(m.get("followed")) if m.get("followed") is not None else card + short
    return (f"Claude used a suggested skill on {_pct(followed, n)} of lines ({followed} of {n}): "
            f"{card} load{'s' if card != 1 else ''} from routes, {short} from shortlists — {_outcomes_badge(n, card + short)}.")


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

DAYS_CAP = 30
PLUGIN_IGNORED = "plugin skill — hide it per project (skillOverrides) or report upstream"
PLUGIN_MISSED = "plugin skill — its description lives upstream; report the gap there"


def _days_to(n: int, target: int, days: int) -> str:
    """How long until n reaches target at the window's rate, in words.

    Fewer than 2 samples is no rate at all; beyond 30 days the estimate is too loose to state.
    """
    if n < 2:
        return "unknown (fewer than 2 samples)"
    more = max(1, round((target - n) / (n / max(1, days))))
    return f"{DAYS_CAP}+ days" if more > DAYS_CAP else f"~{more} more days"


def _edit_step(name: str, state: dict, what: str, plugin_step: str = PLUGIN_IGNORED) -> str:
    """Where to make the change: the real SKILL.md for user/project skills; plugins are not ours to edit."""
    f = (state.get("skill_files") or {}).get(name) or {}
    if f.get("plugin") or (not f and ":" in name):
        return plugin_step
    if f.get("path"):
        return f"{what} in {f['path']}"
    return f"{what} in its SKILL.md (find it with {AUDIT_CMD})"


def actions(state: dict, ignored_list: list[dict], missed_list: list[dict]) -> list[dict]:
    """Ranked to-do list (max 6). Each item names the exact next step; nothing is performed."""
    out: list[dict] = []
    a = state.get("audit") or {}
    days = _i((state.get("header") or {}).get("days")) or 7

    for x in ignored_list[:PER_SKILL_ACTIONS]:
        jev = f", average Jev confidence {x['avg_jev']:.2f}" if x.get("avg_jev") is not None else ""
        c, sl = _i(x.get("card")), _i(x.get("shortlist", x.get("shown")))
        out.append({"title": f"Check why {x['skill']} is never loaded",
                    "why": (f"Shown in {c + sl} lines ({c} as the route, {sl} in a shortlist), "
                            f"loaded 0{jev}; its description may not match, or it's a routing miss."),
                    "how": _edit_step(x["skill"], state, "check the description"),
                    "weight": 50 + c + sl / 2})
    for x in [m for m in missed_list if _i(m.get("times")) >= MISSED_MIN][:PER_SKILL_ACTIONS]:
        out.append({"title": f"Check the description of {x['skill']}",
                    "why": (f"Jev didn't suggest {x['skill']} on {x['times']} prompts where you "
                            "loaded it first."),
                    "how": _edit_step(x["skill"], state, "improve the description", PLUGIN_MISSED),
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
    loads = conf["outcomes"]["loads"]
    if loads < SOLID_AT:
        target = EARLY_AT if loads < EARLY_AT else SOLID_AT
        out.append({"title": f"Keep using it: {_days_to(loads, target, days)} to {target} loads",
                    "why": (f"The follow rate rests on only {loads} load{'s' if loads != 1 else ''} of a "
                            f"suggested skill; {EARLY_AT} are the minimum to read it, {SOLID_AT} to call it solid."),
                    "how": "nothing to run; the numbers fill in as you work",
                    "weight": 70 if conf["outcomes"]["n"] < EARLY_AT else 38})
    ho = conf["holdout"]["n"]
    if ho < EARLY_AT:
        out.append({"title": f"Holdout lift needs time: {_days_to(ho, EARLY_AT, days)}",
                    "why": (f"Only {ho} held-out prompt{'s' if ho != 1 else ''} with a test result "
                            f"— too early; 10 are needed before the lift means anything."),
                    "how": "keep running tests in your sessions; check back in python3 scripts/cli.py stats",
                    "weight": 20})

    out.sort(key=lambda x: -x["weight"])
    for x in out:
        x["weight"] = round(float(x["weight"]), 1)
    return out[:ACTIONS_MAX]
