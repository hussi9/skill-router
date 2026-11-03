#!/usr/bin/env python3
"""
visibility.py -- per-project plan: which skills go `name-only` so the used ones stay described.

Claude Code caps the skill listing (1% of context by default) and, on overflow, drops the
descriptions of the least-used skills first while keeping names. A project's own skills can
lose their descriptions to skills it never uses. This module plans, per project, which
user/project skills to set to `name-only` via the documented `skillOverrides` setting so the
skills the project actually uses keep their descriptions. `apply`/`undo` (below) are the only code that writes a file.

Inputs
  usage = {"global": {skill: count},                 # skill_audit.load_inputs usage dict
           "projects": project_usage.usage_by_project(...)}   # keyed by project_key
  jev_ranked (optional, Jev mode) = jev_ranked_for(project_key, features_path)

Relevance = 3 x project usage + 1 x global usage + 2 x Jev rank hits (Jev mode only).

Selection is greedy: non-plugin skills not used in the project are sorted by relevance
ascending (ties by name) and moved to name_only one at a time until every project-used skill
keeps its description in the simulated listing. The shortest prefix reaching the best result
is kept; when nothing improves (or everything already fits) name_only is empty.

Guarantees: plugin skills are never put in name_only (`skillOverrides` does not apply to
them; plugins appear only in plugin_suggestions as (plugin, used in project, total)). A skill
with any truthy project usage count, or a project last_used within 90 days
(independent of the caller's window), is never hidden; bare-name
matches are protected too, so the check errs toward keeping. Existing `skillOverrides` in the
merged settings are honoured (`name-only` costs a name, `off` leaves the listing).

Non-joinable projects (project_usage entry with joinable False or dir None) get an empty plan
with applicable=False rather than an exception, so a caller looping over every project can
skip them; Task 4.3 must refuse to apply a plan whose `applicable` is False.

NAMES-IN-BUDGET ASSUMPTION: the docs do not say whether skill names are charged against the
listing budget. `names_in_budget=True` (the audit's model) charges them; False treats names as
free and always present, so only description characters compete. Every plan states the model
used, and `before` reports name_cost_chars vs budget_chars so the two can be compared.

Pruning: after the greedy prefix, every override whose removal leaves project_used_kept
unchanged is dropped (`pruned: N` in notes), so no no-op override is ever written.

SIMULATED: the harness orders drops by ITS OWN usage statistics, which are not visible here.
We approximate them with our global usage counts, so before/after numbers are a simulation
(`simulated=True` on every plan) and Task 4.4 measures the real effect.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

import project_usage  # type: ignore[import-not-found]  # noqa: E402
import skill_audit as sa  # type: ignore[import-not-found]  # noqa: E402

W_PROJECT = 3
W_GLOBAL = 1
W_JEV = 2
JEV_TOP = 3
PROTECT_DAYS = 90                       # never hide a skill used here this recently
CHARS_PER_TOKEN = sa.CHARS_PER_TOKEN


@dataclass(frozen=True)
class VisibilityPlan:
    keep: list[str]                                   # names that keep descriptions after the plan
    name_only: list[str]                              # to add to skillOverrides as "name-only"
    plugin_suggestions: list[tuple[str, int, int]]    # (plugin, used in project, total) - suggestions only
    before: dict                                      # {"fit_tokens", "dropped", "project_used_kept", "project_used_total",
                                                      #  "name_cost_chars", "budget_chars", "names_in_budget"}
    after: dict
    applicable: bool                                  # False => must never be applied
    simulated: bool = True                            # drop order approximated with our usage counts
    project_key: str = ""
    notes: list[str] = field(default_factory=list)


def jev_ranked_for(project_key: str, features_path: Optional[Path] = None) -> dict[str, int]:
    """skill -> times Jev ranked it in the top 3 for this project's prompts (features log rows)."""
    if features_path is None:
        import jev_dataset  # type: ignore[import-not-found]
        features_path = jev_dataset.features_path()
    try:
        lines = Path(features_path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    out: dict[str, int] = {}
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or row.get("project") != project_key:
            continue
        ranked = row.get("jev_ranked")
        if not isinstance(ranked, list):
            continue
        for name in ranked[:JEV_TOP]:
            if isinstance(name, str) and name:
                out[name] = out.get(name, 0) + 1
    return out


def _num(value: object) -> float:
    """A usage count as a number; any other truthy value counts as one use."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else 0.0
    return 1.0 if value else 0.0


def _count(name: str, counts: dict) -> float:
    return max(_num(counts.get(name, 0)), _num(counts.get(sa._bare(name), 0)))


def _recent(ts: object, now: float) -> bool:
    epoch = project_usage._parse_ts(ts) if isinstance(ts, str) else None
    return epoch is not None and epoch >= now - PROTECT_DAYS * 86400


def _used(skills: list[dict], entry: Optional[dict]) -> tuple[dict, set[str]]:
    """(project usage counts, names of catalog skills this project uses or used in the last 90 days)."""
    raw = (entry or {}).get("skills") or {}
    proj_counts = {n: c for n, c in raw.items() if isinstance(n, str) and _num(c) > 0}
    now = time.time()
    recent = {n for n, ts in ((entry or {}).get("last_used") or {}).items()
              if isinstance(n, str) and _recent(ts, now)}
    proj_names = set(proj_counts) | recent
    proj_bare = {sa._bare(n) for n in proj_names}
    used = {e["name"] for e in skills if e["name"] in proj_names or sa._bare(e["name"]) in proj_bare}
    return proj_counts, used


def plan(project: Path, catalog: list[dict], usage: dict, settings: dict,
         jev_ranked: Optional[dict[str, int]] = None,
         context_tokens: int = sa.DEFAULT_CONTEXT_TOKENS,
         names_in_budget: bool = True) -> VisibilityPlan:
    """Plan which user/project skills to set name-only in `project` (dry; writes nothing)."""
    settings = settings or {}
    key = project_usage.project_key(Path(project))
    entry = (usage.get("projects") or {}).get(key)
    global_usage: dict[str, int] = dict(usage.get("global") or {})

    overrides = settings.get("skillOverrides")
    overrides = overrides if isinstance(overrides, dict) else {}
    skills = [e for e in sa._routable(catalog) if overrides.get(e["name"]) != "off"]
    preset = {e["name"] for e in skills
              if overrides.get(e["name"]) == "name-only" and e.get("type") != "plugin-skill"}

    budget = sa.listing_budget(settings, context_tokens)
    cap = sa.desc_cap(settings)
    has_desc = {e["name"] for e in skills if min(len(sa._desc(e)), cap)}
    name_cost = sum(len(e["name"]) for e in skills)
    assumption = (f"assumption: names {'count' if names_in_budget else 'do not count'} toward the "
                  f"listing budget (docs do not say); names {name_cost} chars vs budget {budget}")

    def simulate(name_only: set[str], used: set[str]) -> tuple[dict, set[str]]:
        chars, kept = sa.fit_listing_detail(skills, global_usage, budget, cap, name_only | preset,
                                            names_in_budget=names_in_budget)
        dropped = len(has_desc - kept - name_only - preset)
        return ({"fit_tokens": chars // CHARS_PER_TOKEN, "dropped": dropped,
                 "project_used_kept": len(used & kept),
                 "project_used_total": len(used & has_desc)}, kept)

    def with_cost(stats: dict) -> dict:
        return {**stats, "name_cost_chars": name_cost, "budget_chars": budget,
                "names_in_budget": names_in_budget}

    if entry is not None and (not entry.get("joinable") or not entry.get("dir")):
        before, kept = simulate(set(), set())
        return VisibilityPlan(keep=sorted(kept), name_only=[], plugin_suggestions=[],
                              before=with_cost(before), after=with_cost(before), applicable=False,
                              project_key=key,
                              notes=["project not joinable: plan must not be applied", assumption])

    proj_counts, used = _used(skills, entry)

    groups: dict[str, list[str]] = {}
    for e in skills:
        if e.get("type") == "plugin-skill":
            groups.setdefault(e["name"].split(":", 1)[0] if ":" in e["name"] else "unknown",
                              []).append(e["name"])
    suggestions = sorted(((p, len(set(ns) & used), len(ns)) for p, ns in groups.items()),
                         key=lambda x: (x[1] / x[2], x[0]))

    def relevance(name: str) -> float:
        r = W_PROJECT * _count(name, proj_counts) + W_GLOBAL * _count(name, global_usage)
        if jev_ranked:
            r += W_JEV * _count(name, jev_ranked)
        return r

    before, kept0 = simulate(set(), used)
    if before["project_used_kept"] == before["project_used_total"]:
        return VisibilityPlan(keep=sorted(kept0), name_only=[], plugin_suggestions=suggestions,
                              before=with_cost(before), after=with_cost(before), applicable=True,
                              project_key=key, notes=["pruned: 0", assumption])

    candidates = sorted((e["name"] for e in skills
                         if e.get("type") != "plugin-skill" and e["name"] not in used
                         and e["name"] in has_desc and e["name"] not in overrides),
                        key=lambda n: (relevance(n), n))
    best_n, best_kept = 0, before["project_used_kept"]
    chosen: set[str] = set()
    for i, name in enumerate(candidates, 1):
        chosen.add(name)
        stats, _ = simulate(chosen, used)
        if stats["project_used_kept"] > best_kept:
            best_n, best_kept = i, stats["project_used_kept"]
        if best_kept == before["project_used_total"]:
            break
    name_only = candidates[:best_n]

    # Prune no-op overrides (lowest relevance first: their descriptions are usually dropped
    # anyway). Putting a description back only adds characters, so a removal that fails now
    # cannot succeed after later removals: one pass is already stable (O(n x fit)).
    kept_set = set(name_only)
    for name in list(name_only):
        trial = kept_set - {name}
        if simulate(trial, used)[0]["project_used_kept"] >= best_kept:
            kept_set = trial
    pruned = len(name_only) - len(kept_set)
    name_only = [n for n in name_only if n in kept_set]

    after, kept = simulate(set(name_only), used)
    notes = [f"pruned: {pruned}", assumption]
    if best_kept != before["project_used_total"]:
        notes.append("not every project-used skill fits even with all candidates name-only")
    return VisibilityPlan(keep=sorted(kept), name_only=name_only, plugin_suggestions=suggestions,
                          before=with_cost(before), after=with_cost(after), applicable=True, project_key=key,
                          notes=notes)


# ---- worthwhile / budget lever -------------------------------------------------

MIN_GAIN = 3
MIN_GAIN_FRACTION = 0.25
MAX_NAMES_PER_GAIN = 20
MAX_FRACTION_STEPS = 100                 # skillListingBudgetFraction searched to 0.001 up to 0.10


def gain(p: VisibilityPlan) -> int:
    """Project-used skills that newly keep their description."""
    return p.after["project_used_kept"] - p.before["project_used_kept"]


def worthwhile(p: VisibilityPlan) -> tuple[bool, str]:
    """(ok, reason). A plan is worth applying only if it recovers enough descriptions cheaply."""
    if not p.applicable:
        return False, "plan is not applicable (project not joinable to usage history)"
    need = max(MIN_GAIN, math.ceil(MIN_GAIN_FRACTION * p.before["project_used_total"]))
    g = gain(p)
    if g < need:
        return False, f"gain {g} < required {need} project-used skills regaining descriptions"
    if len(p.name_only) > MAX_NAMES_PER_GAIN * g:
        return False, f"{len(p.name_only)} overrides for a gain of {g} (max {MAX_NAMES_PER_GAIN} per gain)"
    return True, f"gain {g} >= {need} with {len(p.name_only)} overrides"


def budget_needed(project: Path, catalog: list[dict], usage: dict, settings: dict,
                  context_tokens: int = sa.DEFAULT_CONTEXT_TOKENS,
                  names_in_budget: bool = True) -> tuple[Optional[float], int]:
    """(smallest skillListingBudgetFraction, to 0.001, at which every project-used skill keeps its
    description with no new overrides; extra tokens per session vs the current budget).
    (None, 0) if not reachable at fraction <= 0.10. Never applied automatically."""
    settings = settings or {}
    entry = (usage.get("projects") or {}).get(project_usage.project_key(Path(project)))
    overrides = settings.get("skillOverrides")
    overrides = overrides if isinstance(overrides, dict) else {}
    skills = [e for e in sa._routable(catalog) if overrides.get(e["name"]) != "off"]
    preset = {e["name"] for e in skills
              if overrides.get(e["name"]) == "name-only" and e.get("type") != "plugin-skill"}
    cap = sa.desc_cap(settings)
    has_desc = {e["name"] for e in skills if min(len(sa._desc(e)), cap)}
    _, used = _used(skills, entry)
    want = used & has_desc
    global_usage = dict(usage.get("global") or {})

    def ok(step: int) -> bool:
        budget = int(step / 1000 * context_tokens * CHARS_PER_TOKEN)
        _, kept = sa.fit_listing_detail(skills, global_usage, budget, cap, preset,
                                        names_in_budget=names_in_budget)
        return want <= kept

    if not ok(MAX_FRACTION_STEPS):
        return None, 0
    lo, hi = 1, MAX_FRACTION_STEPS                      # kept grows with budget: binary search
    while lo < hi:
        mid = (lo + hi) // 2
        lo, hi = (lo, mid) if ok(mid) else (mid + 1, hi)
    current = sa.listing_budget(settings, context_tokens) // CHARS_PER_TOKEN
    return lo / 1000, lo * context_tokens // 1000 - current


# ---- apply / undo (the only code in this wave that writes a file) --------------

REL_DIR = Path(".claude")
REL_TARGET = REL_DIR / "settings.local.json"
BACKUP_SUFFIX = ".skill-router-backup"
ABSENT_SUFFIX = ".skill-router-absent"
APPLIED_SUFFIX = ".skill-router-applied.json"
PARTIAL_UNDO_MSG = ("settings.local.json changed since apply; removed only skill-router's "
                    "overrides (not byte-identical)")


class VisibilityError(Exception):
    """Apply or undo refused; the message is one line saying why."""


def explicit_overrides(project: Path) -> dict:
    """skillOverrides from user, project and local settings, merged key by key (local wins)."""
    root = Path(project)
    merged: dict = {}
    for path in (sa._path(None, "SKILL_AUDIT_SETTINGS", sa.CLAUDE_DIR / "settings.json"),
                 root / REL_DIR / "settings.json", root / REL_TARGET):
        ov = (sa._json(Path(path)) or {}).get("skillOverrides")
        if isinstance(ov, dict):
            merged.update(ov)
    return merged


def _git(project: Path, *args: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True,
                              timeout=20)
    except (OSError, subprocess.SubprocessError) as e:
        raise VisibilityError(f"cannot tell whether {REL_TARGET} is tracked by git ({type(e).__name__}): refusing") from None


def _tracked(project: Path) -> bool:
    """True if tracked. False only when positively not a repo or positively untracked; else refuse."""
    r = _git(project, "rev-parse", "--is-inside-work-tree")
    if r.returncode != 0:
        if "not a git repository" in (r.stderr or "").lower():
            return False
        raise VisibilityError(f"cannot tell whether {REL_TARGET} is tracked by git: refusing")
    r = _git(project, "ls-files", "--error-unmatch", str(REL_TARGET))
    if r.returncode == 0:
        return True
    if r.returncode == 1 and "did not match" in (r.stderr or "").lower():
        return False
    raise VisibilityError(f"cannot tell whether {REL_TARGET} is tracked by git: refusing")


def _check_location(project: Path) -> Path:
    """Refuse user-level and symlinked targets; returns the resolved project dir."""
    proj = project.resolve()
    home = Path.home().resolve()
    user_claude = (Path.home() / ".claude").resolve()
    claude_dir, target = project / REL_DIR, project / REL_TARGET
    if proj == home or (target.parent.resolve() / target.name).is_relative_to(user_claude):
        raise VisibilityError("refusing to write user-level settings (run from a project directory)")
    if claude_dir.is_symlink() or target.is_symlink() \
            or target.parent.resolve() != proj / REL_DIR:
        raise VisibilityError(f"{REL_DIR} or {REL_TARGET} is a symlink: refusing to write through it")
    return proj


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _create_excl(path: Path, data: bytes, mode: int) -> None:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    except FileExistsError:
        raise VisibilityError("a backup already exists: run undo first") from None
    with os.fdopen(fd, "wb") as f:
        os.fchmod(f.fileno(), mode)
        f.write(data)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def apply(project: Path, p: VisibilityPlan, force: bool = False) -> Path:
    """Write `name-only` overrides for p.name_only into <project>/.claude/settings.local.json.

    Returns the backup (or absent-marker) path. A skill with an explicit override in ANY
    settings scope is never written. The file is re-serialised (indent=2); the backup keeps
    the original bytes."""
    project = Path(project)
    target = project / REL_TARGET
    backup = target.with_name(target.name + BACKUP_SUFFIX)
    absent = target.with_name(target.name + ABSENT_SUFFIX)
    applied = target.with_name(target.name + APPLIED_SUFFIX)
    if p.project_key != project_usage.project_key(project):
        raise VisibilityError("plan was made for a different project")
    if not p.applicable:
        raise VisibilityError("plan is not applicable (project not joinable to usage history)")
    if not force:
        ok, why = worthwhile(p)
        if not ok:
            raise VisibilityError(f"not worthwhile: {why} (use --force to override)")
    _check_location(project)
    if backup.exists() or absent.exists() or applied.exists():
        raise VisibilityError("a backup already exists: run undo first")
    if _tracked(project):
        raise VisibilityError(f"{REL_TARGET} is tracked by git: refusing to edit it")
    existed = target.exists()
    raw = target.read_bytes() if existed else b""
    if existed:
        try:
            doc = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise VisibilityError(f"{REL_TARGET} is not valid JSON: refusing to edit it") from None
        if not isinstance(doc, dict):
            raise VisibilityError(f"{REL_TARGET} is not a JSON object: refusing to edit it")
    else:
        doc = {}
    ov = doc.get("skillOverrides", {})
    if not isinstance(ov, dict):
        raise VisibilityError("skillOverrides is not an object: refusing to edit it")
    taken = explicit_overrides(project)
    add = [n for n in p.name_only if n not in ov and n not in taken]
    if not add:
        raise VisibilityError("nothing to change: every planned skill already has an override")
    mode = stat.S_IMODE(target.stat().st_mode) if existed else 0o600
    created_dir = not (project / REL_DIR).exists()
    new_bytes = (json.dumps({**doc, "skillOverrides": {**ov, **{n: "name-only" for n in add}}},
                            indent=2) + "\n").encode("utf-8")
    marker = backup if existed else absent
    (project / REL_DIR).mkdir(parents=True, exist_ok=True)
    record = {"sha256": _sha(new_bytes), "added": add, "created_dir": created_dir,
              "created_overrides_key": "skillOverrides" not in doc}
    made: list[Path] = []

    def abort(msg: str) -> VisibilityError:
        for m in made:
            m.unlink(missing_ok=True)
        if created_dir:
            try:
                (project / REL_DIR).rmdir()
            except OSError:
                pass
        return VisibilityError(msg)

    try:
        _create_excl(marker, raw, mode)
        made.append(marker)
        _create_excl(applied, json.dumps(record).encode("utf-8"), 0o600)
        made.append(applied)
        try:
            unchanged = (target.read_bytes() == raw) if existed else not target.exists()
        except OSError:
            unchanged = False
        if not unchanged:
            raise abort("file changed during apply, retry")
        _atomic_write(target, new_bytes, mode)
    except VisibilityError:
        for m in made:
            m.unlink(missing_ok=True)
        raise
    except BaseException:
        raise abort("apply failed") from None
    return marker


def undo_status(project: Path) -> Optional[str]:
    """Undo apply. "restored" (byte-identical), "merged" (file changed since apply: only
    skill-router's overrides removed), "missing" (file deleted since apply: backup and record
    kept, nothing restored) or None when there is nothing to undo."""
    project = Path(project)
    target = project / REL_TARGET
    backup = target.with_name(target.name + BACKUP_SUFFIX)
    absent = target.with_name(target.name + ABSENT_SUFFIX)
    applied = target.with_name(target.name + APPLIED_SUFFIX)
    if not (backup.exists() or absent.exists() or applied.exists()):
        return None
    _check_location(project)
    if _tracked(project):
        raise VisibilityError(f"{REL_TARGET} is tracked by git: refusing to touch it")
    try:
        record = json.loads(applied.read_text(encoding="utf-8"))
        want, added = record["sha256"], [n for n in record["added"] if isinstance(n, str)]
    except (OSError, ValueError, KeyError, TypeError):
        raise VisibilityError("applied record missing or unreadable: cannot undo safely") from None
    try:
        cur: Optional[bytes] = target.read_bytes()
    except FileNotFoundError:
        cur = None
    if cur is None and backup.exists():
        return "missing"                  # user deleted the file: keep the original bytes
    if cur is None:
        status = "restored"               # absent before apply and deleted since: nothing to restore
    elif _sha(cur) == want:
        if backup.exists():
            _atomic_write(target, backup.read_bytes(), stat.S_IMODE(backup.stat().st_mode))
        else:
            target.unlink()
        status = "restored"
    else:
        status = "merged"
        if cur is not None:
            try:
                doc = json.loads(cur.decode("utf-8"))
            except ValueError:
                raise VisibilityError(f"{REL_TARGET} is no longer valid JSON: fix it, then undo") from None
            if not isinstance(doc, dict):
                raise VisibilityError(f"{REL_TARGET} is no longer valid JSON: fix it, then undo")
            ov = doc.get("skillOverrides")
            if not isinstance(ov, dict):
                ov = {}                   # key removed by the user: nothing of ours left to remove
            keep = {k: v for k, v in ov.items() if not (k in added and v == "name-only")}
            if keep != ov:
                if keep or not record.get("created_overrides_key"):
                    doc["skillOverrides"] = keep
                else:
                    del doc["skillOverrides"]
                _atomic_write(target, (json.dumps(doc, indent=2) + "\n").encode("utf-8"),
                              stat.S_IMODE(target.stat().st_mode))
    for m in (backup, absent, applied):
        m.unlink(missing_ok=True)
    if record.get("created_dir"):
        try:
            (project / REL_DIR).rmdir()
        except OSError:
            pass
    return status


def undo(project: Path) -> bool:
    """Undo apply; False when there is nothing to undo (see undo_status for the detail)."""
    return undo_status(project) is not None


# ---- cached summary: lets the doctor report without the slow transcript scan ----------------

def summary_path() -> Path:
    return Path.home() / ".claude" / "skill_router_cache" / "visibility-summary.json"


def read_summary() -> dict:
    try:
        data = json.loads(summary_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_summary(key: str, names_in_budget: bool, p: VisibilityPlan, budget_fraction: Optional[float],
                  extra_tokens: int, unused_skills: int) -> None:
    """Merge one project/model entry into the cache (mode 600; counts and keys only, no names/paths)."""
    data = read_summary()
    ok, _why = worthwhile(p)
    data.setdefault("projects", {}).setdefault(key, {})["names_count" if names_in_budget else "names_free"] = {
        "worthwhile": ok,
        "kept_before": p.before["project_used_kept"], "kept_after": p.after["project_used_kept"],
        "used_total": p.before["project_used_total"],
        "dropped_before": p.before["dropped"], "dropped_after": p.after["dropped"],
        "name_only": len(p.name_only),
        "budget_fraction": budget_fraction, "budget_extra_tokens": extra_tokens,
    }
    data["unused_skills"] = unused_skills
    data["updated"] = int(time.time())
    path = summary_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".visibility-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=1, sort_keys=True)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
    except OSError:
        pass                                           # a cache failure never breaks a plan
