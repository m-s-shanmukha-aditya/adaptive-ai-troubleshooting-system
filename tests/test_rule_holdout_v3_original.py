import json
from pathlib import Path


ORIGINAL_RESULTS_PATH = (
    Path(__file__).resolve().parent
    / "golden"
    / "holdout_v3_results_original.json"
)


def test_original_v3_score_totals_remain_13_5_15():
    original = json.loads(ORIGINAL_RESULTS_PATH.read_text(encoding="utf-8"))
    totals = original["totals"]["all"]
    assert {
        key: totals[key]
        for key in ("tp", "fp", "fn")
    } == {"tp": 13, "fp": 5, "fn": 15}
