import json
from pathlib import Path

from app.services.issue_analyzer import IssueAnalyzer


GOLDEN_DIR = Path(__file__).resolve().parent / "golden"


def test_issue_analyzer_matches_recorded_golden_outputs():
    cases = json.loads(
        (GOLDEN_DIR / "analyzer_cases.json").read_text(encoding="utf-8")
    )["cases"]
    expected = json.loads(
        (GOLDEN_DIR / "analyzer_expected.json").read_text(encoding="utf-8")
    )["cases"]
    analyzer = IssueAnalyzer()

    actual = [
        {
            "id": case["id"],
            "output": analyzer.analyze(case["issue"]).model_dump(mode="json"),
        }
        for case in cases
    ]

    assert actual == expected
