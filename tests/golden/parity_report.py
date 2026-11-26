import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = PROJECT_ROOT / "tests"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(TESTS_DIR))

from app.services.rule_interpreter import RuleInterpreter
from fixtures.postgres_rules import build_postgres_rules_blueprint


GOLDEN_DIR = Path(__file__).resolve().parent
CASES_PATH = GOLDEN_DIR / "analyzer_cases.json"
EXPECTED_PATH = GOLDEN_DIR / "analyzer_expected.json"


def _format(values: list[str] | set[str]) -> str:
    return ", ".join(sorted(values)) if values else "(none)"


def main() -> None:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    old_outputs = {
        case["id"]: case["output"]
        for case in json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))["cases"]
    }
    blueprint = build_postgres_rules_blueprint()
    interpreter = RuleInterpreter()
    comparisons = []

    print("Observation parity")
    print("case id | old analyzer ids | rule ids | missing (old only) | extra (new only)")
    for case in cases:
        case_id = case["id"]
        old_ids = set(old_outputs[case_id]["observations"])
        interpretation = interpreter.interpret(case["issue"], blueprint)
        new_ids = {observation.id for observation in interpretation.observations}
        missing = old_ids - new_ids
        extra = new_ids - old_ids
        comparisons.append((case_id, missing, extra))
        print(
            f"{case_id} | {_format(old_ids)} | {_format(new_ids)} | "
            f"{_format(missing)} | {_format(extra)}"
        )

    print("\nClarification questions")
    print("case id | old analyzer questions | rule interpreter questions")
    for case in cases:
        case_id = case["id"]
        old_questions = old_outputs[case_id]["follow_up_questions"]
        new_questions = interpreter.interpret(case["issue"], blueprint).clarification_questions
        print(
            f"{case_id} | {_format(old_questions)} | {_format(new_questions)}"
        )

    exact = [case_id for case_id, missing, extra in comparisons if not missing and not extra]
    gaps = [case_id for case_id, missing, _ in comparisons if missing]
    extras = [case_id for case_id, _, extra in comparisons if extra]
    postgres_comparisons = [
        comparison for comparison in comparisons if comparison[0].startswith("pg_")
    ]
    postgres_exact = [
        case_id for case_id, missing, extra in postgres_comparisons
        if not missing and not extra
    ]
    postgres_gaps = [
        case_id for case_id, missing, _ in postgres_comparisons if missing
    ]
    postgres_extras = [
        case_id for case_id, _, extra in postgres_comparisons if extra
    ]
    print("\nSummary")
    print(f"Exact parity: {len(exact)} ({_format(exact)})")
    print(f"Cases with gaps: {len(gaps)} ({_format(gaps)})")
    print(f"Cases with extras: {len(extras)} ({_format(extras)})")
    print(
        "PostgreSQL-only: "
        f"{len(postgres_exact)} exact, {len(postgres_gaps)} with gaps, "
        f"{len(postgres_extras)} with extras."
    )

    print("\nPostgreSQL-rule cross-domain check")
    non_database_cases = [case for case in cases if not case["id"].startswith("pg_")]
    cross_domain_matches = []
    for case in non_database_cases:
        result = interpreter.interpret(case["issue"], blueprint)
        fired_ids = result.matched_rule_ids + result.clarification_rule_ids
        if fired_ids:
            cross_domain_matches.append((case["id"], fired_ids))
            print(f"{case['id']}: {_format(fired_ids)}")
    if not cross_domain_matches:
        print("No PostgreSQL observation or clarification rules fired for all 12 non-database cases.")

    redis_case = next(
        case for case in non_database_cases if case["id"] == "redis_connection_refused"
    )
    redis_old_ids = old_outputs[redis_case["id"]]["observations"].keys()
    redis_result = interpreter.interpret(redis_case["issue"], blueprint)
    print(
        "redis_connection_refused: old analyzer emitted "
        f"{_format(set(redis_old_ids))}; PostgreSQL rules fired "
        f"{_format(set(redis_result.matched_rule_ids + redis_result.clarification_rule_ids))}."
    )


if __name__ == "__main__":
    main()
