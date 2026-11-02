"""Scoring regression: abstention is not positive success; don't hide failures."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from benchmark_jev import metrics, primary
from jev_choose import Choice, Pick


def test_metrics_include_misses_failures_and_false_routes():
    rows = [
        {"expected": ["a"], "prediction": "a", "expected_tier": "heavy", "model": "haiku"},
        {"expected": ["b"], "prediction": None, "expected_tier": "standard", "failure": "timeout"},
        {"expected": [], "prediction": "a", "expected_tier": "light"},
        {"expected": [], "prediction": None, "expected_tier": "light"},
    ]
    m = metrics(rows)
    assert m["correct_including_abstentions"] == 2
    assert m["positive_hits"] == 1 and m["positive_n"] == 2
    assert m["route_precision"] == .5 and m["coverage"] == .5
    assert m["false_routes_on_no_skill"] == 1 and m["failures"] == 1
    assert m["tier_downgrade_on_heavy"] == 1


def test_primary_does_not_peek_at_expected_label():
    c = Choice(Pick("domain", .9, "route"), Pick("process", 1., "route"), "BUILD", 1., 0, 0)
    assert primary(c) == "domain"
    assert primary(None) is None
