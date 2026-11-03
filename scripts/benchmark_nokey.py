#!/usr/bin/env python3
"""
benchmark_nokey.py — how well does the router do for someone with no API key?

Compares, on the same prompts, with every provider off (Jev, Gemini, Haiku):

    current      what v2.0 prints today: the lexical card's domain skill
    current+fix  the same router with typo correction on (layer 1)
    shortlist    layer 4 alone: up to 3 installed skills named for Claude to choose
    hybrid       card only when the lexical match is strong, else the shortlist

Inputs are case files that stay local (they hold real prompts):
    labeled  [{"prompt", "expected_skills": [...], "bucket": "real_task"|"real_no_skill"}]
    mined    [{"prompt", "skill"}]   (prompt -> skill Claude actually invoked)

Output: a summary with counts only, no prompt text.

    python3 scripts/benchmark_nokey.py --labeled cases.json --mined mined.json [--floor 6.0]
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

for k, v in {"SKILL_ROUTER_JEV": "0", "SKILL_ROUTER_LLM": "0", "SKILL_ROUTER_NO_EMBED": "1",
             "SKILL_ROUTER_NO_LEARN": "1", "SKILL_ROUTER_SHORTLIST": "1"}.items():
    os.environ[k] = v
if "--embed" in sys.argv:                  # score the keyless shortlist with the semantic daemon
    os.environ["SKILL_ROUTER_NO_EMBED"] = "0"
os.environ.pop("SKILL_ROUTER_HOOK_MODE", None)

import index_match  # noqa: E402  type: ignore[import-not-found]
import router  # noqa: E402  type: ignore[import-not-found]

# Not domain targets: the router itself, process skills (they ride as the second leg),
# and harness builtins. A mined pair naming one of these says nothing about layer 4.
SKIP_LABELS = {"skill-router", "loop", "artifact-design", "dataviz", "schedule", "update-config"}


def _carded(prompt: str) -> tuple[str, int]:
    """(first domain skill on the card or "", printed chars) from the real router.route()."""
    path, chain, _, text = router.route(prompt)
    names = [s.skill for s in chain] if chain else []
    first = names[0] if names else ""
    return first, len(text or "")


def _strong(prompt: str, bar: float) -> str:
    ranked = index_match.rank(prompt, limit=1)
    return ranked[0].name if ranked and ranked[0].score >= bar else ""


def evaluate(cases: list[dict], floor: float, strong_bar: float) -> dict:
    router.SHORTLIST_FLOOR = floor
    systems = {s: {"hit": 0, "shown": 0, "chars": []} for s in ("current", "current+fix", "shortlist", "hybrid")}
    controls = {s: 0 for s in systems}
    n_task = n_ctrl = 0
    for c in cases:
        prompt, expected = c["prompt"], set(c["expected"])
        is_ctrl = not expected
        n_ctrl += is_ctrl
        n_task += not is_ctrl
        for fuzzy, name in ((False, "current"), (True, "current+fix")):
            index_match.FUZZY = fuzzy
            with contextlib.redirect_stdout(io.StringIO()):
                pick, chars = _carded(prompt)
            r = systems[name]
            if pick:
                r["shown"] += 1
                r["chars"].append(chars)
                controls[name] += is_ctrl
                r["hit"] += pick in expected
        index_match.FUZZY = True
        path = index_match.detect_path(prompt)
        line = router.shortlist_line(prompt, path)
        names = line.split(": ", 1)[1].split(" · ") if line else []
        r = systems["shortlist"]
        if names:
            r["shown"] += 1
            r["chars"].append(len(line))
            controls["shortlist"] += is_ctrl
            r["hit"] += bool(expected & set(names))
        strong = _strong(prompt, strong_bar)
        r = systems["hybrid"]
        if strong:
            r["shown"] += 1
            r["chars"].append(200)                 # a card line is ~200 chars before the chain lines
            controls["hybrid"] += is_ctrl
            r["hit"] += strong in expected
        elif names:
            r["shown"] += 1
            r["chars"].append(len(line))
            controls["hybrid"] += is_ctrl
            r["hit"] += bool(expected & set(names))
    out = {"tasks": n_task, "controls": n_ctrl, "floor": floor, "strong_bar": strong_bar, "systems": {}}
    for s, r in systems.items():
        out["systems"][s] = {
            "right_skill_offered": r["hit"],
            "turns_with_output": r["shown"],
            "control_false_alarms": controls[s],
            "median_chars_when_shown": int(statistics.median(r["chars"])) if r["chars"] else 0,
        }
    return out


def evaluate_jev(cases: list[dict]) -> dict:
    """The shipped router with Jev on: card (>= 0.8) plus Jev's top-3 shortlist below the bar."""
    os.environ["SKILL_ROUTER_JEV"] = "1"
    os.environ["SKILL_ROUTER_LLM"] = "1"          # _jev_active reads both; Gemini stays a fallback only
    card_right = list_right = shown = ctrl_shown = n_task = 0
    chars: list[int] = []
    try:
        for c in cases:
            expected = set(c["expected"])
            n_task += bool(expected)
            with contextlib.redirect_stdout(io.StringIO()):
                path, chain, _, text = router.route(c["prompt"])
            names: list[str] = [s.skill for s in chain] if chain else []
            short: list[str] = []
            for ln in (text or "").splitlines():
                if ln.startswith((router.SHORTLIST_MARKER, router.LEGACY_SHORTLIST_MARKER)):
                    short = ln.split(": ", 1)[1].split(" · ")
            if names or short:
                shown += 1
                chars.append(len(text or ""))
                ctrl_shown += not expected
            card_right += bool(names) and names[0] in expected
            list_right += bool(short) and bool(expected & set(short))
    finally:
        os.environ["SKILL_ROUTER_JEV"] = "0"
        os.environ["SKILL_ROUTER_LLM"] = "0"
    return {"tasks": n_task, "card_right": card_right, "shortlist_right_in_3": list_right,
            "right_either": card_right + list_right, "turns_with_output": shown,
            "control_false_alarms": ctrl_shown, "median_chars_when_shown": int(statistics.median(chars)) if chars else 0}


def load(labeled: str | None, mined: str | None) -> dict[str, list[dict]]:
    sets: dict[str, list[dict]] = {}
    if labeled:
        sets["labeled"] = [{"prompt": c["prompt"], "expected": c.get("expected_skills") or []}
                           for c in json.loads(Path(labeled).read_text())]
    if mined:
        rows = []
        for c in json.loads(Path(mined).read_text()):
            s = c.get("skill", "")
            if not c.get("in_index") or s in SKIP_LABELS or s.startswith("superpowers:"):
                continue
            rows.append({"prompt": c["prompt"], "expected": [s, s.split(":")[-1]]})
        sets["mined"] = rows
    return sets


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labeled")
    ap.add_argument("--mined")
    ap.add_argument("--floor", type=float, nargs="*", default=[router.SHORTLIST_FLOOR])
    ap.add_argument("--strong-bar", type=float, default=12.0)
    ap.add_argument("--out")
    ap.add_argument("--embed", action="store_true", help="use the running embedding daemon for the shortlist")
    ap.add_argument("--jev", action="store_true", help="also score the shipped router with Jev on (spends API calls)")
    a = ap.parse_args()
    sets = load(a.labeled, a.mined)
    report = {name: [evaluate(cases, f, a.strong_bar) for f in a.floor] for name, cases in sets.items()}
    if a.jev:
        report["jev_shipped"] = {name: evaluate_jev(cases) for name, cases in sets.items()}
    text = json.dumps(report, indent=1)
    if a.out:
        Path(a.out).write_text(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
