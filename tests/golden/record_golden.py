import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from app.services.issue_analyzer import IssueAnalyzer


GOLDEN_DIR = Path(__file__).resolve().parent
CASES_PATH = GOLDEN_DIR / "analyzer_cases.json"
EXPECTED_PATH = GOLDEN_DIR / "analyzer_expected.json"


def main() -> None:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    analyzer = IssueAnalyzer()
    expected = {
        "cases": [
            {
                "id": case["id"],
                "output": analyzer.analyze(case["issue"]).model_dump(mode="json"),
            }
            for case in cases
        ]
    }
    EXPECTED_PATH.write_text(
        json.dumps(expected, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
