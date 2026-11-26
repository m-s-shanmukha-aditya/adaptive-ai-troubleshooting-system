import json
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from fixtures.postgres_rules import build_postgres_rules_blueprint
from app.services.rule_interpreter import RuleInterpreter


GOLDEN_DIR = TESTS_DIR / "golden"
CASES = json.loads(
    (GOLDEN_DIR / "analyzer_cases.json").read_text(encoding="utf-8")
)["cases"]
OLD_OUTPUTS = {
    case["id"]: case["output"]
    for case in json.loads(
        (GOLDEN_DIR / "analyzer_expected.json").read_text(encoding="utf-8")
    )["cases"]
}

KNOWN_GAPS = {}

INTENTIONAL_DIVERGENCES = {
    "pg_bad_credentials_i_think": {
        "missing": {"credentials_state"},
        "extra": set(),
        "reason": 'The analyzer asserts invalid credentials despite the hedge "I think".',
    },
    "redis_connection_refused": {
        "missing": {"connection_refused", "service_state"},
        "extra": set(),
        "reason": "PostgreSQL rules intentionally omit database-flavoured output for Redis.",
    },
    "java_out_of_memory": {
        "missing": {"reported_detail_1"},
        "extra": set(),
        "reason": "Generic stage-1 error detail is not PostgreSQL domain knowledge.",
    },
}

OLD_SERVICE_STATE_TO_RULE_ID = {
    "stopped": "service_stopped",
    "running": "service_running",
}


def _observation_ids(result) -> set[str]:
    return {observation.id for observation in result.observations}


def _mapped_old_observation_ids(case_id: str) -> set[str]:
    observations = OLD_OUTPUTS[case_id]["observations"]
    old_ids = set(observations)
    # Keep analyzer recordings unchanged; map its legacy state ID by its recorded value.
    # Intentional divergences remain literal old-analyzer IDs so those differences stay visible.
    if "service_state" in old_ids and case_id not in INTENTIONAL_DIVERGENCES:
        old_ids.remove("service_state")
        old_ids.add(OLD_SERVICE_STATE_TO_RULE_ID[observations["service_state"]])
    return old_ids


def test_all_case_observation_parity_and_recorded_differences():
    blueprint = build_postgres_rules_blueprint()
    interpreter = RuleInterpreter()
    expected_differences = {**KNOWN_GAPS, **INTENTIONAL_DIVERGENCES}
    seen_differences = set()

    for case in CASES:
        case_id = case["id"]
        old_ids = _mapped_old_observation_ids(case_id)
        result = interpreter.interpret(case["issue"], blueprint)
        new_ids = _observation_ids(result)
        missing = old_ids - new_ids
        extra = new_ids - old_ids

        if case_id in expected_differences:
            expected = expected_differences[case_id]
            assert missing == expected["missing"], expected["reason"]
            assert extra == expected["extra"], expected["reason"]
            seen_differences.add(case_id)
        else:
            assert new_ids == old_ids, case_id

    assert seen_differences == set(expected_differences)


def test_no_postgres_observation_or_clarification_rule_fires_for_non_database_cases():
    blueprint = build_postgres_rules_blueprint()
    interpreter = RuleInterpreter()
    non_database_cases = [case for case in CASES if not case["id"].startswith("pg_")]

    assert len(non_database_cases) == 12
    for case in non_database_cases:
        result = interpreter.interpret(case["issue"], blueprint)
        assert result.observations == [], case["id"]
        assert result.matched_rule_ids == [], case["id"]
        assert result.clarification_questions == [], case["id"]
        assert result.clarification_rule_ids == [], case["id"]
