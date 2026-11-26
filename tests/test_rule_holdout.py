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
EXPECTED_OUTPUTS = {
    case["id"]: case["output"]
    for case in json.loads(
        (GOLDEN_DIR / "holdout_expected.json").read_text(encoding="utf-8")
    )["cases"]
}

KNOWN_HOLDOUT_DIFFERENCES = {
    "holdout_pg_stopped_log": {
        "missing": {"reported_detail_1", "reported_detail_2"},
        "extra": set(),
        "reason": "The analyzer extracts generic log details that are not PostgreSQL rules.",
    },
    "holdout_pg_port_url": {
        "missing": {
            "connection_refused",
            "reported_detail_1",
            "reported_detail_2",
        },
        "extra": set(),
        "reason": "The fixture lacks this refusal phrasing and generic log-detail extraction.",
    },
    "holdout_pg_creds_log": {
        "missing": {"reported_detail_1", "reported_detail_2"},
        "extra": set(),
        "reason": "The analyzer additionally extracts generic log details.",
    },
    "holdout_pg_unknown_host_compose": {
        "missing": {"reported_detail_1", "reported_detail_2"},
        "extra": set(),
        "reason": "The analyzer additionally extracts generic log details.",
    },
    "holdout_docker_bind_port": {
        "missing": {"reported_detail_1"},
        "extra": set(),
        "reason": "The analyzer extracts a generic daemon error detail, not PostgreSQL knowledge.",
    },
    "holdout_nginx_upstream_refused": {
        "missing": {
            "connection_refused",
            "reported_detail_1",
            "reported_detail_2",
            "reported_detail_3",
        },
        "extra": set(),
        "reason": "The old analyzer emits generic refusal/detail IDs for this non-database report.",
    },
    "holdout_python_traceback": {
        "missing": {"reported_detail_1"},
        "extra": set(),
        "reason": "The analyzer extracts a generic traceback detail, not PostgreSQL knowledge.",
    },
}


def test_recorded_holdout_outputs_match_current_analyzer():
    analyzer = IssueAnalyzer()

    assert set(EXPECTED_OUTPUTS) == {case["id"] for case in CASES}
    for case in CASES:
        output = analyzer.analyze(case["issue"]).model_dump(mode="json")
        assert output == EXPECTED_OUTPUTS[case["id"]], case["id"]


def test_holdout_differences_match_the_recorded_set():
    interpreter = RuleInterpreter()
    blueprint = build_postgres_rules_blueprint()
    seen_differences = set()

    for case in CASES:
        old_ids = set(EXPECTED_OUTPUTS[case["id"]]["observations"])
        result = interpreter.interpret(case["issue"], blueprint)
        new_ids = {observation.id for observation in result.observations}
        missing = old_ids - new_ids
        extra = new_ids - old_ids

        if case["id"] in KNOWN_HOLDOUT_DIFFERENCES:
            expected = KNOWN_HOLDOUT_DIFFERENCES[case["id"]]
            assert missing == expected["missing"], expected["reason"]
            assert extra == expected["extra"], expected["reason"]
            seen_differences.add(case["id"])
        else:
            assert new_ids == old_ids, case["id"]

    assert seen_differences == set(KNOWN_HOLDOUT_DIFFERENCES)
