import json
import sys
from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
if str(GOLDEN_DIR) not in sys.path:
    sys.path.insert(0, str(GOLDEN_DIR))

from score_v3 import collect_results


RESULTS_PATH = GOLDEN_DIR / "holdout_v3_results.json"


def test_frozen_rule_output_matches_v3_results():
    expected = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    assert collect_results() == expected


def test_v3_results_retain_the_original_frozen_totals():
    results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    assert results["status"] == "first score on frozen rules; held-out"
    assert results["totals"] == {
        "all": {
            "case_count": 29,
            "tp": 13,
            "fp": 5,
            "fn": 15,
            "precision": 0.722222,
            "recall": 0.464286,
        },
        "postgresql": {
            "case_count": 22,
            "tp": 13,
            "fp": 2,
            "fn": 15,
            "precision": 0.866667,
            "recall": 0.464286,
        },
        "non_database": {
            "case_count": 7,
            "tp": 0,
            "fp": 3,
            "fn": 0,
            "precision": 0.0,
            "recall": 0.0,
        },
        "all_excluding_known_limitation": {
            "case_count": 28,
            "tp": 13,
            "fp": 4,
            "fn": 14,
            "precision": 0.764706,
            "recall": 0.481481,
        },
    }
