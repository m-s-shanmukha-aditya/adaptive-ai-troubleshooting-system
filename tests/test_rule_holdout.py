import json
import sys
from pathlib import Path

from app.services.issue_analyzer import IssueAnalyzer
from app.services.rule_interpreter import RuleInterpreter

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from fixtures.postgres_rules import build_postgres_rules_blueprint


GOLDEN_DIR = Path(__file__).parent / "golden"
CASES = json.loads(
    (GOLDEN_DIR / "holdout_cases.json").read_text(encoding="utf-8")
)["cases"]
TRUTH = {
    case["id"]: set(case["observations"])
    for case in json.loads(
        (GOLDEN_DIR / "holdout_truth.json").read_text(encoding="utf-8")
    )["cases"]
}
EXPECTED_OUTPUTS = {
    case["id"]: case["output"]
    for case in json.loads(
        (GOLDEN_DIR / "holdout_expected.json").read_text(encoding="utf-8")
    )["cases"]
}

TRUTH_KNOWN_MISSES = {}


def test_recorded_holdout_outputs_match_current_analyzer():
    analyzer = IssueAnalyzer()

    assert set(EXPECTED_OUTPUTS) == {case["id"] for case in CASES}
    assert set(TRUTH) == {case["id"] for case in CASES}
    assert len(CASES) == 14
    for case in CASES:
        output = analyzer.analyze(case["issue"]).model_dump(mode="json")
        assert output == EXPECTED_OUTPUTS[case["id"]], case["id"]


def test_rule_fixture_matches_hand_labeled_truth():
    interpreter = RuleInterpreter()
    blueprint = build_postgres_rules_blueprint()
    seen_known_misses = set()

    for case in CASES:
        result = interpreter.interpret(case["issue"], blueprint)
        new_ids = {observation.id for observation in result.observations}
        missing = TRUTH[case["id"]] - new_ids
        extra = new_ids - TRUTH[case["id"]]

        if case["id"] in TRUTH_KNOWN_MISSES:
            assert missing == TRUTH_KNOWN_MISSES[case["id"]], case["id"]
            assert extra == set(), case["id"]
            seen_known_misses.add(case["id"])
        else:
            assert new_ids == TRUTH[case["id"]], case["id"]

    assert seen_known_misses == set(TRUTH_KNOWN_MISSES)


def test_service_state_ids_distinguish_stopped_and_running():
    interpreter = RuleInterpreter()
    blueprint = build_postgres_rules_blueprint()
    expected_ids = {
        "holdout_pg_stopped_log": "service_stopped",
        "holdout_pg_two_problems": "service_running",
        "holdout_pg_service_negated": "service_running",
    }

    for case_id, expected_id in expected_ids.items():
        case = next(case for case in CASES if case["id"] == case_id)
        observations = {
            observation.id: observation
            for observation in interpreter.interpret(case["issue"], blueprint).observations
        }
        assert expected_id in observations
        assert (
            set(observations) & {"service_stopped", "service_running"}
        ) == {expected_id}


def test_no_postgres_rules_fire_for_heldout_non_database_cases():
    interpreter = RuleInterpreter()
    blueprint = build_postgres_rules_blueprint()
    non_database_cases = [
        case
        for case in CASES
        if not case["id"].startswith("holdout_pg_")
        and case["id"] != "holdout_vague_db"
    ]
    empty_truth_cases = [
        case for case in CASES if not TRUTH[case["id"]]
    ]

    assert len(non_database_cases) == 4
    for case in empty_truth_cases:
        result = interpreter.interpret(case["issue"], blueprint)
        assert result.observations == [], case["id"]
        assert result.matched_rule_ids == [], case["id"]
        assert result.clarification_questions == [], case["id"]
        assert result.clarification_rule_ids == [], case["id"]
