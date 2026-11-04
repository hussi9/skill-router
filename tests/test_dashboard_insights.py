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
        "outcomes": {"summary": {"shown": shown, "followed_rate": followed / shown if shown else 0.0,
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
    assert ins["outcomes"] == ("Claude used 9% of suggested skills (5 of 57 lines): "
                               "5 from routes, 0 from shortlists — solid.")
    assert ins["routing"].endswith("— solid.") and "Of 132 prompts" in ins["routing"]
    assert "1,795" in ins["jev"] and "52%" in ins["jev"]
    assert "43 of 764" in ins["listing"]
    small = di.insights(_state(shown=4, followed=1))
    assert small["outcomes"].endswith("too early.")
    assert di.insights(_state(shown=12, followed=3))["outcomes"].endswith("early signal.")
    empty = di.insights({})
    assert all(isinstance(v, str) and v for v in empty.values())
    conf = di.confidence(_state())
    assert conf["holdout"] == {"n": 1, "badge": "too early"}
    assert conf["outcomes"] == {"n": 57, "badge": "solid"}


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
    conf = {("s", "a0"): 0.4, ("s", "a1"): 0.6}
    out = di.ignored(ev, 7, now=NOW, jev_conf=conf)
    assert out == [{"skill": "alpha-skill", "shown": 3, "loaded": 0, "avg_jev": 0.5}]
    many = [_shown(f"m{i}-{j}", card=[f"skill-{i}"]) for i in range(12) for j in range(3 + i)]
    big = di.ignored(many, 7, now=NOW)
    assert len(big) == 8 and big[0]["skill"] == "skill-11" and big[0]["shown"] == 14


def test_missed_counts_loads_without_matching_suggestion() -> None:
    ev = [
        _shown("p1", card=["alpha-skill"]), _load("p1", "alpha-skill"),       # matched → not missed
        _shown("p2", shortlist=["beta-skill"]), _load("p2", "gamma-skill"),    # other skill shown
        _load("p3", "gamma-skill"), _load("p3", "gamma-skill"),               # nothing shown; once per prompt
        _shown("p4", card=[], shortlist=[]), _load("p4", "delta-skill"),       # silent line
        _shown("p5", held_out=True), _load("p5", "zeta-skill"),               # held out → excluded
        _shown("p6", card=["eta-skill"]), _load("p6", "plug:eta-skill"),       # prefix match
        _load("", "theta-skill"),                                              # no prompt id
        _load("p7", "iota-skill", ts=NOW - 30 * 86400),                        # too old
    ]
    assert di.missed(ev, 7, now=NOW) == [{"skill": "gamma-skill", "times": 2},
                                         {"skill": "delta-skill", "times": 1}]


def test_actions_ranked_and_capped() -> None:
    ign = [{"skill": "alpha-skill", "shown": 11, "loaded": 0, "avg_jev": 0.43},
           {"skill": "beta-skill", "shown": 6, "loaded": 0, "avg_jev": None}]
    mis = [{"skill": "gamma-skill", "times": 4}, {"skill": "delta-skill", "times": 2}]
    acts = di.actions(_state(shown=4, followed=1), ign, mis)
    assert 1 <= len(acts) <= 6
    weights = [a["weight"] for a in acts]
    assert weights == sorted(weights, reverse=True)
    for a in acts:
        assert set(a) == {"title", "why", "how", "weight"}
        assert a["title"] and a["why"] and a["how"]
    titles = " | ".join(a["title"] for a in acts)
    assert acts[0]["title"].startswith("Keep using it ~")          # 4 lines: too early beats all
    assert "alpha-skill" in titles and "gamma-skill" in titles
    assert "beta-skill" not in titles and "delta-skill" not in titles   # one per list; the panel has the rest
    dup = next(a for a in acts if "duplicate" in a["title"])
    assert "gamma-skill ~ plug:gamma-skill" in dup["why"] and "cli.py audit" in dup["how"]
    thin = next(a for a in acts if "short skill description" in a["title"])
    assert "alpha-skill" in thin["how"] and "+23 more" in thin["how"]
    solid = di.actions(_state(shown=57, known_routed=20, known_held=20), [], [])
    assert not any(a["title"].startswith(("Keep using", "Holdout")) for a in solid)
    assert any("168 skills unused" in a["title"] and "cli.py audit" in a["how"] for a in solid)
    assert di.actions({}, [], []) and len(di.actions({}, [], [])) <= 6
