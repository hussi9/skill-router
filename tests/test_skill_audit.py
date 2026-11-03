#!/usr/bin/env python3
"""
Tests for the skill audit engine (scripts/skill_audit.py).

Synthetic catalogs only: every skill name here is made up. Nothing reads the
developer's ~/.claude; load_inputs is pointed at temp files. The Jev path is
exercised with fake callables, so the suite stays offline.

Run: python3 -m pytest tests/test_skill_audit.py -q
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import outcomes  # type: ignore[import-not-found]  # noqa: E402
import skill_audit as sa  # type: ignore[import-not-found]  # noqa: E402


OLD = time.time() - 200 * 86400          # installed long enough ago to judge


def entry(name: str, description: str, kind: str = "domain", type_: str = "skill",
          installed_at: float | None = OLD) -> dict:
    e = {"name": name, "description": description, "kind": kind, "type": type_}
    if installed_at is not None:
        e["installed_at"] = installed_at
    return e


DISTINCT = [
    entry("alpha-tool", "render vector diagrams from plain text outlines"),
    entry("beta-tool", "migrate postgres schemas between staging environments safely"),
    entry("gamma-tool", "compose release notes from merged pull requests"),
    entry("delta-tool", "translate subtitles into twelve spoken languages"),
]


def run(catalog, usage=None, history_days=0, outcomes_in=None, skills_sh=None, jev=None, **kw):
    return sa.audit(catalog, usage or {}, history_days, outcomes_in, skills_sh, jev=jev, **kw)


# ---- listing tokens --------------------------------------------------------

def test_listing_tokens_counts_descriptions():
    catalog = [entry("abcd", "x" * 96), entry("efgh", "y" * 196),
               entry("builtin-thing", "z" * 4000, type_="builtin"),
               entry("cmd-thing", "z" * 4000, type_="plugin-command"),
               entry("an-agent", "z" * 4000, type_="agent")]
    r = run(catalog)
    assert r.skills == 2                       # builtins, commands and agents are not routable
    assert r.listing_tokens == (4 + 96 + 4 + 196) // 4


# ---- listing the harness actually sends -------------------------------------

def test_budget_from_fraction(monkeypatch):
    monkeypatch.delenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", raising=False)
    assert sa.listing_budget({}) == 8000                       # 1% of 200k tokens x 4 chars
    assert sa.listing_budget({"skillListingBudgetFraction": 0.02}, 100_000) == 8000
    assert sa.listing_budget({"skillListingBudgetFraction": "junk"}) == 8000


def test_env_char_budget_wins(monkeypatch):
    monkeypatch.setenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", "1234")
    assert sa.listing_budget({"skillListingBudgetFraction": 0.5}) == 1234
    monkeypatch.setenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", "1234.9")
    assert sa.listing_budget({}) == 1234
    for bad in ("nope", "nan", "inf", "-5", "0", ""):
        monkeypatch.setenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", bad)
        assert sa.listing_budget({}) == 8000


def test_desc_cap_applied(monkeypatch):
    monkeypatch.setenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", "1000000")
    catalog = [entry("abcd", "x" * 5000)]
    r = run(catalog)
    assert r.listing_tokens == (4 + 5000) // 4                 # raw, uncapped
    assert r.listing_sent_tokens == (4 + 1536) // 4
    r = run(catalog, settings={"skillListingMaxDescChars": 100})
    assert r.listing_sent_tokens == (4 + 100) // 4
    assert r.descriptions_dropped == 0


def test_least_used_lose_descriptions_first(monkeypatch):
    monkeypatch.setenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", "700")
    catalog = [entry(n, "d" * 296) for n in ("aaaa", "bbbb", "cccc", "dddd")]   # 300 chars each
    r = run(catalog, {"cccc": 5, "dddd": 2, "bbbb": 2})
    # 1200 chars > 700: drop aaaa (unused), then bbbb (tie with dddd, by name) -> 4+4+300+300 = 608
    assert r.descriptions_dropped == 2
    assert r.listing_sent_tokens == (4 + 4 + 300 + 300) // 4
    assert r.listing_budget_tokens == 700 // 4


def test_listing_deduction_follows_dropped_descriptions(monkeypatch):
    monkeypatch.setenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", "700")
    catalog = [entry(n, "d" * 296) for n in ("aaaa", "bbbb", "cccc", "dddd")]
    r = run(catalog, {n["name"]: 1 for n in catalog})
    assert r.descriptions_dropped == 2 and r.score == 100 - round(2 / 4 * 40)
    assert run(DISTINCT, {n["name"]: 1 for n in DISTINCT}).score == 100   # raw size alone: free


def test_thin_descriptions_counted():
    catalog = [entry("one-w", "short"), entry("two-w", "just four tiny words"),
               entry("ok-one", "render vector diagrams from plain text outlines"),
               entry("a-cmd", "x", type_="agent")]
    assert run(catalog).thin_descriptions == 2


def test_load_settings_later_wins(tmp_path):
    user = tmp_path / "user.json"
    user.write_text(json.dumps({"skillListingMaxDescChars": 10, "skillListingBudgetFraction": 0.5}))
    proj = tmp_path / "proj" / ".claude"
    proj.mkdir(parents=True)
    (proj / "settings.json").write_text(json.dumps({"skillListingMaxDescChars": 20}))
    (proj / "settings.local.json").write_text(json.dumps({"skillListingMaxDescChars": 30}))
    got = sa.load_settings(tmp_path / "proj", user_path=user)
    assert got == {"skillListingMaxDescChars": 30, "skillListingBudgetFraction": 0.5}
    assert sa.load_settings(tmp_path / "none", user_path=tmp_path / "missing.json") == {}


# ---- usage history ---------------------------------------------------------

def test_no_history_means_no_unused_verdict():
    usage = {"alpha-tool": 3}
    short = run(DISTINCT, usage, history_days=12)
    assert short.unused_90d == []
    assert short.history_days == 12
    long = run(DISTINCT, usage, history_days=95)
    assert long.unused_90d == ["beta-tool", "delta-tool", "gamma-tool"]


def test_plugin_and_recent_skills_are_never_unused():
    catalog = DISTINCT + [
        entry("plug:one", "index chat transcripts for semantic recall", type_="plugin-skill"),
        entry("plug:two", "export memory snapshots to markdown files", type_="plugin-skill"),
        entry("kit:three", "audit cloud billing anomalies weekly", type_="plugin-skill"),
        entry("fresh-tool", "scaffold new browser extensions quickly",
              installed_at=time.time() - 10 * 86400)]
    r = run(catalog, {"alpha-tool": 1, "plug:one": 2}, history_days=200)
    assert r.unused_90d == ["beta-tool", "delta-tool", "gamma-tool"]
    assert r.plugin_usage == [("kit", 0, 1), ("plug", 1, 2)]
    assert r.age_unknown == 0


def test_unknown_install_age_is_excluded_and_counted(tmp_path):
    old_dir = tmp_path / "old-skill"
    old_dir.mkdir()
    (old_dir / "SKILL.md").write_text("x")
    os.utime(old_dir / "SKILL.md", (OLD, OLD))
    new_file = tmp_path / "NEW.md"
    new_file.write_text("x")
    catalog = [
        {"name": "no-age", "description": "plan sprint capacity", "kind": "domain", "type": "skill"},
        {"name": "gone-path", "description": "lint yaml", "kind": "domain", "type": "skill",
         "path": str(tmp_path / "missing" / "SKILL.md")},
        {"name": "old-skill", "description": "draw charts", "kind": "domain", "type": "skill",
         "path": str(old_dir)},
        {"name": "new-skill", "description": "write poems", "kind": "domain", "type": "skill",
         "path": str(new_file)}]
    r = run(catalog, {}, history_days=200)
    assert r.unused_90d == ["old-skill"]
    assert r.age_unknown == 2


def test_namespaced_usage_counts_for_bare_and_full_names():
    catalog = [entry("plug:alpha-tool", "render vector diagrams from plain text outlines"),
               entry("beta-tool", "migrate postgres schemas between staging environments safely")]
    r = run(catalog, {"alpha-tool": 1, "plug:beta-tool": 2}, history_days=90)
    assert r.unused_90d == []


# ---- outcomes --------------------------------------------------------------

def test_shown_never_loaded_needs_five_shows():
    outcomes_in = {"alpha-tool": {"shown": 5, "loaded": 0},
                   "beta-tool": {"shown": 4, "loaded": 0},
                   "gamma-tool": {"shown": 9, "loaded": 1}}
    assert run(DISTINCT, outcomes_in=outcomes_in).shown_never_loaded == ["alpha-tool"]
    assert run(DISTINCT, outcomes_in=None).shown_never_loaded == []


def test_outcomes_per_skill_counts_shown_and_loaded(tmp_path, monkeypatch):
    log = tmp_path / "outcomes.jsonl"
    monkeypatch.setenv("SKILL_ROUTER_OUTCOMES_FILE", str(log))
    monkeypatch.delenv("SKILL_ROUTER_NO_LEARN", raising=False)
    for i in range(3):
        outcomes.record_shown("s1", f"p{i}", ["alpha-tool"], ["beta-tool"], "keyless")
    outcomes.record_shown("s1", "p9", ["gamma-tool"], [], "keyless", held_out=True)
    outcomes.record_signal("s1", "p0", "loaded", "alpha-tool")
    per = outcomes.per_skill(90)
    assert per["alpha-tool"] == {"shown": 3, "loaded": 1}
    assert per["beta-tool"] == {"shown": 3, "loaded": 0}
    assert "gamma-tool" not in per             # held-out turns printed nothing


# ---- overlaps --------------------------------------------------------------

TWINS = [
    entry("lint-js", "lint javascript files and fix style errors automatically"),
    entry("lint-ts", "lint javascript files and fix style errors quickly"),
    entry("near-a", "deploy static websites to edge hosting with previews"),
    entry("near-b", "deploy static websites to cloud buckets and invalidate caches"),
    entry("other-kind", "lint javascript files and fix style errors automatically", kind="design"),
]


def test_overlap_keyless_and_jev_paths():
    keyless = run(TWINS)
    pairs = {(a, b): (s, src) for a, b, s, src in keyless.overlaps}
    assert set(pairs) == {("lint-js", "lint-ts")}           # other kind never pairs
    assert pairs[("lint-js", "lint-ts")][1] == "keyword"

    asked: list[list[tuple[str, str]]] = []

    def fake_jev(batch):
        asked.append(list(batch))
        return {p: (0.9 if p == ("near-a", "near-b") else 0.1) for p in batch}

    judged = run(TWINS, jev=fake_jev)
    got = {(a, b): src for a, b, _, src in judged.overlaps}
    assert got == {("near-a", "near-b"): "jev"}              # jev overrules the keyword twin
    assert all(len(b) <= 20 for b in asked)
    flat = [p for b in asked for p in b]
    assert ("near-a", "near-b") in flat and ("lint-js", "lint-ts") in flat


def test_jev_batches_cap_at_twenty_and_two_hundred_pairs():
    catalog = [entry(f"clone-{i:03d}", "summarise meeting transcripts into action items list")
               for i in range(25)]                           # 300 identical pairs
    asked: list[int] = []

    def fake_jev(batch):
        asked.append(len(batch))
        return {p: 0.95 for p in batch}

    r = run(catalog, jev=fake_jev)
    assert max(asked) <= 20 and sum(asked) == 200
    assert sum(1 for o in r.overlaps if o[3] == "jev") == 200
    assert sum(1 for o in r.overlaps if o[3] == "keyword") == 100


def test_jev_failure_falls_back_to_keyword_overlap():
    def broken(batch):
        raise RuntimeError("network down")

    r = run(TWINS, jev=broken)
    assert [(a, b, src) for a, b, _, src in r.overlaps] == [("lint-js", "lint-ts", "keyword")]

    def empty(batch):
        return {}

    r = run(TWINS, jev=empty)
    assert [(a, b, src) for a, b, _, src in r.overlaps] == [("lint-js", "lint-ts", "keyword")]

    def slow(batch):
        time.sleep(0.05)
        return {p: 0.0 for p in batch}

    r = run(TWINS, jev=slow, budget_s=0.0)                   # budget gone before the first call
    assert [(a, b, src) for a, b, _, src in r.overlaps] == [("lint-js", "lint-ts", "keyword")]


def test_default_jev_without_key_is_none(monkeypatch):
    monkeypatch.setattr(sa.jev_choose, "_key", lambda: "")
    assert sa.make_jev(TWINS) is None


def test_default_jev_builds_noul_body_and_reads_answers(monkeypatch):
    monkeypatch.setattr(sa.jev_choose, "_key", lambda: "k")
    sent: list[dict] = []

    def fake_post(body, key, timeout):
        sent.append(body)
        return {"answers": {qid: {"type": "noul", "noul": 0.85} for qid in body["questions"]}}

    monkeypatch.setattr(sa.jev_choose, "_post", fake_post)
    jev = sa.make_jev(TWINS)
    out = jev([("near-a", "near-b")])
    assert out == {("near-a", "near-b"): 0.85}
    body = sent[0]
    assert body["model"] == sa.jev_choose.MODEL
    assert body["state"]["pairs"]["p0"]["a"].startswith("deploy static websites")
    q = body["questions"]["p0"]
    assert q["type"] == "noul" and set(q["criteria"]) == {"true", "false"}


# ---- flagged ---------------------------------------------------------------

def test_flagged_lists_installed_names_only():
    sh = {"catalogs": {"skills.sh": [{"name": "beta-tool", "audit": "clean"}]},
          "metadata": {"flagged": [{"source": "o/r", "skill": "alpha-tool"},
                                   {"source": "o/r", "skill": "not-installed"}]}}
    r = run(DISTINCT, {e["name"]: 1 for e in DISTINCT}, history_days=90, skills_sh=sh)
    assert r.flagged == ["alpha-tool"]
    assert r.score == 100                                     # a name collision costs nothing
    assert "same name as a flagged public skill" in r.top_actions[0]
    assert run(DISTINCT, skills_sh=None).flagged == []


# ---- score -----------------------------------------------------------------

def test_score_deductions_are_capped():
    clean = run(DISTINCT, {n["name"]: 1 for n in DISTINCT}, history_days=90)
    assert clean.score == 100 and clean.top_actions == []

    many = [entry(f"skill-{i:03d}", f"{'w' * 4000} unique{i}") for i in range(40)]
    clones = [entry(f"dup-{i}", "summarise meeting transcripts into action items list")
              for i in range(10)]
    sh = {"catalogs": {"skills.sh": [{"name": "skill-000", "audit": "flagged"},
                                     {"name": "skill-001", "audit": "flagged"}]}}
    worst = run(many + clones, {}, history_days=120, skills_sh=sh)
    assert worst.score == 25                                  # 30 + 25 + 20, all capped; flagged free
    assert len(worst.top_actions) == 3

    # unused share alone: 3 of 4 unused -> 0.75 * 60 = 45, capped at 30
    only_unused = run(DISTINCT, {"alpha-tool": 1}, history_days=90)
    assert only_unused.score == 70
    assert only_unused.top_actions[0].startswith("3 skills show no logged Skill-tool use")
    assert "not tracked" in only_unused.top_actions[0]


def test_no_top_action_presents_an_uncapped_token_figure():
    r = run(DISTINCT, {"alpha-tool": 1}, history_days=90)
    for a in r.top_actions:
        assert "listing tokens" not in a
        assert not re.search(r"\d+\s+(listing )?tokens", a) or "uncapped" in a


def test_top_actions_lead_with_largest_deduction():
    catalog = DISTINCT + TWINS[:2]
    sh = {"catalogs": {"skills.sh": [{"name": "alpha-tool", "audit": "flagged"}]}}
    usage = {e["name"]: 1 for e in catalog}
    many = [entry(f"big-{i}", f"{'q' * 8000} solo{i}") for i in range(6)]   # ~4000 tokens over
    r = run(catalog + many, {**usage, **{e["name"]: 1 for e in many}}, history_days=90,
            skills_sh=sh)
    with_desc = sum(1 for e in catalog + many if e["description"])
    listing = round(r.descriptions_dropped / with_desc * 40)
    assert listing > 3 and r.score == 100 - listing - 3
    assert "lose their description" in r.top_actions[0]
    assert f"{r.descriptions_dropped} of {with_desc} skills" in r.top_actions[0]
    assert "overlap" in r.top_actions[1].lower()
    assert "flagged public skill" in r.top_actions[2]


# ---- scale -----------------------------------------------------------------

def test_audit_runs_on_1200_synthetic_skills_under_2s():
    rng = random.Random(7)
    vocab = [f"word{i}" for i in range(600)]
    kinds = ["domain", "design", "process", "meta"]
    catalog = [entry(f"syn-{i:04d}", " ".join(rng.sample(vocab, 12)), kind=rng.choice(kinds))
               for i in range(1200)]
    start = time.perf_counter()
    r = run(catalog, {}, history_days=90)
    assert time.perf_counter() - start < 2.0
    assert r.skills == 1200


# ---- inputs ----------------------------------------------------------------

def test_load_inputs_reads_files_and_tolerates_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SKILL_ROUTER_OUTCOMES_FILE", str(tmp_path / "none.jsonl"))
    missing = sa.load_inputs(index_path=tmp_path / "a", learned_path=tmp_path / "b",
                             usage_log=tmp_path / "c", skills_sh_path=tmp_path / "d",
                             catalog_path=tmp_path / "e")
    assert missing == ([], {}, 0, None, None)

    (tmp_path / "index.json").write_text(json.dumps({"entries": DISTINCT}))
    (tmp_path / "learned.json").write_text(json.dumps(
        {"per_skill": {"alpha-tool": {"invocations": 4}}, "window_days": 60}))
    old = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 40 * 86400))
    new = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 86400))
    (tmp_path / "usage.log").write_text(f"{old} gamma-tool\n{new}\tbeta-tool\n")
    sh = {"catalogs": {"skills.sh": []},
          "metadata": {"flagged": [{"source": "o/r", "skill": "alpha-tool"}]}}
    (tmp_path / "sh.json").write_text(json.dumps(sh))
    monkeypatch.setenv("SKILL_ROUTER_SKILLS_SH_FILE", str(tmp_path / "sh.json"))
    catalog, usage, days, outs, skills_sh = sa.load_inputs(
        index_path=tmp_path / "index.json", learned_path=tmp_path / "learned.json",
        usage_log=tmp_path / "usage.log", catalog_path=tmp_path / "none.json")
    assert [e["name"] for e in catalog] == [e["name"] for e in DISTINCT]
    assert usage == {"alpha-tool": 4, "gamma-tool": 1, "beta-tool": 1}
    assert days == 40
    assert outs is None                                       # no outcomes log
    assert skills_sh == sh
    r = sa.audit(catalog, usage, days, outs, skills_sh, jev=None)
    assert r.unused_90d == ["delta-tool"] and r.flagged == ["alpha-tool"]


def test_load_inputs_attaches_skill_paths_for_install_age(tmp_path, monkeypatch):
    monkeypatch.setenv("SKILL_ROUTER_OUTCOMES_FILE", str(tmp_path / "none.jsonl"))
    skill_md = tmp_path / "skills" / "beta-tool" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("x")
    bare = [{k: v for k, v in e.items() if k != "installed_at"} for e in DISTINCT]
    (tmp_path / "index.json").write_text(json.dumps({"entries": bare}))
    (tmp_path / "catalog.json").write_text(json.dumps(
        {"entries": [{"name": "beta-tool", "source_path": str(skill_md)}]}))
    catalog, *_ = sa.load_inputs(index_path=tmp_path / "index.json",
                                 learned_path=tmp_path / "x", usage_log=tmp_path / "y",
                                 skills_sh_path=tmp_path / "z",
                                 catalog_path=tmp_path / "catalog.json")
    assert {e["name"]: e.get("path") for e in catalog}["beta-tool"] == str(skill_md)
    r = sa.audit(catalog, {}, 200, None, None)
    assert r.unused_90d == [] and r.age_unknown == 3          # beta-tool is too new


def test_jev_call_timeout_is_capped_by_remaining_budget():
    seen: list[float] = []

    def judge(batch, timeout):
        seen.append(timeout)
        return {p: 0.0 for p in batch}
    judge.takes_timeout = True
    run(TWINS, jev=judge, budget_s=0.5)
    assert seen and all(0 < t <= 0.5 for t in seen)
    seen.clear()
    run(TWINS, jev=judge, budget_s=60)
    assert seen and all(t == sa.JEV_CALL_TIMEOUT_S for t in seen)


def test_tiny_token_sets_are_not_keyword_overlaps():
    cat = [{"name": n, "kind": "skill", "description": "alpha beta"} for n in ("x1", "x2")]
    assert sa._jaccard_pairs(cat, 0.6) == []
    long = "alpha beta gamma delta epsilon zeta"
    cat = [{"name": n, "kind": "skill", "description": long} for n in ("y1", "y2")]
    assert len(sa._jaccard_pairs(cat, 0.6)) == 1
