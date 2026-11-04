"""Dashboard insights: sample badges, sentences with numbers, ignored/missed lists, ranked actions."""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import dashboard_insights as di  # type: ignore[import-not-found]  # noqa: E402

NOW = 1_800_000_000.0


def _shown(pid: str, card=(), shortlist=(), held_out=False, ts=NOW - 60) -> dict:
    return {"type": "shown", "session_id": "s", "prompt_id": pid, "card": list(card),
            "shortlist": list(shortlist), "held_out": held_out, "ts": ts}


def _load(pid: str, skill: str, ts=NOW - 50) -> dict:
    return {"type": "signal", "kind": "loaded", "session_id": "s", "prompt_id": pid,
            "skill": skill, "ts": ts}


def _state(shown=57, followed=5, known_routed=1, known_held=1, decisions=1795) -> dict:
    return {
        "header": {"days": 7},
        "jev": {"decisions": decisions, "route_rate": 0.52, "median_ms": 461.0, "failures": 6},
        "routing": {"cards": 15, "shortlists": 42, "silent": 72, "held_out": 3},
        "outcomes": {"summary": {"shown": shown, "followed": followed,
                                 "followed_rate": followed / shown if shown else 0.0,
                                 "loaded_from_card": followed, "loaded_from_shortlist": 0},
                     "lift": {"routed": {"success": 0, "known": known_routed},
                              "held_out": {"success": 0, "known": known_held}, "lift": 0.0}},
        "listing": {"entries": 764, "described": 43, "name_only": 721},
        "audit": {"score": 48, "skills": 437, "unused": 168, "overlaps": 3, "thin": 25,
                  "thin_names": ["alpha-skill", "beta-skill"],
                  "duplicates": [["gamma-skill", "plug:gamma-skill"]]},
        "visibility": {"projects": 4, "worthwhile": 2, "unused_skills": 169},
    }


def test_badge_thresholds() -> None:
    assert [di.badge(n) for n in (0, 9, 10, 29, 30, 500)] == [
        "too early", "too early", "early signal", "early signal", "solid", "solid"]


def test_insight_sentences_carry_numbers_and_badges() -> None:
    ins = di.insights(_state())
    assert set(ins) == {"jev", "routing", "outcomes", "listing", "audit", "visibility"}
    for k, sentence in ins.items():
        assert re.search(r"\d", sentence), k
        assert sentence.endswith("."), k
    # 57 lines but only 5 loads: the badge follows the rarer side, capped at "early signal"
    assert ins["outcomes"] == ("Claude used a suggested skill on 9% of lines (5 of 57): "
                               "5 loads from routes, 0 from shortlists — early signal.")
    assert ins["routing"].endswith("— solid.") and "Of 132 prompts" in ins["routing"]
    assert "1,795" in ins["jev"] and "52%" in ins["jev"]
    assert "43 of 764" in ins["listing"]
    assert di.insights(_state(shown=4, followed=1))["outcomes"].endswith("too early.")
    assert di.insights(_state(shown=57, followed=12))["outcomes"].endswith("early signal.")
    assert di.insights(_state(shown=57, followed=40))["outcomes"].endswith("solid.")
    empty = di.insights({})
    assert all(isinstance(v, str) and v for v in empty.values())
    conf = di.confidence(_state())
    assert conf["holdout"] == {"n": 1, "badge": "too early"}
    assert conf["outcomes"] == {"n": 57, "loads": 5, "badge": "early signal"}


def test_ignored_requires_three_shows_and_zero_loads() -> None:
    ev = []
    for i in range(3):                                   # alpha: 3 shows, never loaded → ignored
        ev.append(_shown(f"a{i}", card=["alpha-skill"]))
    for i in range(2):                                   # beta: 2 shows only → not ignored
        ev.append(_shown(f"b{i}", shortlist=["beta-skill"]))
    for i in range(4):                                   # gamma: 4 shows, loaded once → not ignored
        ev.append(_shown(f"g{i}", shortlist=["gamma-skill"]))
    ev.append(_load("g0", "gamma-skill"))
    for i in range(5):                                   # delta: loaded via a plugin prefix → not ignored
        ev.append(_shown(f"d{i}", card=["delta-skill"]))
    ev.append(_load("d0", "plug:delta-skill"))
    ev.append(_shown("h", card=["alpha-skill"], held_out=True))   # held out: printed nothing
    ev += [_shown(f"o{i}", card=["epsilon-skill"], ts=NOW - 30 * 86400) for i in range(5)]  # too old
    for i in range(4):                                   # kappa: 4 shortlist-only lines
        ev.append(_shown(f"k{i}", shortlist=["kappa-skill"]))
    conf = {("s", "a0"): 0.4, ("s", "a1"): 0.6}
    out = di.ignored(ev, 7, now=NOW, jev_conf=conf)
    # alpha (3 route lines) outranks kappa (4 shortlist-only lines): a shortlist mention weighs half
    assert out == [{"skill": "alpha-skill", "shown": 3, "card": 3, "shortlist": 0, "loaded": 0, "avg_jev": 0.5},
                   {"skill": "kappa-skill", "shown": 4, "card": 0, "shortlist": 4, "loaded": 0, "avg_jev": None}]
    many = [_shown(f"m{i}-{j}", card=[f"skill-{i}"]) for i in range(12) for j in range(3 + i)]
    big = di.ignored(many, 7, now=NOW)
    assert len(big) == 8 and big[0]["skill"] == "skill-11" and big[0]["shown"] == 14
    assert len(di.ignored(many, 7, now=NOW, limit=None)) == 12      # true total for the page


