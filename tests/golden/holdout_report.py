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
CASES_PATH = GOLDEN_DIR / "holdout_cases.json"
EXPECTED_PATH = GOLDEN_DIR / "holdout_expected.json"


def _format(values) -> str:
    return ", ".join(sorted(values)) if values else "(none)"


def _fired_rule_ids(result) -> list[str]:
    fired_ids = result.matched_rule_ids + result.clarification_rule_ids
    fired_ids.extend(
        observation.id
        for observation in result.observations
        if observation.id not in result.matched_rule_ids
    )
    return fired_ids


def main() -> None:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    old_outputs = {
        case["id"]: case["output"]
        for case in json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))["cases"]
    }
    interpreter = RuleInterpreter()
    blueprint = build_postgres_rules_blueprint()
    comparisons = []

    print("Held-out observation parity")
    print("case id | old analyzer ids | rule interpreter ids | missing | extra")
    for case in cases:
        old_ids = set(old_outputs[case["id"]]["observations"])
        result = interpreter.interpret(case["issue"], blueprint)
        new_ids = {observation.id for observation in result.observations}
        missing = old_ids - new_ids
        extra = new_ids - old_ids
        comparisons.append((case["id"], missing, extra))
        print(
            f"{case['id']} | {_format(old_ids)} | {_format(new_ids)} | "
            f"{_format(missing)} | {_format(extra)}"
        )

    print("\nClarification questions")
    print("case id | old analyzer questions | rule interpreter questions")
    for case in cases:
        result = interpreter.interpret(case["issue"], blueprint)
        old_questions = old_outputs[case["id"]]["follow_up_questions"]
        print(
            f"{case['id']} | {_format(old_questions)} | "
            f"{_format(result.clarification_questions)}"
        )

    exact = [entry for entry in comparisons if not entry[1] and not entry[2]]
    with_gaps = [entry for entry in comparisons if entry[1]]
    with_extras = [entry for entry in comparisons if entry[2]]
    print("\nSummary")
    print(f"Exact parity: {len(exact)}/{len(cases)}")
    print(f"Cases with gaps: {len(with_gaps)}")
    print(f"Cases with extras: {len(with_extras)}")

    non_database_case_ids = {
        "holdout_docker_bind_port",
        "holdout_nginx_upstream_refused",
        "holdout_python_traceback",
        "holdout_disk_npm",
    }
    non_database_cases = [
        case for case in cases if case["id"] in non_database_case_ids
    ]
    print("\nPostgreSQL-rule cross-domain check")
    cross_domain_matches = []
    for case in non_database_cases:
        result = interpreter.interpret(case["issue"], blueprint)
        fired_ids = _fired_rule_ids(result)
        if fired_ids:
            cross_domain_matches.append((case["id"], fired_ids))
            print(f"{case['id']}: {_format(fired_ids)}")
    if not cross_domain_matches:
        print(
            f"No PostgreSQL rules fired for all {len(non_database_cases)} "
            "non-database held-out cases."
        )


if __name__ == "__main__":
    main()
