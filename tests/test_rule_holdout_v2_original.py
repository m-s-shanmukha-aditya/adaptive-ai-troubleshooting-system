import json
from pathlib import Path


ORIGINAL_RESULTS_PATH = (
    Path(__file__).resolve().parent
    / "golden"
    / "holdout_v2_results_original.json"
)


def test_original_v2_score_and_misses_remain_unchanged():
    original = json.loads(ORIGINAL_RESULTS_PATH.read_text(encoding="utf-8"))
    totals = original["totals"]["all"]
    assert {
        key: totals[key]
        for key in ("tp", "fp", "fn")
    } == {"tp": 12, "fp": 0, "fn": 3}

    missing_cases = {
        case["id"]: case["missing"]
        for case in original["cases"]
        if case["missing"]
    }
    assert missing_cases == {
        "v2_pg_port_equal": ["datasource_port", "listener_port"],
        "v2_pg_success": ["connection_success"],
    }