def _prompt(pid: str) -> dict:
    return {"type": "prompt", "session_id": "s", "prompt_id": pid, "ts": NOW - 60}


def test_missed_counts_loads_without_matching_suggestion() -> None:
    ev = [
        _shown("p1", card=["alpha-skill"]), _load("p1", "alpha-skill"),       # matched → not missed
        _shown("p2", shortlist=["beta-skill"]), _load("p2", "gamma-skill"),    # other skill shown
        _prompt("p3"), _load("p3", "gamma-skill"), _load("p3", "gamma-skill"),  # routed silently
        _shown("p4", card=[], shortlist=[]), _load("p4", "delta-skill"),       # silent line
        _shown("p5", held_out=True), _load("p5", "zeta-skill"),               # held out → excluded
        _shown("p6", card=["eta-skill"]), _load("p6", "plug:eta-skill"),       # prefix match
        _load("p8", "lambda-skill"),                                           # router stood down (/command)
        _prompt("p9"), _load("p9", "gamma-skill", ts=NOW - 50),                # first load counts…
        _load("p9", "mu-skill", ts=NOW - 40),                                  # …the chained one does not
        _load("", "theta-skill"),                                              # no prompt id
        _load("p7", "iota-skill", ts=NOW - 30 * 86400),                        # too old
    ]
    assert di.missed(ev, 7, now=NOW) == [{"skill": "gamma-skill", "times": 3},
                                         {"skill": "delta-skill", "times": 1}]
    assert len(di.missed(ev, 7, now=NOW, limit=1)) == 1


def test_actions_ranked_and_capped() -> None:
    ign = [{"skill": "plug:alpha-skill", "shown": 11, "card": 2, "shortlist": 9, "loaded": 0, "avg_jev": 0.43},
           {"skill": "beta-skill", "shown": 6, "card": 6, "shortlist": 0, "loaded": 0, "avg_jev": None}]
    mis = [{"skill": "gamma-skill", "times": 4}, {"skill": "delta-skill", "times": 2}]
    st = _state(shown=4, followed=1)
    st["skill_files"] = {"plug:alpha-skill": {"path": None, "plugin": True},
                         "gamma-skill": {"path": "~/skills/gamma-skill/SKILL.md", "plugin": False}}
    acts = di.actions(st, ign, mis)
    assert 1 <= len(acts) <= 6
    weights = [a["weight"] for a in acts]
    assert weights == sorted(weights, reverse=True)
    for a in acts:
        assert set(a) == {"title", "why", "how", "weight"}
        assert a["title"] and a["why"] and a["how"]
    titles = " | ".join(a["title"] for a in acts)
    assert acts[0]["title"].startswith("Keep using it")              # 4 lines: too early beats all
    assert "unknown (fewer than 2 samples)" in acts[0]["title"]      # 1 load is no rate
    assert "beta-skill" not in titles and "delta-skill" not in titles   # one per list; the panel has the rest
    ig = next(a for a in acts if "alpha-skill" in a["title"])
    assert "11 lines (2 as the route, 9 in a shortlist)" in ig["why"]
    assert ig["how"].startswith("plugin skill — hide it per project (skillOverrides)")
    mi = next(a for a in acts if "gamma-skill" in a["title"])
    assert mi["why"] == "Jev didn't suggest gamma-skill on 4 prompts where you loaded it first."
    assert mi["how"] == "improve the description in ~/skills/gamma-skill/SKILL.md"
    assert not any("epsilon-skill" in a["title"]
                   for a in di.actions(st, [], [{"skill": "epsilon-skill", "times": 1}]))   # N >= 2 only
    dup = next(a for a in acts if "duplicate" in a["title"])
    assert "gamma-skill ~ plug:gamma-skill" in dup["why"] and "cli.py audit" in dup["how"]
    thin = next(a for a in acts if "short skill description" in a["title"])
    assert "alpha-skill" in thin["how"] and "+23 more" in thin["how"]
    early = di.actions(_state(shown=57, followed=5), [], [])
    assert any(a["title"] == "Keep using it: ~7 more days to 10 loads" for a in early)
    slow_state = _state(shown=57, followed=12)
    slow_state["header"]["days"] = 30                               # 12 loads in 30 days
    slow = di.actions(slow_state, [], [])
    assert any(a["title"] == "Keep using it: 30+ days to 30 loads" for a in slow)
    solid = di.actions(_state(shown=57, followed=30, known_routed=20, known_held=20), [], [])
    assert not any(a["title"].startswith(("Keep using", "Holdout")) for a in solid)
    assert any("168 skills unused" in a["title"] and "cli.py audit" in a["how"] for a in solid)
    assert di.actions({}, [], []) and len(di.actions({}, [], [])) <= 6
