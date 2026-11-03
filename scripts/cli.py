#!/usr/bin/env python3
"""
cli.py — `python3 scripts/cli.py stats [--days N]`: are the router's lines followed,
and do routed turns succeed more than held-out ones? Reads the outcomes log only.
Plain text, no prompt text (the log holds none).
"""
from __future__ import annotations

import argparse
import html as _html
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
import outcomes  # noqa: E402
if TYPE_CHECKING:
    from skill_audit import AuditReport


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def stats(days: int = 30) -> str:
    s = outcomes.summarize(days)
    lf = outcomes.lift(days)
    med = outcomes.median_chars(days)
    lines = [f"skill-router stats, last {days} days",
             f"Lines shown: {s['shown']} (held out: {s['held_out']})"]
    if not s["shown"] and not s["held_out"]:
        lines.append("No outcomes recorded yet. Use Claude Code with the router hook for a while, then re-run.")
    lines.append(f"Followed (a shown skill was loaded): {_pct(s['followed_rate'])}"
                 f" ({s['loaded_from_card']} from card, {s['loaded_from_shortlist']} from shortlist)")
    for m in ("jev", "keyless"):
        b = s["by_mode"][m]
        lines.append(f"  {m:<8} shown {b['shown']:>4}  followed {_pct(b['followed_rate'])}")
    lines.append(f"Tests green after a shown line: {s['tests_green_after']}; corrections: {s['corrections_after']}")
    r, h = lf["routed"], lf["held_out"]
    lines.append(f"Success, routed: {r['success']}/{r['known']}; held out: {h['success']}/{h['known']}"
                 " (known = any test result or a correction; success = tests green, none red, no correction)")
    if lf["lift"] is None:
        lines.append("Holdout lift: not enough data (needs known turns on both sides)")
    else:
        lines.append(f"Holdout lift: {lf['lift'] * 100:+.0f} points (n={r['known']} routed, {h['known']} held out)")
    if med is None:
        lines.append("Tokens injected per shown line: no data (older events carry no length)")
    else:
        lines.append(f"Tokens injected per shown line: ~{med / 4:.0f} (median {med:.0f} chars / 4)")
    return "\n".join(lines)


# ---- audit renderers ----------------------------------------------------------



def _sa():
    """skill_audit, imported on first use so `stats` never depends on it."""
    import skill_audit
    return skill_audit


_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u2028\u2029]")
UNUSED_NOTE = ("no logged Skill-tool use in 90 days (slash-command and other-client use "
               "is not tracked) — review before archiving")
FLAGGED_NOTE = "same name as a flagged public skill — check its source"
TEXT_LIST_MAX = 5
MD_LIST_MAX = 25


def _clean(s: object) -> str:
    """Strip control characters, collapse newlines, and replace $HOME with ~."""
    t = _CTRL.sub(" ", str(s)).replace("\n", " ")
    home = str(Path.home())
    if home and home != "/":
        t = t.replace(home, "~")
    return t.strip()


def _safe(names: list[str]) -> list[str]:
    return [n for n in names if isinstance(n, str) and _sa().SAFE_NAME.fullmatch(n)]


def _md_esc(s: object) -> str:
    return re.sub(r"([|*_`<>\\\[\]()#!])", r"\\\1", _clean(s))


def _action_ok(a: str) -> bool:
    """False when an action lists names (after 'check its source: ') and one is unsafe."""
    marker = "check its source: "
    if marker not in a:
        return True
    tail = a.split(marker, 1)[1].rstrip(".").removesuffix(" ...")
    return all(_sa().SAFE_NAME.fullmatch(t.strip()) for t in tail.split(","))


def _model(r: AuditReport) -> dict:
    """Sanitized, format-neutral view of the report."""
    mode = "keyword"
    ov = []
    for a, b, score, m in r.overlaps:
        if not (isinstance(a, str) and isinstance(b, str) and _sa().SAFE_NAME.fullmatch(a)
                and _sa().SAFE_NAME.fullmatch(b)):
            continue
        ov.append((a, b, float(score), m))
        if m == "jev":
            mode = "Jev"
    if mode != "Jev":
        mode = "keyword"
    no_hist = r.history_days < _sa().MIN_HISTORY_DAYS
    return {
        "skills": r.skills, "tokens": r.listing_tokens, "score": r.score,
        "sent": r.listing_sent_tokens, "budget": r.listing_budget_tokens,
        "dropped": r.descriptions_dropped, "thin": r.thin_descriptions,
        "days": r.history_days,
        "no_hist": no_hist,
        "unused": [] if no_hist else _safe(r.unused_90d),
        "never": _safe(r.shown_never_loaded),
        "overlaps": ov, "mode": mode, "flagged": _safe(r.flagged),
        "actions": [_clean(a) for a in r.top_actions[:3]
                    if isinstance(a, str) and _action_ok(a)],
        "age_unknown": r.age_unknown,
        "measured": ((r.measured_described, r.measured_entries, r.measured_tokens)
                     if r.measured_entries else None),
        "plugins": [] if no_hist else [(p, u, t) for p, u, t in r.plugin_usage
                                       if isinstance(p, str) and _sa().SAFE_NAME.fullmatch(p)],
    }


