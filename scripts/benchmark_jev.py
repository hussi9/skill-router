#!/usr/bin/env python3
"""Paired, bounded chooser evaluation; synthetic frozen labels, never live hook state.

No retries. Maximum four requests per case (old/new skill and standalone tier).
Keep the deployed model and threshold fixed. This evaluates classifier decisions,
not downstream model responses, the full router's prefilters, or actual savings.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import statistics
import sys
import time
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import index_match
import jev_choose


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def metrics(rows):
    routed = [r for r in rows if r["prediction"]]
    positive = [r for r in rows if r["expected"]]
    negative = [r for r in rows if not r["expected"]]
    correct = lambda r: r["prediction"] in r["expected"] if r["expected"] else not r["prediction"]
    hit = sum(correct(r) for r in routed)
    return {"n": len(rows), "correct_including_abstentions": sum(correct(r) for r in rows),
            "positive_hits": sum(correct(r) for r in positive), "positive_n": len(positive),
            "routed": len(routed), "route_precision": hit / len(routed) if routed else None,
            "coverage": len(routed) / len(rows) if rows else 0,
            "false_routes_on_no_skill": sum(bool(r["prediction"]) for r in negative),
            "no_skill_n": len(negative), "failures": sum(bool(r.get("failure")) for r in rows),
            "tier_correct": sum(r.get("tier") == r["expected_tier"] for r in rows) if any("tier" in r for r in rows) else None,
            "tier_downgrade_on_heavy": sum(r["expected_tier"] == "heavy" and r.get("model") in ("haiku", "sonnet") for r in rows),
            "median_ms": statistics.median(r.get("elapsed_ms", 0) for r in rows) if rows else 0,
            "input_tokens": sum(r.get("input_tokens", 0) for r in rows
                                if not (r.get("choice") or {}).get("cached")),
            "input_tokens_scope": "successful uncached skill calls only; standalone tier excluded"}


def primary(c):
    # Production prefers a confident domain, then a confident process. Never
    # choose the leg using the expected label (the old evaluation did that).
    if c is None:
        return None
    for p in (c.domain, c.process):
        if p.name and p.confidence >= .8:
            return p.name
    return None


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--cases", type=Path, required=True)
    ap.add_argument("--index", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--baseline", type=Path, required=True)
    ap.add_argument("--timeout", type=float, default=1.2)
    ap.add_argument("--max-calls", type=int, default=160)
    args = ap.parse_args()
    cases = json.loads(args.cases.read_text())
    if len(cases) * 4 > args.max_calls:
        ap.error("case count exceeds explicit request budget")
    if args.out.exists():
        ap.error("output already exists; use a new directory to prevent accidental replay")
    if not os.environ.get("TYPESAFE_API_KEY"):
        ap.error("supply TYPESAFE_API_KEY via runtime environment")
    args.out.mkdir(parents=True)
    spec = importlib.util.spec_from_file_location("jev_baseline", args.baseline)
    old = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = old
    spec.loader.exec_module(old)
    entries = json.loads(args.index.read_text())["entries"]
    allowed = {e["name"] for e in entries if e.get("type") not in jev_choose.NOT_ROUTABLE}
    assert all(set(c["expected_skills"]) <= allowed for c in cases)
    os.environ["SKILL_ROUTER_JEV"] = "1"
    os.environ["SKILL_ROUTER_NO_LEARN"] = "1"
    for name, module in (("old", old), ("new", jev_choose)):
        module.CACHE = args.out / name / "cache"
    manifest = {"cases_sha256": digest(args.cases), "index_sha256": digest(args.index),
                "old_sha256": digest(args.baseline), "new_sha256": digest(jev_choose.__file__),
                "model": jev_choose.MODEL, "threshold": .8, "timeout": args.timeout,
                "max_calls": args.max_calls, "retries": 0, "n": len(cases),
                "started_at": time.time(), "label_source": "independent synthetic policy labels; not owner adjudicated"}
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    rows = []
    for i, case in enumerate(cases):
        common = {"id": case["id"], "bucket": case["bucket"], "expected": case["expected_skills"],
                  "expected_tier": case["expected_tier"]}
        start = time.monotonic()
        ranked = index_match.rank(case["prompt"], limit=len(entries), index_path=str(args.index))
        ranked = [m for m in ranked if m.name in allowed and m.name != "skill-router"]
        lexical = {**common, "strategy": "lexical_top1", "prediction": ranked[0].name if ranked else None,
                   "elapsed_ms": int((time.monotonic() - start) * 1000)}
        rows.append(lexical)
        # Alternate order across cases to reduce systematic provider timing bias.
        modules = [("old", old), ("new", jev_choose)]
        if i % 2:
            modules.reverse()
        for name, module in modules:
            start = time.monotonic()
            module.LAST_FAILURE = ""
            c = module.choose(case["prompt"], context=case.get("context", ""), entries=entries, timeout=args.timeout)
            skill_ms = int((time.monotonic() - start) * 1000)
            failure = module.LAST_FAILURE if c is None else ""
            tier_start = time.monotonic()
            # Task hook has no preceding conversational context; both arms receive
            # the exact same standalone task string, as in production.
            t = module.tier_only(case["prompt"], timeout=args.timeout)
            row = {**common, "strategy": "jev_" + name, "prediction": primary(c),
                   "choice": asdict(c) if c else None, "elapsed_ms": skill_ms,
                   "failure": failure or ("unclassified_failure" if c is None else ""),
                   "input_tokens": c.tokens if c else 0,
                   "tier": t.name if t else None, "tier_confidence": t.confidence if t else 0,
                   "model": module.model_for(t), "tier_elapsed_ms": int((time.monotonic() - tier_start) * 1000)}
            rows.append(row)
        with (args.out / "rows.jsonl").open("a") as f:
            for row in rows[-3:]:
                f.write(json.dumps(row) + "\n")
        print(f"{i + 1}/{len(cases)} completed", flush=True)
    summary = {}
    for strategy in ("lexical_top1", "jev_old", "jev_new"):
        rr = [r for r in rows if r["strategy"] == strategy]
        summary[strategy] = {"all": metrics(rr), "buckets": {
            b: metrics([r for r in rr if r["bucket"] == b]) for b in sorted({r["bucket"] for r in rr})}}
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    by = {s: {r["id"]: r for r in rows if r["strategy"] == s} for s in summary}
    correct = lambda r: r["prediction"] in r["expected"] if r["expected"] else not r["prediction"]
    paired = {}
    for baseline in ("lexical_top1", "jev_old"):
        pairs = [(correct(by["jev_new"][k]), correct(v)) for k, v in by[baseline].items()]
        paired[baseline] = {"new_only_correct": sum(a and not b for a, b in pairs),
                            "baseline_only_correct": sum(b and not a for a, b in pairs),
                            "both_correct": sum(a and b for a, b in pairs),
                            "both_wrong": sum(not a and not b for a, b in pairs)}
    (args.out / "paired.json").write_text(json.dumps(paired, indent=2) + "\n")
    manifest["completed_at"] = time.time()
    manifest["request_slots_executed"] = len(cases) * 4
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({k: v["all"] for k, v in summary.items()}, indent=2))


if __name__ == "__main__":
    main()
