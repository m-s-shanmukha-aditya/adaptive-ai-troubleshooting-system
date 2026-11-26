import json
import sys
from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
if str(GOLDEN_DIR) not in sys.path:
    sys.path.insert(0, str(GOLDEN_DIR))

from score_v2 import collect_results


def test_frozen_rule_output_matches_v2_results():
    expected = json.loads(
        (GOLDEN_DIR / "holdout_v2_results.json").read_text(encoding="utf-8")
    )
    assert collect_results() == expected
