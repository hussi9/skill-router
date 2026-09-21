#!/usr/bin/env python3
"""What the router would COST, not how often it is right.

Two samples from ~/.claude/projects transcripts (last 75 days, router's own sessions excluded):
  skill turns    prompts on which Claude invoked a Skill (the accuracy set)
  no-skill turns prompts on which it did not — the majority of real traffic, and where a route
                 card is either a rescued miss or pure overhead. Seeded random sample.

For each prompt the real router.route() runs with Jev on, and we record whether a card, a
suggestion or nothing came out, the size of what would be injected, and the size of the
SKILL.md the card tells the model to load. Tokens are estimated as chars / 4.

Writes into the CURRENT WORKING DIRECTORY. Run from a scratch dir with TYPESAFE_API_KEY set.
"""
from __future__ import annotations

import importlib.util
import json
import os
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))
os.environ.update({"SKILL_ROUTER_JEV": "1", "SKILL_ROUTER_NO_EMBED": "1", "SKILL_ROUTER_NO_LEARN": "1",
                   "SKILL_ROUTER_LLM_SKIP_ANTHROPIC": "1"})
import jev_choose  # type: ignore[import-not-found]  # noqa: E402
import router  # type: ignore[import-not-found]  # noqa: E402

spec = importlib.util.spec_from_file_location("prov", Path(__file__).with_name("router-eval-provider.py"))
prov = importlib.util.module_from_spec(spec); spec.loader.exec_module(prov)   # reuse its miner + NOISE
jev_choose.CACHE = Path.cwd() / "jev-cache"
jev_choose.TIMEOUT_S = 20
N_NOSKILL = int(os.environ.get("N_NOSKILL", "150"))
tok = lambda s: len(s) // 4


def mine_noskill() -> list[dict]:
    """User prompts whose turn contained no Skill call at all."""
    out, seen = [], set()
    for f in prov.ROOT.glob("*/*.jsonl"):
        if "skill-router" in f.parent.name or f.stat().st_mtime < prov.CUTOFF:
            continue
        pending, ctx, last, used = None, "", "", False
        try:
            for line in open(f, encoding="utf-8", errors="ignore"):
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
                    t = prov.text_of(msg, "user").strip()
                    if not t:
                        continue
                    if pending and not used and pending[:120].lower() not in seen:
                        seen.add(pending[:120].lower())
                        out.append({"prompt": pending, "context": ctx[-300:]})
                    bad = any(n in t for n in prov.NOISE) or t.startswith("/") or not (3 <= len(t) <= 700)
                    pending, ctx, used = (None, "", False) if bad else (t, last, False)
                else:
                    if any(isinstance(p, dict) and p.get("type") == "tool_use" and p.get("name") == "Skill"
                           for p in msg.get("content") or []):
                        used = True
                    said = prov.text_of(msg, "assistant").strip()
                    if said:
                        last = said
        except OSError:
            continue
    random.Random(20260921).shuffle(out)
    return out[:N_NOSKILL]


def skill_md_tokens(name: str) -> int:
    short = name.split(":")[-1]
    hits = [Path.home() / ".claude/skills" / short / "SKILL.md",
            *Path.home().glob(f".claude/plugins/cache/*/*/*/skills/{short}/SKILL.md")]
    for h in hits:
        if h.is_file():
            return tok(h.read_text(errors="ignore"))
    return 0


def run(rows: list[dict]) -> list[dict]:
    res = []
    for r in rows:
        router.PREV_ASSISTANT = r.get("context", "")
        path, chain, _, text = router.route(r["prompt"])
        kind = "card" if chain else ("suggestion" if text else "silent")
        res.append({**r, "kind": kind, "path": path, "skills": [s.skill for s in chain],
                    "inject_tok": tok(text), "tier": router.LAST_CARD.tier if chain else "",
                    "decided_by": router.LAST_CARD.decided_by if chain else "",
                    "skill_md_tok": sum(skill_md_tokens(s.skill) for s in chain)})
    return res


def show(title: str, res: list[dict]) -> None:
    n = len(res)
    cards = [r for r in res if r["kind"] == "card"]
    sugg = [r for r in res if r["kind"] == "suggestion"]
    print(f"\n== {title}: {n} prompts")
    print(f"  card {len(cards)} ({len(cards) / n:.0%}) | suggestion {len(sugg)} ({len(sugg) / n:.0%}) | silent {n - len(cards) - len(sugg)}")
    if cards:
        it = sorted(r["inject_tok"] for r in cards); sm = sorted(r["skill_md_tok"] for r in cards)
        print(f"  card size: median {it[len(it) // 2]} tok, max {it[-1]} | hard-tier {sum(r['tier'] == 'hard' for r in cards)}")
        print(f"  SKILL.md the card asks for: median {sm[len(sm) // 2]} tok, mean {sum(sm) // len(sm)}, max {sm[-1]}")
    inj = sum(r["inject_tok"] for r in res)
    print(f"  injected per prompt, averaged over ALL prompts: {inj / n:.0f} tok")
    print(f"  SKILL.md per prompt if every card is obeyed:     {sum(r['skill_md_tok'] for r in res) / n:.0f} tok")


if __name__ == "__main__":
    skill_rows = [p for p in prov.mine() if not p["short"]]
    nos = mine_noskill()
    a, b = run(skill_rows), run(nos)
    for r in a:
        r["right"] = r["truth"] in [s.split(":")[-1] for s in r["skills"]]
    (Path.cwd() / "router-eval-cost-results.json").write_text(json.dumps({"skill": a, "noskill": b}, indent=1))
    show("turns where Claude invoked a skill by itself", a)
    ca = [r for r in a if r["kind"] == "card"]
    print(f"  cards naming the skill Claude used anyway: {sum(r['right'] for r in ca)}/{len(ca)} "
          f"| cards naming a different skill: {sum(not r['right'] for r in ca)} "
          f"(extra SKILL.md if obeyed: {sum(r['skill_md_tok'] for r in ca if not r['right'])} tok total)")
    show("turns where Claude invoked NO skill", b)
    print("\n  what it would have carded on no-skill turns:")
    for r in b:
        if r["kind"] == "card":
            print(f"    {r['tier']:<4} {'+'.join(s.split(':')[-1] for s in r['skills']):<44} | {r['prompt'][:70]!r}")
