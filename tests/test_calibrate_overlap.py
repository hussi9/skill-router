#!/usr/bin/env python3
"""Tests for scripts/calibrate_overlap.py. Synthetic data and temp paths only."""
from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import calibrate_overlap as co  # type: ignore[import-not-found]  # noqa: E402
import skill_audit as sa  # type: ignore[import-not-found]  # noqa: E402


def test_best_threshold_maximises_f1():
    # Jev confirms every pair with jaccard >= 0.55; below that it does not.
    rows = [("a", "b", j / 100, 0.95 if j >= 55 else 0.1) for j in range(25, 95, 3)]
    best = co.best_threshold(rows)
    assert 0.5 <= best["threshold"] <= 0.55
    assert best["f1"] == 1.0 and best["precision"] == 1.0 and best["recall"] == 1.0
    assert best["labels"] == len(rows)


def test_collect_uses_jev_and_caps_pairs():
    catalog = [{"name": f"s{i}", "kind": "skill",
                "description": "alpha beta gamma delta epsilon zeta" + f" w{i}"} for i in range(12)]
    seen = []

    def jev(pairs, timeout=None):
        seen.append(len(pairs))
        return {p: 0.9 for p in pairs}
    jev.takes_timeout = True
    rows = co.collect(catalog, jev, max_pairs=10)
    assert len(rows) == 10 and max(seen) <= sa.JEV_BATCH
    assert all(len(r) == 4 and r[3] == 0.9 for r in rows)


def test_no_key_leaves_threshold_unchanged(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(co, "make_jev", lambda catalog: None)
    monkeypatch.setattr(co, "load_inputs", lambda: ([], {}, 0, None, None))
    before = sa.KEYWORD_OVERLAP_AT
    assert co.main([]) == 0
    assert sa.KEYWORD_OVERLAP_AT == before
    assert "no TYPESAFE_API_KEY" in capsys.readouterr().out


def test_labels_file_is_private(tmp_path, monkeypatch):
    target = tmp_path / "labels.jsonl"
    monkeypatch.setenv("SKILL_ROUTER_OVERLAP_LABELS", str(target))
    co.write_labels([("a", "b", 0.5, 0.9)])
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text().splitlines()[0])["jev"] == 0.9
