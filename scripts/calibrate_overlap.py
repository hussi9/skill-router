#!/usr/bin/env python3
"""
Calibrate the keyless duplicate-detection threshold against Jev's verdicts.

Candidate same-kind pairs (description-token Jaccard >= 0.2) are judged by Jev; a pair is
a positive when Jev's probability >= 0.8. We then pick the Jaccard threshold (0.20-0.90,
step 0.05) that maximises F1 against those labels. Needs TYPESAFE_API_KEY; without one
nothing changes. Labels hold real skill names, so they are written mode 600 to a
git-ignored local file and never printed.

Run: python3 scripts/calibrate_overlap.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import skill_audit as sa  # type: ignore[import-not-found]  # noqa: E402
from skill_audit import _jaccard_pairs, _routable, load_inputs, make_jev  # type: ignore[import-not-found]  # noqa: E402

CANDIDATE_AT = 0.2
LABEL_AT = sa.JEV_OVERLAP_AT
MIN_LABELS = 20
MIN_F1 = 0.5
Row = tuple[str, str, float, float]


def labels_path() -> Path:
    env = os.environ.get("SKILL_ROUTER_OVERLAP_LABELS")
    return Path(env) if env else sa.CLAUDE_DIR / "skill_router_cache" / "overlap-labels.jsonl"


def collect(catalog: list[dict], jev, max_pairs: int = 400) -> list[Row]:
    """(a, b, jaccard, jev_prob) over candidate pairs; evenly sampled when over max_pairs."""
    pairs = _jaccard_pairs(_routable(catalog), CANDIDATE_AT)
    if len(pairs) > max_pairs:
        step = len(pairs) / max_pairs
        pairs = [pairs[int(i * step)] for i in range(max_pairs)]
    rows: list[Row] = []
    collect.attempted = len(pairs)  # type: ignore[attr-defined]
    for i in range(0, len(pairs), sa.JEV_BATCH):
        batch = pairs[i:i + sa.JEV_BATCH]
        keys = [(a, b) for a, b, _ in batch]
        try:
            answer = jev(keys, timeout=30.0) if getattr(jev, "takes_timeout", False) else jev(keys)
        except Exception:                      # noqa: BLE001 — keep what was judged so far
            break
        for a, b, j in batch:
            p = sa._valid_prob((answer or {}).get((a, b)))
            if p is not None:
                rows.append((a, b, j, p))
    return rows


def best_threshold(rows: list[Row], target: float = LABEL_AT) -> dict:
    """Jaccard threshold in 0.20-0.90 maximising F1 against labels jev_prob >= target."""
    best = {"threshold": sa.KEYWORD_OVERLAP_AT, "precision": 0.0, "recall": 0.0, "f1": 0.0,
            "labels": len(rows)}
    for step in range(20, 91, 5):
        t = step / 100
        tp = sum(1 for r in rows if r[2] >= t and r[3] >= target)
        fp = sum(1 for r in rows if r[2] >= t and r[3] < target)
        fn = sum(1 for r in rows if r[2] < t and r[3] >= target)
        p = tp / (tp + fp) if tp + fp else 0.0
        r_ = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * p * r_ / (p + r_) if p + r_ else 0.0
        if f1 > best["f1"] + 1e-12:
            best.update(threshold=t, precision=round(p, 3), recall=round(r_, 3), f1=round(f1, 3))
    return best


def write_labels(rows: list[Row]) -> Path:
    path = labels_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        for a, b, j, p in rows:
            f.write(json.dumps({"a": a, "b": b, "jaccard": round(j, 4), "jev": p}) + "\n")
    os.chmod(path, 0o600)
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--max-pairs", type=int, default=400)
    args = ap.parse_args(argv)
    if args.max_pairs < 1:
        print("error: --max-pairs must be >= 1", file=sys.stderr)
        return 2
    catalog = load_inputs()[0]
    jev = make_jev(catalog)
    if jev is None:
        print("no TYPESAFE_API_KEY: threshold unchanged "
              f"(keyword overlap >= {sa.KEYWORD_OVERLAP_AT})")
        return 0
    rows = collect(catalog, jev, max_pairs=args.max_pairs)
    write_labels(rows)
    best = best_threshold(rows)
    pos = sum(1 for r in rows if r[3] >= LABEL_AT)
    best["judged"] = len(rows)
    best["attempted"] = getattr(collect, "attempted", len(rows))
    best["positives"] = pos
    best["negatives"] = len(rows) - pos
    print(json.dumps(best))
    if len(rows) < MIN_LABELS or best["f1"] < MIN_F1:
        print("poor evidence: do NOT change KEYWORD_OVERLAP_AT")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
