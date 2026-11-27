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


def test_current_v2_score_totals_remain_15_0_0():
    results = collect_results()
    assert {
        key: results["totals"]["all"][key]
        for key in ("tp", "fp", "fn")
    } == {"tp": 15, "fp": 0, "fn": 0}


def test_v2_non_database_cases_fire_no_postgres_rules():
    import json

    from app.services.rule_interpreter import RuleInterpreter
    from tests.fixtures.postgres_rules import build_postgres_rules_blueprint

    cases = json.loads(
        (GOLDEN_DIR / "holdout_v2.json").read_text(encoding="utf-8")
    )["cases"]
    non_database_cases = [
        case for case in cases if not case["id"].startswith("v2_pg_")
    ]
    interpreter = RuleInterpreter()
    blueprint = build_postgres_rules_blueprint()

    assert len(non_database_cases) == 5
    for case in non_database_cases:
        result = interpreter.interpret(case["issue"], blueprint)
        assert result.observations == [], case["id"]
        assert result.clarification_questions == [], case["id"]