def _sections(m: dict) -> list[tuple[str, list[str], list[str]]]:
    """(heading, items, fixed lines never dropped); actions are handled separately."""
    out: list[tuple[str, list[str], list[str]]] = []
    listing = [_listing_line(m)]
    if m["thin"]:
        listing.append(f"{m['thin']} skills have descriptions too thin to route (< "
                       f"{_sa().KEYWORD_OVERLAP_MIN_TOKENS} tokens) — consider improving them")
    out.append(("Listing", [], listing))
    if m["no_hist"]:
        out.append(("Usage", [], [f"no usage history yet ({m['days']} days logged; "
                                  "need 30) — unused skills and plugin usage are not reported"]))
    else:
        out.append((f"Unused ({len(m['unused'])})", m["unused"],
                    [UNUSED_NOTE] if m["unused"] else ["none"]))
        if m["age_unknown"]:
            out.append(("Install age unknown", [],
                        [f"{m['age_unknown']} standalone skills left out of the unused list"]))
        if m["plugins"]:
            out.append(("Plugin usage (90 days)",
                        [f"{p}: {u} of {t} skills used" for p, u, t in m["plugins"]], []))
    if m["never"]:
        out.append((f"Shown but never loaded ({len(m['never'])})", m["never"], []))
    ov = [f"{a} ~ {b} ({s:.2f})" for a, b, s, _ in m["overlaps"]]
    out.append((f"Overlaps ({len(m['overlaps'])}, judged by {m['mode']})", ov,
                [] if ov else ["none"]))
    if m["flagged"]:
        out.append((f"Flagged ({len(m['flagged'])})", m["flagged"], [FLAGGED_NOTE]))
    return out


def _cut(items: list[str], cap: int) -> list[str]:
    return items if len(items) <= cap else items[:cap] + [f"+{len(items) - cap} more"]


def _listing_line(m: dict) -> str:
    if m.get("measured"):
        d, n, t = m["measured"]
        return f"measured: {d} of {n} entries described; Skills ~{t} tokens"
    return (f"~{m['sent']} of ~{m['tokens']} uncapped listing tokens fit; {m['dropped']} "
            f"descriptions dropped (budget ~{m['budget']} tokens)")


def _headline(m: dict) -> str:
    return (f"{m['skills']} routable skills, ~{m['tokens']} uncapped listing tokens; "
            f"history {m['days']} days")


def _render_text(m: dict) -> str:
    lines = [f"skill doctor — {_headline(m)}", f"Score: {m['score']}/100"]
    if m["actions"]:
        lines += ["Top actions:"] + [f"  {a}" for a in m["actions"]]
    secs = _sections(m)
    caps = [min(len(it), TEXT_LIST_MAX) for _, it, _ in secs]

    def cost(i: int) -> int:
        _, it, fixed = secs[i]
        return 1 + len(fixed) + caps[i] + (1 if caps[i] < len(it) else 0)

    while len(lines) + sum(cost(i) for i in range(len(secs))) > 30 and any(caps):
        j = max(range(len(secs)), key=lambda i: caps[i])
        caps[j] -= 1
    for i, (head, it, fixed) in enumerate(secs):
        lines.append(f"{head}:")
        lines += [f"  {_clean(x)}" for x in _cut(it, caps[i]) + fixed]
    return "\n".join(lines)


def _render_md(m: dict) -> str:
    lines = ["# Skill doctor", "", _md_esc(_headline(m)), "", f"**Score: {m['score']}/100**"]
    if m["actions"]:
        lines += ["", "## Top actions", ""] + [f"- {_md_esc(i)}" for i in m["actions"]]
    for head, it, fixed in _sections(m):
        lines += ["", f"## {_md_esc(head)}", ""] + [f"- {_md_esc(i)}" for i in _cut(it, MD_LIST_MAX) + fixed]
    return "\n".join(lines) + "\n"


_CSS = (":root{--bg:#fff;--fg:#1a1a1a;--mut:#555;--line:#ddd;--acc:#0b5fbb}"
        "@media(prefers-color-scheme:dark){:root{--bg:#161616;--fg:#eaeaea;--mut:#aaa;"
        "--line:#333;--acc:#6aaeff}}"
        "body{margin:0 auto;max-width:44rem;padding:1rem 16px;background:var(--bg);color:var(--fg);"
        "font:16px/1.5 system-ui,sans-serif}h1{font-size:1.5rem}h2{font-size:1.1rem;"
        "border-bottom:1px solid var(--line);padding-bottom:.2rem}.score{font-size:2rem;"
        "color:var(--acc);font-weight:700}.mut{color:var(--mut)}li{overflow-wrap:anywhere}")


