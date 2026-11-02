import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from benchmark_descriptions import paired_counts


def test_paired_counts_keep_regressions_and_abstentions():
    rows = []
    for i, expected, before, after in [(1, ["a"], None, "a"), (2, ["b"], "b", None),
                                        (3, [], None, None), (4, ["c"], None, "wrong")]:
        for arm, prediction in [("before", before), ("after", after)]:
            rows.append({"id": str(i), "expected": expected, "prediction": prediction, "strategy": arm})
    assert paired_counts(rows) == {"after_only_correct": 1, "before_only_correct": 1,
                                   "both_correct": 1, "both_wrong": 1}
    with pytest.raises(ValueError, match="unmatched"):
        paired_counts(rows[:-1])
