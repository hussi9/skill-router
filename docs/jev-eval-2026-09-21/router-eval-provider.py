#!/usr/bin/env python3
"""Evaluate scripts/jev_choose.py (the shipped provider, not a copy of its logic) on real
(prompt -> first Skill invoked) pairs mined from ~/.claude/projects transcripts.

Differs from router-eval-build.py in two ways, both needed for the context question:
  - it also records the tail of the assistant message that preceded each prompt
  - it keeps short prompts (>= 3 chars) and flags them, because "yes please continue"
    is exactly what the context is meant to rescue; the original set dropped < 20 chars

Writes next to the CURRENT WORKING DIRECTORY, never next to itself: run it from a scratch dir.
    cd <scratch> && TYPESAFE_API_KEY=... python3 <repo>/docs/jev-eval-2026-09-21/router-eval-provider.py
"""
from __future__ import annotations

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
import jev_choose  # type: ignore[import-not-found]  # noqa: E402

OUT = Path.cwd()
jev_choose.CACHE = OUT / "jev-cache"
ROOT = Path.home() / ".claude" / "projects"
CUTOFF = time.time() - 75 * 86400
NOISE = ("[skill-router]", "Stop hook", "PreToolUse", "IRON RULE", "hookSpecificOutput", "<system-reminder>",
         "<command-name>", "<local-command", "Caveat:", "<task-notification", "[Request interrupted",
         "This session is being continued")
ENTRIES = jev_choose.load_entries()
short = lambda s: s.split(":")[-1]
# Truths Jev is never offered (slash commands, harness builtins) are counted, not scored.
KIND = {short(e["name"]): e.get("kind") for e in ENTRIES
        if e.get("type") not in jev_choose.NOT_ROUTABLE and e["name"] != jev_choose.SELF_SKILL}
INDEXED = {short(e["name"]) for e in ENTRIES}
UNREACHABLE: list[str] = []


