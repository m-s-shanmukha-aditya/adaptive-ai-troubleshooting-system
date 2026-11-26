import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = PROJECT_ROOT / "tests"
GOLDEN_DIR = TESTS_DIR / "golden"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from app.models.blueprint import ProceduralBlueprint
from app.services.rule_interpreter import RuleInterpreter
from fixtures.postgres_rules import build_postgres_rules_blueprint


RECORDED_INPUTS = (
    ("analyzer_cases.json", 32),
    ("holdout_cases.json", 14),
    ("holdout_v2.json", 18),
)


def _recorded_issues() -> list[tuple[str, str, str]]:
    issues = []
    for filename, expected_count in RECORDED_INPUTS:
        cases = json.loads(
            (GOLDEN_DIR / filename).read_text(encoding="utf-8")
        )["cases"]
        assert len(cases) == expected_count, filename
        issues.extend(
            (filename, case["id"], case["issue"])
            for case in cases
        )
    return issues


def test_json_blueprint_rules_match_fixture_for_every_recorded_input():
    blueprint_path = (
        PROJECT_ROOT / "data" / "blueprints" / "postgres_connection.json"
    )
    blueprint_data = json.loads(blueprint_path.read_text(encoding="utf-8"))
    json_blueprint = ProceduralBlueprint.model_validate(blueprint_data)
    fixture_blueprint = build_postgres_rules_blueprint()
    interpreter = RuleInterpreter()
    issues = _recorded_issues()

    assert len(issues) == 64
    assert json_blueprint.status.value == "draft"
    assert json_blueprint.active is False

    for filename, case_id, report in issues:
        fixture_output = interpreter.interpret(
            report, fixture_blueprint
        ).model_dump(mode="json")
        json_output = interpreter.interpret(
            report, json_blueprint
        ).model_dump(mode="json")
        assert json_output == fixture_output, f"{filename}:{case_id}"
