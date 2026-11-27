import json
import sys
from pathlib import Path

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
if str(GOLDEN_DIR) not in sys.path:
    sys.path.insert(0, str(GOLDEN_DIR))

from score_v3 import collect_results

from app.services.rule_interpreter import RuleInterpreter
from tests.fixtures.postgres_rules import build_postgres_rules_blueprint


RESULTS_PATH = GOLDEN_DIR / "holdout_v3_results.json"
CASES_PATH = GOLDEN_DIR / "holdout_v3.json"


def test_frozen_rule_output_matches_v3_results():
    expected = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    assert collect_results() == expected


def test_v3_results_are_marked_as_retrained():
    results = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    assert results["status"] == (
        "retrained: v3 used for rule fixes; not a held-out score"
    )


def test_all_v3_non_database_traps_fire_no_postgres_rules():
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    non_database_cases = [
        case for case in cases if not case["id"].startswith("v3_pg_")
    ]
    interpreter = RuleInterpreter()
    blueprint = build_postgres_rules_blueprint()

    assert len(non_database_cases) == 7
    for case in non_database_cases:
        result = interpreter.interpret(case["issue"], blueprint)
        assert result.observations == [], case["id"]
        assert result.clarification_questions == [], case["id"]