def _render_html(m: dict) -> str:
    e = lambda s: _html.escape(_clean(s), quote=True)  # noqa: E731
    parts = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
             '<meta name="viewport" content="width=device-width,initial-scale=1">',
             "<title>Skill doctor</title>", f"<style>{_CSS}</style></head><body>",
             "<h1>Skill doctor</h1>", f'<p class="mut">{e(_headline(m))}</p>',
             f'<p class="score">Score: {m["score"]}/100</p>']
    secs = [(h, _cut(it, MD_LIST_MAX) + fx) for h, it, fx in _sections(m)]
    if m["actions"]:
        secs.insert(0, ("Top actions", m["actions"]))
    for head, items in secs:
        parts.append(f"<h2>{e(head)}</h2><ul>" + "".join(f"<li>{e(i)}</li>" for i in items) + "</ul>")
    parts.append("</body></html>")
    return "\n".join(parts) + "\n"


def render(report: AuditReport, fmt: Literal["text", "md", "html"] = "text") -> str:
    m = _model(report)
    if fmt == "md":
        return _render_md(m)
    if fmt == "html":
        return _render_html(m)
    if fmt == "text":
        return _render_text(m)
    raise ValueError(f"unknown format: {fmt}")


def run_audit(fmt: str, no_jev: bool, context_tokens: int = 200_000,
              project: Optional[str] = None) -> str:
    skill_audit = _sa()
    settings = skill_audit.load_settings(Path(project) if project else None)
    catalog, usage, days, outs, sh = skill_audit.load_inputs()
    jev = None if no_jev else skill_audit.make_jev(catalog)
    import listing_probe
    measured = listing_probe.fresh_for(Path(project) if project else Path.cwd())
    rep = skill_audit.audit(catalog, usage, days, outs, sh, jev=jev if callable(jev) else None,
                           settings=settings, context_tokens=context_tokens, measured=measured)
    return render(rep, fmt)  # type: ignore[arg-type]


def _env_budget() -> bool:
    try:
        return float(os.environ.get("SLASH_COMMAND_TOOL_CHAR_BUDGET", "").strip()) >= 1
    except ValueError:
        return False


