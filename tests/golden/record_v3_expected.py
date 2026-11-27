import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.issue_analyzer import IssueAnalyzer


GOLDEN_DIR = Path(__file__).resolve().parent
CASES_PATH = GOLDEN_DIR / "holdout_v3.json"
EXPECTED_PATH = GOLDEN_DIR / "holdout_v3_expected.json"
GENERIC_ID = re.compile(r"reported_detail_\d+\Z")
SERVICE_STATE_IDS = {
    "stopped": "service_stopped",
    "running": "service_running",
}
IGNORED_IDS = {"connection_refused"}


def _mapped_observation_ids(output: dict) -> set[str]:
    observations = output["observations"]
    observation_ids = {
        observation_id
        for observation_id in observations
        if GENERIC_ID.fullmatch(observation_id) is None
        and observation_id not in IGNORED_IDS
    }
    if "service_state" in observations:
        observation_ids.discard("service_state")
        state = observations["service_state"]
        observation_ids.add(SERVICE_STATE_IDS.get(state, "service_state"))
    return observation_ids


def _score(cases: list[dict], outputs: dict[str, dict]) -> dict[str, int | float]:
    true_positives = false_positives = false_negatives = 0
    for case in cases:
        truth = set(case["truth"])
        emitted = _mapped_observation_ids(outputs[case["id"]])
        true_positives += len(truth & emitted)
        false_positives += len(emitted - truth)
        false_negatives += len(truth - emitted)

    precision_denominator = true_positives + false_positives
    recall_denominator = true_positives + false_negatives
    precision = (
        true_positives / precision_denominator
        if precision_denominator
        else 0.0
    )
    recall = true_positives / recall_denominator if recall_denominator else 0.0
    return {
        "tp": true_positives,
        "fp": false_positives,
        "fn": false_negatives,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
    }


def main() -> None:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    analyzer = IssueAnalyzer()
    recorded_cases = [
        {
            "id": case["id"],
            "output": analyzer.analyze(case["issue"]).model_dump(mode="json"),
        }
        for case in cases
    ]
    expected = {"cases": recorded_cases}
    EXPECTED_PATH.write_text(
        json.dumps(expected, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    outputs = {case["id"]: case["output"] for case in recorded_cases}
    score = _score(cases, outputs)
    print(
        f"old IssueAnalyzer: TP={score['tp']}, FP={score['fp']}, "
        f"FN={score['fn']}, precision={score['precision']:.2%}, "
        f"recall={score['recall']:.2%} "
        "(rules: TP=13, FP=5, FN=15)"
    )


if __name__ == "__main__":
    main()
