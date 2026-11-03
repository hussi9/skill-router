#!/usr/bin/env python3
"""
Tests for the per-project visibility plan (scripts/visibility.py).

Synthetic catalogs and usage only; every skill name here is made up. The listing
budget is pinned with SLASH_COMMAND_TOOL_CHAR_BUDGET so the arithmetic is exact.

Run: python3 -m pytest tests/test_visibility.py -q
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import project_usage as pu  # type: ignore[import-not-found]  # noqa: E402
import skill_audit as sa  # type: ignore[import-not-found]  # noqa: E402
import visibility as vis  # type: ignore[import-not-found]  # noqa: E402


def sk(name: str, type_: str = "skill", desc_len: int = 98) -> dict:
    return {"name": name, "description": "d" * desc_len, "type": type_, "kind": "domain"}


def usage_for(proj: Path, project_skills: dict[str, int], global_usage: dict[str, int],
              joinable: bool = True) -> dict:
    entry = {"dir": str(proj) if joinable else None, "joinable": joinable, "ambiguous": False,
             "skills": dict(project_skills),
             "last_used": {n: "2026-11-01T00:00:00Z" for n in project_skills}}
    return {"global": dict(global_usage), "projects": {pu.project_key(proj): entry}}


@pytest.fixture
def proj(tmp_path: Path) -> Path:
    p = tmp_path / "work" / "app"
    p.mkdir(parents=True)
    return p


@pytest.fixture
def budget(monkeypatch):
    def set_(chars: int) -> None:
        monkeypatch.setenv("SLASH_COMMAND_TOOL_CHAR_BUDGET", str(chars))
    return set_


# Ten user skills of 100 chars each (2-char name + 98-char description).
TEN = [sk(f"u{i}") for i in range(10)]
# Globally, u2..u9 are used more than u0/u1, so the harness drops u0/u1 first.
GLOBAL = {f"u{i}": i for i in range(2, 10)}


def test_project_used_skills_keep_descriptions(proj, budget):
    budget(600)
    p = vis.plan(proj, TEN, usage_for(proj, {"u0": 3, "u1": 1}, GLOBAL), {})
    assert p.applicable and p.simulated
    assert p.before["project_used_total"] == 2 and p.before["project_used_kept"] == 0
    assert p.after["project_used_kept"] == 2 == p.after["project_used_total"]
    assert {"u0", "u1"} <= set(p.keep)
    assert not {"u0", "u1"} & set(p.name_only)
    assert 0 < len(p.name_only) < 8
    assert p.after["dropped"] <= p.before["dropped"]
    assert not set(p.keep) & set(p.name_only)


def test_recently_used_in_project_is_never_hidden(proj, budget):
    budget(250)                                    # too small to ever fit everything
    used = {f"u{i}": 1 for i in range(6)}
    p = vis.plan(proj, TEN, usage_for(proj, used, {}), {})
    assert not set(used) & set(p.name_only)
    assert set(p.name_only) <= {"u6", "u7", "u8", "u9"}


def test_plugin_skills_never_overridden(proj, budget):
    budget(600)
    catalog = TEN + [sk(f"px:p{i}", "plugin-skill") for i in range(4)]
    p = vis.plan(proj, catalog, usage_for(proj, {"u0": 2, "px:p1": 1}, GLOBAL), {})
    assert not [n for n in p.name_only if n.startswith("px:")]
    assert p.plugin_suggestions == [("px", 1, 4)]


def test_jev_rank_raises_relevance(proj, budget):
    budget(900)                                    # one description must go
    flat = {f"u{i}": 1 for i in range(2, 10)}
    use = usage_for(proj, {"u0": 1}, flat)
    keyless = vis.plan(proj, TEN, use, {})
    assert keyless.name_only and keyless.name_only[0] == "u1"
    jev = vis.plan(proj, TEN, use, {}, jev_ranked={"u1": 2})
    assert "u1" not in jev.name_only and jev.name_only[0] == "u2"


def test_no_change_when_everything_fits(proj, budget):
    budget(100_000)
    p = vis.plan(proj, TEN, usage_for(proj, {"u0": 1}, GLOBAL), {})
    assert p.name_only == [] and p.applicable
    assert p.before == p.after and p.before["dropped"] == 0
    assert sorted(p.keep) == sorted(e["name"] for e in TEN)


def test_not_joinable_is_not_applicable(proj, budget):
    budget(600)
    p = vis.plan(proj, TEN, usage_for(proj, {"u0": 3}, GLOBAL, joinable=False), {})
    assert p.applicable is False and p.name_only == []


def test_no_project_usage_means_nothing_to_protect(proj, budget):
    budget(600)
    p = vis.plan(proj, TEN, {"global": GLOBAL, "projects": {}}, {})
    assert p.applicable and p.name_only == [] and p.before["project_used_total"] == 0


def test_existing_overrides_are_simulated(proj, budget):
    budget(600)
    settings = {"skillOverrides": {"u2": "name-only", "u3": "off", "u4": "name-only"}}
    p = vis.plan(proj, TEN, usage_for(proj, {"u0": 3, "u1": 1}, GLOBAL), settings)
    assert not {"u2", "u3", "u4"} & set(p.name_only)
    assert p.after["project_used_kept"] == 2


def test_audit_numbers_unchanged():
    def reference(skills, usage, budget, cap):          # the pre-4.2 _fit_listing, verbatim
        costs = {e["name"]: (len(e["name"]), min(len(sa._desc(e)), cap)) for e in skills}
        total = sum(n + d for n, d in costs.values())
        dropped = 0
        if total > budget:
            order = sorted((e["name"] for e in skills if costs[e["name"]][1]),
                           key=lambda n: (max(usage.get(n, 0), usage.get(sa._bare(n), 0)), n))
            for n in order:
                if total <= budget:
                    break
                total -= costs[n][1]
                dropped += 1
        return total, dropped

    rng = random.Random(7)
    for _ in range(200):
        skills = [{"name": f"s{i}", "description": "d" * rng.choice([0, 5, 50, 400, 2000])}
                  for i in range(rng.randint(0, 30))]
        usage = {e["name"]: rng.randint(0, 5) for e in skills if rng.random() < 0.5}
        b, cap = rng.randint(0, 5000), rng.choice([100, 1536])
        assert sa._fit_listing(skills, usage, b, cap) == reference(skills, usage, b, cap)


def test_fit_listing_detail_name_only_costs_name_only():
    skills = [sk("aa"), sk("bb")]
    chars, kept = sa.fit_listing_detail(skills, {}, 10_000, 1536, name_only={"aa"})
    assert chars == 2 + 2 + 98 and kept == {"bb"}


def test_jev_ranked_for_counts_top3_for_project(tmp_path):
    f = tmp_path / "features.jsonl"
    rows = [{"project": "abc123abc123", "jev_ranked": ["a", "b", "c", "d"]},
            {"project": "abc123abc123", "jev_ranked": ["a"]},
            {"project": "other0000000", "jev_ranked": ["z"]},
            {"jev_ranked": ["y"]}]
    f.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    assert vis.jev_ranked_for("abc123abc123", f) == {"a": 2, "b": 1, "c": 1}
    assert vis.jev_ranked_for("abc123abc123", tmp_path / "missing.jsonl") == {}


def test_pruning_removes_noop_overrides(proj, budget):
    budget(600)
    # u0 is project-used with mid global usage; u1..u4 sit below it and are dropped anyway,
    # so the greedy prefix u1..u5 holds four no-op overrides: hiding u5 alone suffices.
    glob = {"u0": 5, "u1": 1, "u2": 2, "u3": 3, "u4": 4, **{f"u{i}": i + 1 for i in range(5, 10)}}
    p = vis.plan(proj, TEN, usage_for(proj, {"u0": 1}, glob), {})
    assert p.before["project_used_kept"] == 0
    assert p.after["project_used_kept"] == 1
    assert p.name_only == ["u5"]
    assert "pruned: 4" in p.notes


def test_names_in_budget_model(proj, budget):
    budget(500)                                    # 10 x 98 desc chars + 20 name chars
    use = usage_for(proj, {"u0": 1}, GLOBAL)
    charged = vis.plan(proj, TEN, use, {})
    free = vis.plan(proj, TEN, use, {}, names_in_budget=False)
    assert charged.before["name_cost_chars"] == 20 and charged.before["budget_chars"] == 500
    assert free.before["dropped"] <= charged.before["dropped"]
    assert any("do not count" in n for n in free.notes)
    assert any("names count" in n for n in charged.notes)
    chars, kept = sa.fit_listing_detail(TEN, {}, 98 * 10, 1536, names_in_budget=False)
    assert len(kept) == 10 and chars == 1000
    assert len(sa.fit_listing_detail(TEN, {}, 98 * 10, 1536)[1]) == 9


def test_used_plugin_skill_never_hidden_under_tight_budget(proj, budget):
    budget(150)
    catalog = TEN + [sk(f"px:p{i}", "plugin-skill") for i in range(4)]
    p = vis.plan(proj, catalog, usage_for(proj, {"px:p0": 5, "u0": 1}, GLOBAL), {})
    assert not [n for n in p.name_only if n.startswith("px:")]


def test_bare_name_protection_both_ways(proj, budget):
    budget(250)
    catalog = [sk(f"u{i}") for i in range(8)] + [sk("x"), sk("plug:x", "plugin-skill"),
                                                   sk("y"), sk("plug:y", "plugin-skill")]
    p = vis.plan(proj, catalog, usage_for(proj, {"x": 1, "plug:y": 1}, {}), {})
    assert not {"x", "y", "plug:x", "plug:y"} & set(p.name_only)
    assert p.before["project_used_total"] == 4


def test_recent_last_used_protected_beyond_caller_window(proj, budget):
    budget(250)
    use = usage_for(proj, {}, {})
    entry = next(iter(use["projects"].values()))
    entry["skills"] = {"u0": 1}
    entry["last_used"] = {n: pu_ts(1) for n in ("u5", "u6")} | {"u7": pu_ts(200)}
    entry["skills"]["u8"] = "3"                    # non-int truthy count still counts as used
    p = vis.plan(proj, TEN, use, {})
    assert not {"u0", "u5", "u6", "u8"} & set(p.name_only)


def pu_ts(days_ago: float) -> str:
    import datetime as dt
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat()


# ---- Task 4.3: apply, undo, worthwhile, budget lever, CLI ---------------------

import subprocess  # noqa: E402

import cli  # type: ignore[import-not-found]  # noqa: E402

TWENTY = [sk(f"u{i:02d}", desc_len=96) for i in range(20)]            # 100 chars each with name
GLOBAL20 = {f"u{i:02d}": i + 1 for i in range(5, 20)}
USED5 = {f"u{i:02d}": 2 for i in range(5)}


def worthy(proj: Path, budget, settings: dict | None = None):
    budget(1000)
    u = usage_for(proj, USED5, GLOBAL20)
    return vis.plan(proj, TWENTY, u, settings or {}), u


def target(proj: Path) -> Path:
    return proj / ".claude" / "settings.local.json"


def test_worthy_fixture_is_worthwhile(proj, budget):
    p, _ = worthy(proj, budget)
    assert vis.worthwhile(p)[0], vis.worthwhile(p)


def test_dry_run_writes_nothing(proj, budget, monkeypatch):
    p, u = worthy(proj, budget)
    monkeypatch.setattr(sa, "load_inputs", lambda: (TWENTY, GLOBAL20, 75, None, None))
    monkeypatch.setattr(sa, "load_settings", lambda d=None, user_path=None: {})
    monkeypatch.setattr(pu, "usage_by_project", lambda *a, **k: u["projects"])
    before = sorted(proj.rglob("*"))
    text, code = cli.run_visibility("plan", str(proj), no_jev=True)
    assert code == 0 and "worthwhile: yes" in text and "budget lever (names count)" in text
    assert "simulated" in text
    assert sorted(proj.rglob("*")) == before


def test_plan_without_usage_history(proj, monkeypatch):
    monkeypatch.setattr(sa, "load_inputs", lambda: (TWENTY, {}, 75, None, None))
    monkeypatch.setattr(sa, "load_settings", lambda d=None, user_path=None: {})
    monkeypatch.setattr(pu, "usage_by_project", lambda *a, **k: {})
    assert cli.run_visibility("plan", str(proj)) == ("no usage history for this project", 0)
    assert cli.run_visibility("apply", str(proj))[1] == 1


def test_apply_then_undo_is_byte_identical(proj, budget):
    p, _ = worthy(proj, budget)
    t = target(proj)
    t.parent.mkdir()
    original = b'{\n "model":  "x",\n "skillOverrides": {"keepme": "on"}\n}'
    t.write_bytes(original)
    t.chmod(0o640)
    backup = vis.apply(proj, p)
    assert backup.read_bytes() == original
    doc = json.loads(t.read_text())
    assert all(doc["skillOverrides"][n] == "name-only" for n in p.name_only)
    assert "off" not in doc["skillOverrides"].values()
    assert vis.undo(proj) is True
    assert t.read_bytes() == original and oct(t.stat().st_mode & 0o777) == "0o640"
    assert not backup.exists()
    assert vis.undo(proj) is False


def test_existing_overrides_preserved_and_restored(proj, budget):
    p, _ = worthy(proj, budget)
    t = target(proj)
    t.parent.mkdir()
    t.write_text(json.dumps({"permissions": {"allow": ["a"]}, "skillOverrides": {"zzz": "off"}}))
    vis.apply(proj, p)
    doc = json.loads(t.read_text())
    assert doc["permissions"] == {"allow": ["a"]} and doc["skillOverrides"]["zzz"] == "off"
    assert set(doc["skillOverrides"]) == {"zzz", *p.name_only}


def test_existing_explicit_override_kept(proj, budget):
    p, _ = worthy(proj, budget)
    pick = p.name_only[0]
    t = target(proj)
    t.parent.mkdir()
    t.write_text(json.dumps({"skillOverrides": {pick: "user-invocable-only"}}))
    vis.apply(proj, p, force=True)
    assert json.loads(t.read_text())["skillOverrides"][pick] == "user-invocable-only"


def test_malformed_settings_refused(proj, budget):
    p, _ = worthy(proj, budget)
    t = target(proj)
    t.parent.mkdir()
    t.write_text("{not json")
    with pytest.raises(vis.VisibilityError, match="not valid JSON"):
        vis.apply(proj, p)
    assert t.read_text() == "{not json"
    assert not list(t.parent.glob("*skill-router*"))


def test_tracked_settings_refused(proj, budget):
    p, _ = worthy(proj, budget)
    t = target(proj)
    t.parent.mkdir()
    t.write_text("{}")
    for cmd in (["init", "-q"], ["add", ".claude/settings.local.json"]):
        subprocess.run(["git", "-C", str(proj), *cmd], check=True, capture_output=True)
    with pytest.raises(vis.VisibilityError, match="tracked by git"):
        vis.apply(proj, p)
    assert t.read_text() == "{}"


def test_untracked_in_git_repo_allowed(proj, budget):
    p, _ = worthy(proj, budget)
    subprocess.run(["git", "-C", str(proj), "init", "-q"], check=True, capture_output=True)
    vis.apply(proj, p)
    assert vis.undo(proj)


def test_absent_file_undo_removes_it(proj, budget):
    p, _ = worthy(proj, budget)
    t = target(proj)
    marker = vis.apply(proj, p)
    assert marker.name.endswith(".skill-router-absent") and t.exists()
    assert oct(t.stat().st_mode & 0o777) == "0o600"
    assert vis.undo(proj) is True
    assert not t.exists() and not marker.exists()


def test_second_apply_refused_until_undo(proj, budget):
    p, _ = worthy(proj, budget)
    vis.apply(proj, p)
    with pytest.raises(vis.VisibilityError, match="undo first"):
        vis.apply(proj, p)
    vis.undo(proj)
    vis.apply(proj, p)


def test_non_worthwhile_plan_refused_without_force(proj, budget):
    budget(600)
    p = vis.plan(proj, TEN, usage_for(proj, {"u0": 3, "u1": 1}, GLOBAL), {})
    ok, why = vis.worthwhile(p)
    assert not ok and "gain 2" in why
    with pytest.raises(vis.VisibilityError, match="not worthwhile"):
        vis.apply(proj, p)
    assert not target(proj).exists()
    vis.apply(proj, p, force=True)
    assert target(proj).exists()


def test_not_applicable_refused_even_with_force(proj, budget):
    budget(600)
    p = vis.plan(proj, TEN, usage_for(proj, {"u0": 1}, GLOBAL, joinable=False), {})
    with pytest.raises(vis.VisibilityError, match="not applicable"):
        vis.apply(proj, p, force=True)


def test_budget_needed_reports_fraction_and_tokens(proj, budget):
    budget(1000)   # env pins the current budget; the search ignores it by design
    u = usage_for(proj, USED5, GLOBAL20)
    frac, extra = vis.budget_needed(proj, TWENTY, u, {}, context_tokens=200_000)
    assert frac is not None and 0 < frac <= 0.10
    ctx = 200_000
    assert extra == int(frac * 1000) * ctx // 1000 - 1000 // 4
    # smallest: one step less does not keep every used skill; this step does
    skills = sa._routable(TWENTY)
    used = set(USED5)

    def fits(f: float) -> bool:
        _, kept = sa.fit_listing_detail(skills, GLOBAL20, int(f * ctx * 4), sa.desc_cap({}), set())
        return used <= kept
    assert fits(frac) and not fits(round(frac - 0.001, 3))
    # names free needs no more than names counted
    free, _ = vis.budget_needed(proj, TWENTY, u, {}, ctx, names_in_budget=False)
    assert free is not None and free <= frac
    # unreachable
    huge = [sk(f"h{i:03d}", desc_len=1500) for i in range(100)]
    gl = {f"h{i:03d}": i + 1 for i in range(1, 100)}
    assert vis.budget_needed(proj, huge, usage_for(proj, {"h000": 1}, gl), {}, ctx) == (None, 0)


# ---- fix round 1: undo keeps later edits, location guards, git fail-closed ----

def test_permission_added_after_apply_survives_undo_existing_file(proj, budget):
    p, _ = worthy(proj, budget)
    t = target(proj)
    t.parent.mkdir()
    t.write_text(json.dumps({"model": "x"}))
    vis.apply(proj, p)
    doc = json.loads(t.read_text())
    doc["permissions"] = {"allow": ["Bash(ls)"]}
    t.write_text(json.dumps(doc))
    assert vis.undo_status(proj) == "merged"
    after = json.loads(t.read_text())
    assert after == {"model": "x", "permissions": {"allow": ["Bash(ls)"]}}


def test_permission_added_after_apply_survives_undo_absent_file(proj, budget):
    p, _ = worthy(proj, budget)
    t = target(proj)
    vis.apply(proj, p)
    doc = json.loads(t.read_text())
    doc["permissions"] = {"allow": ["Bash(ls)"]}
    doc["skillOverrides"]["mine"] = "off"
    doc["skillOverrides"][p.name_only[0]] = "on"          # user changed it since: keep
    t.write_text(json.dumps(doc))
    assert vis.undo_status(proj) == "merged"
    after = json.loads(t.read_text())
    assert after["permissions"] == {"allow": ["Bash(ls)"]}
    assert after["skillOverrides"] == {"mine": "off", p.name_only[0]: "on"}
    assert not list(t.parent.glob("*skill-router*"))


def test_absent_case_edit_free_undo_removes_file_and_created_dir(proj, budget):
    p, _ = worthy(proj, budget)
    vis.apply(proj, p)
    assert vis.undo_status(proj) == "restored"
    assert not (proj / ".claude").exists()


def test_cli_partial_undo_message(proj, budget):
    p, _ = worthy(proj, budget)
    vis.apply(proj, p)
    doc = json.loads(target(proj).read_text())
    doc["x"] = 1
    target(proj).write_text(json.dumps(doc))
    text, code = cli.run_visibility("undo", str(proj))
    assert code == 0 and text == vis.PARTIAL_UNDO_MSG


def test_home_directory_refused(proj, budget, monkeypatch):
    p, _ = worthy(proj, budget)
    monkeypatch.setenv("HOME", str(proj))
    with pytest.raises(vis.VisibilityError, match="user-level"):
        vis.apply(proj, p)
    assert not (proj / ".claude").exists()


def test_inside_real_dot_claude_refused(proj, budget, monkeypatch, tmp_path):
    home = tmp_path / "h"
    inner = home / ".claude" / "sub"
    inner.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    budget(1000)
    p = vis.plan(inner, TWENTY, usage_for(inner, USED5, GLOBAL20), {})
    with pytest.raises(vis.VisibilityError, match="user-level"):
        vis.apply(inner, p)


def test_symlinked_settings_file_refused(proj, budget, tmp_path):
    p, _ = worthy(proj, budget)
    real = tmp_path / "elsewhere.json"
    real.write_text("{}")
    target(proj).parent.mkdir()
    target(proj).symlink_to(real)
    with pytest.raises(vis.VisibilityError, match="symlink"):
        vis.apply(proj, p)
    assert real.read_text() == "{}" and target(proj).is_symlink()


def test_dangling_symlink_and_symlinked_dir_refused(proj, budget, tmp_path):
    p, _ = worthy(proj, budget)
    target(proj).parent.mkdir()
    target(proj).symlink_to(tmp_path / "nowhere.json")
    with pytest.raises(vis.VisibilityError, match="symlink"):
        vis.apply(proj, p)
    target(proj).unlink()
    target(proj).parent.rmdir()
    other = tmp_path / "otherdir"
    other.mkdir()
    (proj / ".claude").symlink_to(other)
    with pytest.raises(vis.VisibilityError, match="symlink"):
        vis.apply(proj, p)
    assert not list(other.iterdir())


@pytest.mark.parametrize("err", [FileNotFoundError("git"), subprocess.TimeoutExpired("git", 1)])
def test_git_missing_or_timeout_refuses(proj, budget, monkeypatch, err):
    p, _ = worthy(proj, budget)

    def boom(*a, **k):
        raise err
    monkeypatch.setattr(vis.subprocess, "run", boom)
    with pytest.raises(vis.VisibilityError, match="cannot tell"):
        vis.apply(proj, p)
    assert not target(proj).exists()


def test_git_unexpected_error_refuses_and_known_negatives_allow(proj, budget, monkeypatch):
    p, _ = worthy(proj, budget)
    cp = subprocess.CompletedProcess

    def fake(replies):
        def run(cmd, **k):
            return replies[cmd[3]]
        return run
    monkeypatch.setattr(vis.subprocess, "run", fake({"rev-parse": cp([], 128, "", "fatal: detected dubious ownership")}))
    with pytest.raises(vis.VisibilityError, match="cannot tell"):
        vis.apply(proj, p)
    monkeypatch.setattr(vis.subprocess, "run", fake({
        "rev-parse": cp([], 0, "true\n", ""),
        "ls-files": cp([], 128, "", "fatal: index file corrupt")}))
    with pytest.raises(vis.VisibilityError, match="cannot tell"):
        vis.apply(proj, p)
    monkeypatch.setattr(vis.subprocess, "run", fake({
        "rev-parse": cp([], 0, "true\n", ""),
        "ls-files": cp([], 1, "", "error: pathspec 'x' did not match any file(s) known to git")}))
    vis.apply(proj, p)


def test_project_scope_override_never_written(proj, budget):
    p, _ = worthy(proj, budget)
    pick = p.name_only[0]
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.json").write_text(json.dumps({"skillOverrides": {pick: "on"}}))
    vis.apply(proj, p, force=True)
    assert pick not in json.loads(target(proj).read_text())["skillOverrides"]
    # planning with the merged scopes never even picks it
    p2 = vis.plan(proj, TWENTY, usage_for(proj, USED5, GLOBAL20),
                  {"skillOverrides": vis.explicit_overrides(proj)})
    assert pick not in p2.name_only


def test_file_changed_during_apply_aborts_cleanly(proj, budget, monkeypatch):
    p, _ = worthy(proj, budget)
    t = target(proj)
    t.parent.mkdir()
    t.write_text('{"a": 1}')
    real = vis._create_excl
    seen = []

    def racing(path, data, mode):
        real(path, data, mode)
        seen.append(path)
        if len(seen) == 2:
            t.write_text('{"a": 2}')
    monkeypatch.setattr(vis, "_create_excl", racing)
    with pytest.raises(vis.VisibilityError, match="changed during apply"):
        vis.apply(proj, p)
    assert t.read_text() == '{"a": 2}' and not list(t.parent.glob("*skill-router*"))


def test_markers_are_exclusive_and_private(proj, budget):
    p, _ = worthy(proj, budget)
    vis.apply(proj, p)
    rec = target(proj).with_name("settings.local.json.skill-router-applied.json")
    assert oct(rec.stat().st_mode & 0o777) == "0o600"
    with pytest.raises(vis.VisibilityError):
        vis._create_excl(rec, b"x", 0o600)


def test_cli_oserror_is_one_line(proj, budget, monkeypatch):
    p, u = worthy(proj, budget)
    monkeypatch.setattr(sa, "load_inputs", lambda: (TWENTY, GLOBAL20, 75, None, None))
    monkeypatch.setattr(sa, "load_settings", lambda d=None, user_path=None: {})
    monkeypatch.setattr(pu, "usage_by_project", lambda *a, **k: u["projects"])
    monkeypatch.setattr(vis, "apply", lambda *a, **k: (_ for _ in ()).throw(PermissionError(13, "Permission denied")))
    assert cli.run_visibility("apply", str(proj)) == ("error: Permission denied", 1)
    monkeypatch.setattr(vis, "undo_status", lambda *a: (_ for _ in ()).throw(PermissionError(13, "Permission denied")))
    assert cli.run_visibility("undo", str(proj)) == ("error: Permission denied", 1)


def test_plan_output_notes_and_sufficient_budget(proj, budget, monkeypatch):
    p, u = worthy(proj, budget)
    monkeypatch.setattr(sa, "load_inputs", lambda: (TWENTY, GLOBAL20, 75, None, None))
    monkeypatch.setattr(sa, "load_settings", lambda d=None, user_path=None: {})
    monkeypatch.setattr(pu, "usage_by_project", lambda *a, **k: u["projects"])
    text, _ = cli.run_visibility("plan", str(proj), no_jev=True)
    assert "SLASH_COMMAND_TOOL_CHAR_BUDGET is set" in text and "reformats" in text
    monkeypatch.delenv("SLASH_COMMAND_TOOL_CHAR_BUDGET")
    monkeypatch.setattr(vis, "budget_needed", lambda *a, **k: (0.001, 0))
    assert "already sufficient" in cli.run_visibility("plan", str(proj), no_jev=True)[0]


# ---- fix round 2 ----

def test_symlinked_user_dot_claude_refused(budget, tmp_path, monkeypatch):
    home = tmp_path / "home"
    dotfiles = tmp_path / "dotfiles-claude"
    inner = dotfiles / "skills" / "y"
    inner.mkdir(parents=True)
    home.mkdir()
    (home / ".claude").symlink_to(dotfiles)
    monkeypatch.setenv("HOME", str(home))
    budget(1000)
    p = vis.plan(inner, TWENTY, usage_for(inner, USED5, GLOBAL20), {})
    with pytest.raises(vis.VisibilityError, match="user-level"):
        vis.apply(inner, p)
    assert not (inner / ".claude").exists()


def test_deleted_settings_after_apply_keeps_backup(proj, budget):
    p, _ = worthy(proj, budget)
    t = target(proj)
    t.parent.mkdir()
    t.write_text('{"a": 1}')
    backup = vis.apply(proj, p)
    t.unlink()
    assert vis.undo_status(proj) == "missing"
    assert backup.read_text() == '{"a": 1}' and not t.exists()
    assert list(t.parent.glob("*applied.json"))
    text, code = cli.run_visibility("undo", str(proj))
    assert code == 0 and "removed after apply; original kept at" in text and str(backup) in text


def test_undo_after_user_removed_overrides_key_and_added_another(proj, budget):
    p, _ = worthy(proj, budget)
    t = target(proj)
    t.parent.mkdir()
    t.write_text(json.dumps({"model": "x"}))
    vis.apply(proj, p)
    t.write_text(json.dumps({"model": "x", "env": {"A": "1"}}))      # skillOverrides key gone
    assert vis.undo_status(proj) == "merged"
    assert json.loads(t.read_text()) == {"model": "x", "env": {"A": "1"}}
    assert not list(t.parent.glob("*skill-router*"))


def test_undo_invalid_json_still_refuses(proj, budget):
    p, _ = worthy(proj, budget)
    vis.apply(proj, p)
    target(proj).write_text("{ nope")
    with pytest.raises(vis.VisibilityError, match="no longer valid JSON"):
        vis.undo_status(proj)


def test_absent_before_apply_and_deleted_since_reports_restored(proj, budget):
    p, _ = worthy(proj, budget)
    vis.apply(proj, p)
    target(proj).unlink()
    assert vis.undo_status(proj) == "restored"
    assert not list(target(proj).parent.glob("*skill-router*"))


def test_plan_caches_both_models(proj, budget, monkeypatch, tmp_path):
    p, u = worthy(proj, budget)
    monkeypatch.setattr(sa, "load_inputs", lambda: (TWENTY, GLOBAL20, 75, None, None))
    monkeypatch.setattr(sa, "load_settings", lambda d=None, user_path=None: {})
    monkeypatch.setattr(pu, "usage_by_project", lambda *a, **k: u["projects"])
    text, code = cli.run_visibility("plan", str(proj), no_jev=True)
    assert code == 0
    got = vis.read_summary()["projects"][pu.project_key(proj)]
    assert set(got) == {"names_count", "names_free"}
    assert all("worthwhile" in v and "budget_fraction" in v for v in got.values())