def run_visibility(action: str, project: Optional[str], no_jev: bool = False, names_free: bool = False,
                   context_tokens: int = 200_000, force: bool = False,
                   transcripts_dir: Optional[Path] = None) -> tuple[str, int]:
    """visibility plan|apply|undo. `plan` is a dry run; only `apply`/`undo` write a file."""
    import project_usage
    import visibility as vis
    proj = Path(project).resolve() if project else Path.cwd().resolve()
    if action == "undo":
        try:
            st = vis.undo_status(proj)
        except vis.VisibilityError as e:
            return str(e), 1
        except OSError as e:
            return f"error: {e.strerror or e}", 1
        return {None: "nothing to undo", "restored": "restored the previous settings.local.json",
                "merged": vis.PARTIAL_UNDO_MSG,
                "missing": "settings.local.json was removed after apply; original kept at "
                           f"{proj / vis.REL_TARGET}{vis.BACKUP_SUFFIX}"}[st], 0
    skill_audit = _sa()
    settings = skill_audit.load_settings(proj)
    settings["skillOverrides"] = vis.explicit_overrides(proj)      # every scope, not just the last
    catalog, glob, _days, _outs, _sh = skill_audit.load_inputs()
    tdir = transcripts_dir or Path.home() / ".claude" / "projects"
    usage = {"global": glob, "projects": project_usage.usage_by_project(tdir)}
    key = project_usage.project_key(proj)
    entry = usage["projects"].get(key)
    if not entry or not entry.get("joinable"):
        return "no usage history for this project", (1 if action == "apply" else 0)
    jev = None if no_jev else vis.jev_ranked_for(key)
    names = not names_free
    p = vis.plan(proj, catalog, usage, settings, jev_ranked=jev,
                 context_tokens=context_tokens, names_in_budget=names)
    ok, why = vis.worthwhile(p)
    import listing_probe
    meas = listing_probe.load(key)
    if action == "apply":
        try:
            marker = vis.apply(proj, p, force=force)
        except vis.VisibilityError as e:
            return str(e), 1
        except OSError as e:
            return f"error: {e.strerror or e}", 1
        return (f"applied {len(p.name_only)} name-only overrides (settings.local.json was reformatted; "
                f"{marker.name} keeps the original bytes); undo with `visibility undo`"), 0
    b, a = p.before, p.after
    lines = [f"project {key} ({'names count' if names else 'names free'}; {'Jev' if jev is not None else 'keyless'})",
             f"kept (project-used): {b['project_used_kept']}/{b['project_used_total']} -> "
             f"{a['project_used_kept']}/{a['project_used_total']}",
             f"dropped descriptions: {b['dropped']} -> {a['dropped']}",
             *([listing_probe.line(meas)] if meas else []),
             f"name_only: {len(p.name_only)}" + (f" ({', '.join(p.name_only[:10])}"
                                                  f"{', ...' if len(p.name_only) > 10 else ''})" if p.name_only else ""),
             f"worthwhile: {'yes' if ok else 'no'} ({why})"]
    if p.plugin_suggestions:
        lines.append("plugin suggestions (not applied): " + ", ".join(
            f"{n} {u}/{t} used" for n, u, t in p.plugin_suggestions[:5]))
    unused = None
    for label, flag in (("names count", True), ("names free", False)):
        frac, extra = vis.budget_needed(proj, catalog, usage, settings, context_tokens, flag)
        if unused is None:
            unused = len(skill_audit.audit(catalog, glob, _days, _outs, _sh, jev=None, budget_s=0.0,
                                           settings=settings).unused_90d)   # same figure as `cli.py audit`
        # cache both models so the doctor never shows an unmeasured value
        pm = p if flag == names else vis.plan(proj, catalog, usage, settings, jev_ranked=jev,
                                              context_tokens=context_tokens, names_in_budget=flag)
        vis.write_summary(key, flag, pm, frac, extra, unused)
        if frac is None:
            lines.append(f"budget lever ({label}): not reachable at skillListingBudgetFraction <= 0.10")
        elif extra <= 0:
            lines.append(f"budget lever ({label}): already sufficient at the current budget")
        else:
            lines.append(f"budget lever ({label}): skillListingBudgetFraction {frac:.3f} "
                         f"({extra:+d} tokens per session); add \"skillListingBudgetFraction\": {frac:.3f} to settings")
    if _env_budget():
        lines.append("note: SLASH_COMMAND_TOOL_CHAR_BUDGET is set and overrides skillListingBudgetFraction")
    lines.append("apply reformats settings.local.json (indent 2); the backup keeps the original bytes")
    lines.append("simulated: drop order is approximated from global usage; the real effect is measured separately")
    lines.append(f"apply allowed: {'yes' if ok else 'no (use --force to override)'}")
    return "\n".join(lines), 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="skill-router")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("stats", help="show outcome statistics")
    sp.add_argument("--days", type=int, default=30)
    ap_a = sub.add_parser("audit", help="skill doctor: audit installed skills (read-only)")
    ap_a.add_argument("--format", choices=["text", "md", "html"], default="text")
    ap_a.add_argument("--out", help="write the report to FILE (mode 600) instead of stdout")
    ap_a.add_argument("--no-jev", action="store_true", help="keyword overlaps only")
    ap_a.add_argument("--context-tokens", type=int, default=200_000,
                      help="assumed context window for the listing budget (default 200000)")
    ap_a.add_argument("--project", help="project dir whose .claude settings apply (default: cwd)")
    ap_l = sub.add_parser("listing", help="measure/show the skill listing Claude Code really sends")
    ap_l.add_argument("action", choices=["measure", "show"])
    ap_l.add_argument("--project", help="project dir (default: cwd)")
    ap_v = sub.add_parser("visibility", help="plan, apply or undo a project's skill visibility (plan is a dry run)")
    ap_v.add_argument("action", choices=["plan", "apply", "undo"])
    ap_v.add_argument("--project", help="project dir (default: cwd)")
    ap_v.add_argument("--no-jev", action="store_true", help="usage-based relevance only")
    ap_v.add_argument("--names-free", action="store_true", help="assume skill names do not count toward the budget")
    ap_v.add_argument("--context-tokens", type=int, default=200_000)
    ap_v.add_argument("--force", action="store_true", help="apply even if the plan is not worthwhile")
    args = ap.parse_args(argv)
    if args.cmd == "listing":
        import listing_probe
        proj = Path(args.project).expanduser() if args.project else Path.cwd()
        return (listing_probe.main_measure(proj) if args.action == "measure"
                else listing_probe.main_show(proj))
    if args.cmd == "visibility":
        text, code = run_visibility(args.action, args.project, args.no_jev, args.names_free,
                                    max(1, args.context_tokens), args.force)
        print(text)
        return code
    if args.cmd == "audit":
        text = run_audit(args.format, args.no_jev, max(1, args.context_tokens), args.project)
        if args.out:
            fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                         | getattr(os, "O_NOFOLLOW", 0), 0o600)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(text if text.endswith("\n") else text + "\n")
            os.chmod(args.out, 0o600)
        else:
            print(text, end="" if text.endswith("\n") else "\n")
        return 0
    print(stats(max(1, args.days)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
