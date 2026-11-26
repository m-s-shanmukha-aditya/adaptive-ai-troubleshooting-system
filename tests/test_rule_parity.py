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

KNOWN_GAPS = {
    # Port mismatch requires comparing the configured and listener port values.
    "pg_port_mismatch": {"port_mismatch"},
    # Definite credential status requires respecting the analyzer's nearby-hedge check.
    "pg_bad_credentials": {"credentials_state"},
    # The mixed report also needs comparison of the two extracted port values.
    "pg_mixed_signals": {"port_mismatch"},
    # Redis-only analyzer outputs must not be made PostgreSQL rules that fire cross-domain.
    "redis_connection_refused": {"connection_refused", "service_state"},
    # Generic reported-detail extraction should not be encoded as a PostgreSQL-specific rule.
    "java_out_of_memory": {"reported_detail_1"},
}


def _observation_ids(result) -> set[str]:
    return {observation.id for observation in result.observations}


def test_postgres_rule_observation_parity_and_recorded_gaps():
    blueprint = build_postgres_rules_blueprint()
    interpreter = RuleInterpreter()
    seen_gaps = set()

    for case in CASES:
        case_id = case["id"]
        old_ids = set(OLD_OUTPUTS[case_id]["observations"])
        result = interpreter.interpret(case["issue"], blueprint)
        new_ids = _observation_ids(result)
        missing = old_ids - new_ids
        extra = new_ids - old_ids

        if case_id in KNOWN_GAPS:
            assert missing == KNOWN_GAPS[case_id], case_id
            assert extra == set(), case_id
            seen_gaps.add(case_id)
        else:
            assert new_ids == old_ids, case_id

    assert seen_gaps == set(KNOWN_GAPS)


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