def text_of(msg: dict, role: str) -> str:
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        if role == "user" and any(isinstance(p, dict) and p.get("type") == "tool_result" for p in c):
            return ""
        return "\n".join(p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
    return ""


def mine() -> list[dict]:
    pairs, seen = [], set()
    files = sorted((p for p in ROOT.glob("*/*.jsonl") if p.stat().st_mtime > CUTOFF),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        if "skill-router" in f.parent.name:
            continue
        pending, last_assistant, ctx, pending_ts = None, "", "", ""
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if '"type":"user"' not in line and '"type":"assistant"' not in line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if ev.get("isSidechain"):
                        continue
                    msg = ev.get("message") or {}
                    if ev.get("type") == "user":
                        t = text_of(msg, "user").strip()
                        if t:
                            bad = any(n in t for n in NOISE) or t.startswith("/") or not (3 <= len(t) <= 700)
                            pending, ctx = (None, "") if bad else (t, last_assistant)
                            pending_ts = str(ev.get("timestamp") or "")
                    elif ev.get("type") == "assistant":
                        said = text_of(msg, "assistant").strip()
                        if pending:
                            for part in msg.get("content") or []:
                                if isinstance(part, dict) and part.get("type") == "tool_use" and part.get("name") == "Skill":
                                    s = str((part.get("input") or {}).get("skill", "")).strip().lstrip("/")
                                    k = pending[:120].lower()
                                    if s and k not in seen and short(s) in INDEXED and short(s) not in KIND:
                                        seen.add(k)
                                        UNREACHABLE.append(short(s))
                                    elif s and k not in seen and short(s) in KIND:
                                        seen.add(k)
                                        pairs.append({"prompt": pending, "truth": short(s), "context": ctx[-300:], "ts": pending_ts,
                                                      "short": len(pending) < 20})
                                    pending = None
                                    break
                        if said:
                            last_assistant = said
        except OSError:
            continue
    return pairs


def run(p: dict) -> dict:
    out = dict(p)
    for tag, ctx in (("plain", ""), ("ctx", p["context"])):
        if tag == "ctx" and not ctx:
            out[tag] = out["plain"]
            continue
        c = jev_choose.choose(p["prompt"], context=ctx, entries=ENTRIES, timeout=20)
        out[tag] = None if c is None else {
            "d": short(c.domain.name) if c.domain.name else None, "dc": c.domain.confidence,
            "p": short(c.process.name) if c.process.name else None, "pc": c.process.confidence,
            "path": c.path, "pathc": c.path_confidence, "ms": c.ms, "tok": c.tokens}
    return out


def leg(r: dict, tag: str) -> tuple[str | None, float]:
    a = r[tag]
    return (a["p"], a["pc"]) if KIND.get(r["truth"]) == "process" else (a["d"], a["dc"])


def report(rows: list[dict], title: str) -> None:
    rows = [r for r in rows if r["plain"] and r["ctx"]]
    if not rows:
        return
    print(f"\n== {title}: {len(rows)} pairs")
    for tag in ("plain", "ctx"):
        hit = [leg(r, tag)[0] == r["truth"] for r in rows]
        either = [r["truth"] in (r[tag]["d"], r[tag]["p"]) for r in rows]
        routed = [(leg(r, tag)[0] == r["truth"]) for r in rows if leg(r, tag)[1] >= 0.8 and leg(r, tag)[0]]
        sugg = [(leg(r, tag)[0] == r["truth"]) for r in rows if 0.5 <= leg(r, tag)[1] < 0.8 and leg(r, tag)[0]]
        proc = [r for r in rows if KIND.get(r["truth"]) == "process"]
        print(f"  [{tag:5}] right-leg {sum(hit)}/{len(rows)} | either-leg {sum(either)}/{len(rows)} | "
              f"process {sum(leg(r, tag)[0] == r['truth'] for r in proc)}/{len(proc)} | "
              f"route>=0.8: fires {len(routed)}, right {sum(routed)} | suggest 0.5-0.8: fires {len(sugg)}, right {sum(sugg)}")
        q = [r for r in rows if r[tag]["path"] == "QUESTION"]
        print(f"          path=QUESTION on {len(q)} of these skill-invoking prompts ({sum(r[tag]['pathc'] >= 0.8 for r in q)} at >=0.8) "
              f"— each would be wrongly silenced if QUESTION were trusted")
    ms = sorted(r["plain"]["ms"] for r in rows)
    tok = sorted(r["plain"]["tok"] for r in rows)
    print(f"  latency median {ms[len(ms) // 2]} ms, p90 {ms[int(len(ms) * .9)]} ms, max {ms[-1]} ms | "
          f"{tok[len(tok) // 2]} input tokens | over 2000 ms: {sum(m > 2000 for m in ms)}")


if __name__ == "__main__":
    pairs = mine()
    from collections import Counter
    print(f"not scored — truth is a builtin/command Jev is never offered: {dict(Counter(UNREACHABLE))}")
    print(f"mined {len(pairs)} pairs ({sum(p['short'] for p in pairs)} short, "
          f"{sum(bool(p['context']) for p in pairs)} with a previous assistant message); index {len(ENTRIES)} entries")
    with ThreadPoolExecutor(3) as ex:
        rows = list(ex.map(run, pairs))
    (OUT / "router-eval-provider-results.json").write_text(json.dumps(rows, indent=1))
    failed = [r for r in rows if not r["plain"]]
    if failed:
        print(f"FAILED calls: {len(failed)}")
    report([r for r in rows if not r["short"]], "original filter (20-700 chars)")
    report([r for r in rows if r["short"]], "short follow-ups (<20 chars)")
    report([r for r in rows if r["context"]], "all pairs that had a previous assistant message")
    print("\nchanged by context (plain -> ctx):")
    for r in rows:
        if r["plain"] and r["ctx"] and leg(r, "plain")[0] != leg(r, "ctx")[0]:
            a, b = leg(r, "plain"), leg(r, "ctx")
            mark = "FIXED " if b[0] == r["truth"] else ("BROKE " if a[0] == r["truth"] else "other ")
            print(f"  {mark} truth={r['truth']:<28} {str(a[0]):<24}({a[1]:.2f}) -> {str(b[0]):<24}({b[1]:.2f}) | {r['prompt'][:50]!r}")
