#!/usr/bin/env python3
"""Paired routing-description experiment on frozen, independently labeled prompts.

Same chooser/model/input policy/threshold; only index routing metadata differs.
No retries or standalone-tier calls. Never writes live caches or router state.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import time

import jev_choose
from benchmark_jev import digest, metrics, primary


def is_correct(row):
    return row["prediction"] in row["expected"] if row["expected"] else row["prediction"] is None


def paired_counts(rows):
    arms = {arm: {r["id"]: r for r in rows if r["strategy"] == arm} for arm in ("before", "after")}
    if set(arms["before"]) != set(arms["after"]):
        raise ValueError("paired experiment has unmatched cases")
    pairs = [(is_correct(arms["after"][k]), is_correct(v)) for k, v in arms["before"].items()]
    return {"after_only_correct": sum(a and not b for a, b in pairs),
            "before_only_correct": sum(b and not a for a, b in pairs),
            "both_correct": sum(a and b for a, b in pairs),
            "both_wrong": sum(not a and not b for a, b in pairs)}


def main():
    ap = argparse.ArgumentParser(__doc__)
    for name in ("cases", "before", "after", "out"):
        ap.add_argument("--" + name, required=True, type=Path)
    ap.add_argument("--max-calls", type=int, default=80)
    ap.add_argument("--timeout", type=float, default=1.2)
    args = ap.parse_args()
    cases = json.loads(args.cases.read_text())
    if not cases or len(cases) * 2 > args.max_calls:
        ap.error("case count exceeds explicit request budget or is empty")
    if len({c["id"] for c in cases}) != len(cases):
        ap.error("duplicate case IDs")
    if args.out.exists():
        ap.error("output exists; refusing to replay")
    if not os.environ.get("TYPESAFE_API_KEY"):
        ap.error("supply provider key through runtime environment")
    if jev_choose.ROUTE_AT != .8:
        ap.error("experiment requires unchanged 0.8 threshold")
    indexes = {arm: json.loads(getattr(args, arm).read_text())["entries"] for arm in ("before", "after")}
    treatment = {"routing_summary", "routing_description_version", "routing_description_sha256", "source_kind", "kind"}
    for arm, entries in indexes.items():
        if len({e["name"] for e in entries}) != len(entries):
            ap.error("duplicate index names")
    def untreated(entries):
        return {e["name"]: {k: v for k, v in e.items() if k not in treatment} for e in entries}
    if untreated(indexes["before"]) != untreated(indexes["after"]):
        ap.error("non-treatment catalog fields changed")
    if len({(c["prompt"], c.get("context", "")) for c in cases}) != len(cases):
        ap.error("duplicate prompt/context pairs would reuse cached results")
    allowed = {e["name"] for e in indexes["before"] if e.get("type") not in jev_choose.NOT_ROUTABLE}
    if any(not set(c["expected_skills"]) <= allowed for c in cases):
        ap.error("a label names a non-routable skill")
    args.out.mkdir(parents=True, mode=0o700)
    os.environ["SKILL_ROUTER_JEV"] = "1"
    os.environ["SKILL_ROUTER_NO_LEARN"] = "1"
    manifest = {"cases_sha256": digest(args.cases), "before_sha256": digest(args.before),
                "after_sha256": digest(args.after), "chooser_sha256": digest(jev_choose.__file__),
                "model": jev_choose.MODEL, "threshold": jev_choose.ROUTE_AT, "timeout": args.timeout,
                "max_calls": args.max_calls, "retries": 0, "n": len(cases), "started_at": time.time(),
                "label_source": "fresh historical user prompts; labels frozen before predictions",
                "treatment": "routing summaries and three source-verified kind corrections",
                "scope": "chooser primary skill only; full hook and downstream execution not measured"}
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    rows = []
    for i, case in enumerate(cases):
        for arm in (("before", "after") if i % 2 == 0 else ("after", "before")):
            jev_choose.CACHE = args.out / arm / "cache"
            start = time.monotonic()
            choice = jev_choose.choose(case["prompt"], context=case.get("context", ""),
                                       entries=indexes[arm], timeout=args.timeout,
                                       session_id="description-benchmark", prompt_id=case["id"])
            row = {"id": case["id"], "bucket": case["bucket"], "strategy": arm,
                   "expected": case["expected_skills"], "expected_tier": case.get("expected_tier", "unknown"),
                   "prediction": primary(choice), "choice": asdict(choice) if choice else None,
                   "elapsed_ms": int((time.monotonic() - start) * 1000),
                   "failure": jev_choose.LAST_FAILURE if choice is None else "",
                   "input_tokens": choice.tokens if choice else 0}
            rows.append(row)
            with (args.out / "rows.jsonl").open("a") as f:
                f.write(json.dumps(row) + "\n")
        print(f"{i+1}/{len(cases)} paired cases completed", flush=True)
    summary = {}
    for arm in ("before", "after"):
        rr = [r for r in rows if r["strategy"] == arm]
        summary[arm] = {"all": metrics(rr), "buckets": {
            b: metrics([r for r in rr if r["bucket"] == b]) for b in sorted({r["bucket"] for r in rr})}}
    summary["paired"] = paired_counts(rows)
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    events = [json.loads(line) for arm in ("before", "after")
              for line in (args.out / arm / "jev-decisions.jsonl").read_text().splitlines()]
    manifest.update(completed_at=time.time(), request_slots_executed=len(cases)*2,
                    provider_attempts=sum(e["provider_attempted"] for e in events),
                    cache_hits=sum(e["cache_hit"] for e in events))
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({arm: summary[arm]["all"] for arm in ("before", "after")}, indent=2))
    print(json.dumps(summary["paired"]))


if __name__ == "__main__":
    main()
