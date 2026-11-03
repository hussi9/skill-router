#!/usr/bin/env python3
"""
benchmark_shortlist.py — which shortlist should the router show, with and without Jev?

Variants, all scored on whether an acceptable skill is in the first 1 / 3 names:

    nokey-lex     keyword retrieval (what a keyless user has today)
    nokey-fused   keyword + local semantic retrieval, reciprocal-rank fused
    jev-route     Jev's whole-index Choice argmax, only at >= 0.8 (the shipped card)
    jev-dist      Jev's whole-index Choice, top 3 by probability across all chunks
    jev-rerank    fused top 8 -> one Noul per candidate in the SAME request ("does this skill fit?")
    jev-blend     jev-dist and jev-rerank, reciprocal-rank fused

One Jev request per prompt carries every question, as production would. Responses are
cached under --out so a rerun costs nothing. Needs fastembed (repo .venv) for the semantic
leg and TYPESAFE_API_KEY (env or refresh_env cache) for the Jev legs.

    .venv/bin/python -I scripts/benchmark_shortlist.py --labeled L.json --mined M.json --out DIR
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
os.environ.setdefault("SKILL_ROUTER_NO_EMBED", "1")

import benchmark_nokey as bn  # noqa: E402  type: ignore[import-not-found]
import index_match as im  # noqa: E402  type: ignore[import-not-found]
import jev_choose as jc  # noqa: E402  type: ignore[import-not-found]

K_RERANK = 8
NOT_TARGET_TYPES = ("builtin", "plugin-command", "command", "agent")


def targets() -> list[dict]:
    raw = json.loads(Path(im.INDEX_FILE).read_text(encoding="utf-8"))["entries"]
    return [e for e in raw if e.get("type") not in NOT_TARGET_TYPES and e.get("kind") not in ("process", "meta")
            and e["name"] != "skill-router"]


def doc_text(e: dict) -> str:
    use = "; ".join((e.get("use_when") or [])[:3])
    return f"{e['name'].replace('-', ' ')}: {e.get('routing_summary') or e.get('description', '')} {use}"[:600]


class Semantic:
    def __init__(self, docs: list[dict]):
        import numpy as np
        from fastembed import TextEmbedding
        self.np = np
        self.model = TextEmbedding("BAAI/bge-small-en-v1.5")
        d = np.array(list(self.model.embed([doc_text(e) for e in docs])))
        self.D = d / np.linalg.norm(d, axis=1, keepdims=True)
        self.names = [e["name"] for e in docs]

    def rank(self, prompt: str, k: int = 50) -> list[str]:
        q = self.np.array(list(self.model.query_embed([prompt])))[0]
        s = self.D @ (q / self.np.linalg.norm(q))
        return [self.names[i] for i in self.np.argsort(-s)[:k]]


def rrf(*orders: list[str], k: int = 10) -> list[str]:
    score: dict[str, float] = {}
    for order in orders:
        for r, n in enumerate(order):
            score[n] = score.get(n, 0.0) + 1.0 / (k + r)
    return sorted(score, key=lambda n: -score[n])


def jev_request(prompt: str, cands: list[dict], entries: list[dict]) -> tuple[dict, dict]:
    questions, keymap = jc.build_questions(entries)
    questions.pop("tier", None)
    questions.pop("path", None)
    state = {"request": prompt[: jc.PROMPT_CHARS],
             "candidates": {f"c{i}": doc_text(e) for i, e in enumerate(cands)}}
    for i, _ in enumerate(cands):
        questions[f"fit_{i}"] = {
            "type": "noul",
            "instructions": (f"Would the agent skill described in `candidates.c{i}` be the right specialist to load "
                             "for the developer's `request`? " + jc._TYPO_NOTE),
            "criteria": {"true": "The skill's subject and purpose match what the request asks to be done",
                         "false": "The skill is unrelated, only loosely related, or the request is chat or a question"}}
    return {"model": jc.MODEL, "state": state, "questions": questions}, keymap


def dist_order(answers: dict, keymap: dict) -> tuple[list[str], float]:
    """All named options across domain chunks by probability; and the shipped argmax confidence."""
    probs: list[tuple[float, str]] = []
    for qid, keys in keymap.items():
        if not qid.startswith("domain_"):
            continue
        a = answers.get(qid) or {}
        for key, p in (a.get("probabilities") or {}).items():
            if key in keys:
                probs.append((float(p), keys[key]))
    probs.sort(reverse=True)
    pick = jc._pick(answers, [q for q in keymap if q.startswith("domain_")], keymap)
    return [n for _, n in probs], (pick.confidence if pick and pick.name else 0.0)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labeled")
    ap.add_argument("--mined")
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-jev", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    (out / "jev").mkdir(parents=True, exist_ok=True)
    docs = targets()
    by_name = {e["name"]: e for e in docs}
    allowed = set(by_name)
    entries = jc.load_entries()
    sem = Semantic(docs)
    key = "" if a.no_jev else jc._key()
    report: dict = {}
    spend = {"calls": 0, "cached": 0, "input_tokens": 0, "failures": 0, "ms": []}
    for set_name, cases in bn.load(a.labeled, a.mined).items():
        rows = []
        for c in cases:
            prompt, expected = c["prompt"], set(c["expected"])
            lex = [m.name for m in im.rank(prompt, limit=50) if m.name in allowed]
            fused = rrf(lex, sem.rank(prompt))
            row = {"ctrl": not expected, "orders": {"nokey-lex": lex, "nokey-fused": fused}, "noul_max": None}
            if key:
                cands = [by_name[n] for n in fused[:K_RERANK]]
                body, keymap = jev_request(prompt, cands, entries)
                h = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
                f = out / "jev" / f"{h}.json"
                if f.exists():
                    data = json.loads(f.read_text())
                    spend["cached"] += 1
                else:
                    t = time.monotonic()
                    data = jc._post(body, key, 20.0)
                    spend["ms"].append(int((time.monotonic() - t) * 1000))
                    spend["calls"] += 1
                    if data:
                        f.write_text(json.dumps(data))
                        spend["input_tokens"] += jc._usage(data)
                answers = (data or {}).get("answers") or {}
                if not answers:
                    spend["failures"] += 1
                dist, conf = dist_order(answers, keymap)
                nouls = [(float((answers.get(f"fit_{i}") or {}).get("noul") or 0.0), e["name"])
                         for i, e in enumerate(cands)]
                nouls.sort(reverse=True)
                rerank = [n for _, n in nouls]
                row["orders"].update({
                    "jev-route": dist[:1] if conf >= jc.ROUTE_AT else [],
                    "jev-dist": dist, "jev-rerank": rerank, "jev-blend": rrf(dist[:10], rerank)})
                row["noul_max"] = nouls[0][0] if nouls else 0.0
                row["noul_top3"] = [p for p, _ in nouls[:3]]
            row["hit"] = {v: [min([o.index(e) for e in expected if e in o] or [99])] for v, o in row["orders"].items()}
            rows.append(row)
        summary = {}
        for v in rows[0]["orders"]:
            pos = [r["hit"][v][0] for r in rows if not r["ctrl"]]
            summary[v] = {"top1": sum(p < 1 for p in pos), "top3": sum(p < 3 for p in pos), "of": len(pos)}
        # How a Noul gate would treat chat controls vs tasks (the burden question).
        gate = {}
        if key:
            for t in (0.3, 0.5, 0.7):
                tasks = [r for r in rows if not r["ctrl"]]
                ctrls = [r for r in rows if r["ctrl"]]
                gate[str(t)] = {
                    "tasks_shown": sum((r["noul_max"] or 0) >= t for r in tasks),
                    "tasks_shown_and_right_in_3": sum((r["noul_max"] or 0) >= t and r["hit"]["jev-rerank"][0] < 3
                                                      for r in tasks),
                    "controls_shown": sum((r["noul_max"] or 0) >= t for r in ctrls), "controls": len(ctrls)}
        report[set_name] = {"variants": summary, "noul_gate": gate}
    spend["median_ms"] = sorted(spend["ms"])[len(spend["ms"]) // 2] if spend["ms"] else 0
    spend.pop("ms")
    report["spend"] = spend
    (out / "summary.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
