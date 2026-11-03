#!/usr/bin/env python3
"""
jev_dataset.py — Jev's routing decisions as training data for a local chooser.

The router (hook mode, Jev answered) appends one features row per prompt to
~/.claude/skill_router_cache/jev-features.jsonl (SKILL_ROUTER_FEATURES_FILE):
the lexical and semantic top-10 skill names, Jev's ranking, and a SHA-256 of
the prompt. export() joins that with the outcomes log (SKILL_ROUTER_OUTCOMES_FILE)
for the skill actually loaded that turn, and writes out/train.jsonl:

  {"prompt_sha256", "features": {"lex_top10", "sem_top10"},
   "label_ranked": [...], "label_loaded": str | None}

Skill names, hashes and numbers only; no prompt text exists to leak. Rows are
exported only to a new or empty directory, mode 600.

  python3 scripts/jev_dataset.py --out DIR [--days 90]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import outcomes  # noqa: E402


def features_path() -> Path:
    override = os.environ.get("SKILL_ROUTER_FEATURES_FILE")
    return Path(override) if override else Path.home() / ".claude" / "skill_router_cache" / "jev-features.jsonl"


def _names(value) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(n, str) for n in value):
        return None
    return value


def _loaded_labels() -> dict[tuple, str]:
    """(session_id, prompt_id) -> first skill loaded that turn."""
    out: dict[tuple, str] = {}
    for e in outcomes._read_events():
        if e.get("type") == "signal" and e.get("kind") == "loaded" and e.get("skill"):
            out.setdefault((e.get("session_id"), e.get("prompt_id")), str(e["skill"]))
    return out


def export(out: Path, since_days: int = 90) -> dict:
    out = Path(out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError(f"{out} exists and is not an empty directory")
    cutoff = time.time() - since_days * 86400
    labels = _loaded_labels()
    latest: dict[tuple, dict] = {}
    skipped = 0
    try:
        lines = features_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            skipped += 1
            continue
        if not isinstance(r, dict):
            skipped += 1
            continue
        try:
            ts = float(r.get("ts") or 0)
        except (TypeError, ValueError):
            skipped += 1
            continue
        if ts < cutoff:
            continue
        if not r.get("session_id") or not r.get("prompt_id"):
            skipped += 1    # an empty id would merge unrelated turns under one label
            continue
        lex, sem, ranked = _names(r.get("lex_top10")), _names(r.get("sem_top10")), _names(r.get("jev_ranked"))
        sha = r.get("prompt_sha256")
        if lex is None or sem is None or ranked is None or not isinstance(sha, str) or not sha:
            skipped += 1
            continue
        latest[(r.get("session_id"), r.get("prompt_id"))] = {
            "prompt_sha256": sha, "features": {"lex_top10": lex, "sem_top10": sem},
            "label_ranked": ranked, "label_loaded": labels.get((r.get("session_id"), r.get("prompt_id")))}
    out.mkdir(parents=True, exist_ok=True)
    fd = os.open(out / "train.jsonl", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for row in latest.values():
            f.write(json.dumps(row) + "\n")
    return {"rows": len(latest), "with_loaded": sum(1 for r in latest.values() if r["label_loaded"]),
            "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args(argv)
    try:
        print(json.dumps(export(args.out, args.days)))
    except ValueError as e:
        print(f"jev_dataset: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
