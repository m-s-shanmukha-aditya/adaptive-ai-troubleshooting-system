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
TRUTH_PATH = GOLDEN_DIR / "holdout_truth.json"
OLD_EXPECTED_PATH = GOLDEN_DIR / "holdout_expected.json"
BEFORE_PATH = GOLDEN_DIR / "holdout_rules_before.json"

GENERIC_IDS = {"connection_refused"}


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _domain_ids(observations: dict) -> set[str]:
    return {
        observation_id
        for observation_id in observations
        if observation_id not in GENERIC_IDS
        and not observation_id.startswith("reported_detail_")
    }


def _format(values: set[str]) -> str:
    return ", ".join(sorted(values)) if values else "(none)"


def _score_case_ids(
    case_ids: list[str],
    truth: dict[str, set[str]],
    predicted: dict[str, set[str]],
):
    rows = []
    true_positives = false_positives = false_negatives = 0
    for case_id in case_ids:
        expected = truth[case_id]
        actual = predicted[case_id]
        missing = expected - actual
        extra = actual - expected
        true_positives += len(expected & actual)
        false_positives += len(extra)
        false_negatives += len(missing)
        rows.append((case_id, missing, extra))
    return rows, true_positives, false_positives, false_negatives


def _metrics(label: str, true_positives: int, false_positives: int, false_negatives: int) -> None:
    precision_denominator = true_positives + false_positives
    recall_denominator = true_positives + false_negatives
    precision = true_positives / precision_denominator if precision_denominator else 0.0
    recall = true_positives / recall_denominator if recall_denominator else 0.0
    print(
        f"{label}: TP={true_positives}, FP={false_positives}, FN={false_negatives}, "
        f"precision={precision:.1%}, recall={recall:.1%}"
    )


def _print_service_text(
    label: str,
    cases: list[dict],
    observations_by_case: dict[str, dict],
    observation_ids: tuple[str, ...],
) -> None:
    print(label)
    found = False
    for case in cases:
        for observation_id in observation_ids:
            observation = observations_by_case[case["id"]].get(observation_id)
            if observation is None:
                continue
            found = True
            if isinstance(observation, dict):
                matched_text = observation.get("matched_text")
                captures = observation.get("captures", {})
                value = captures.get("state") if captures else None
                text = matched_text or value or "(no matched text recorded)"
            else:
                text = str(observation)
            print(f"  {case['id']} [{observation_id}]: {text}")
    if not found:
        print(f"  (no {', '.join(observation_ids)} observations)")


def main() -> None:
    cases = _read_json(CASES_PATH)["cases"]
    truth_entries = _read_json(TRUTH_PATH)["cases"]
    truth = {
        entry["id"]: set(entry["observations"])
        for entry in truth_entries
    }
    if {case["id"] for case in cases} != set(truth):
        raise ValueError("Held-out cases and truth labels must have identical IDs.")

    old_entries = _read_json(OLD_EXPECTED_PATH)["cases"]
    old_by_case = {entry["id"]: entry["output"] for entry in old_entries}
    case_ids = {case["id"] for case in cases}
    if set(old_by_case) != case_ids:
        raise ValueError("Held-out cases and analyzer recordings must have identical IDs.")
    old_predictions = {
        case["id"]: _domain_ids(old_by_case[case["id"]]["observations"])
        for case in cases
    }
    old_service_text = {
        case["id"]: old_by_case[case["id"]]["observations"]
        for case in cases
    }

    before_entries = _read_json(BEFORE_PATH)["cases"]
    before_by_case = {entry["id"]: entry for entry in before_entries}
    if set(before_by_case) != case_ids:
        raise ValueError("Held-out cases and before-change rule recordings must have identical IDs.")
    before_predictions = {
        case["id"]: _domain_ids(
            {observation["id"]: observation for observation in before_by_case[case["id"]]["observations"]}
        )
        for case in cases
    }
    before_service_text = {
        case["id"]: {
            observation["id"]: observation
            for observation in before_by_case[case["id"]]["observations"]
        }
        for case in cases
    }

    interpreter = RuleInterpreter()
    blueprint = build_postgres_rules_blueprint()
    after_results = {
        case["id"]: interpreter.interpret(case["issue"], blueprint)
        for case in cases
    }
    after_predictions = {
        case["id"]: _domain_ids(
            {observation.id: observation for observation in after_results[case["id"]].observations}
        )
        for case in cases
    }
    after_service_text = {
        case["id"]: {
            observation.id: observation.model_dump(mode="json")
            for observation in after_results[case["id"]].observations
        }
        for case in cases
    }

    systems = [
        ("Old analyzer", old_predictions, old_service_text),
        ("Rules before", before_predictions, before_service_text),
        ("Rules after", after_predictions, after_service_text),
    ]
    all_case_ids = [case["id"] for case in cases]
    print("Held-out domain-observation scoring (generic IDs excluded)")
    print(
        "case | old missing | old extra | before missing | before extra | "
        "after missing | after extra"
    )
    scores = {}
    row_maps = {}
    for label, predictions, _ in systems:
        rows, tp, fp, fn = _score_case_ids(all_case_ids, truth, predictions)
        scores[label] = (tp, fp, fn)
        row_maps[label] = {
            case_id: (missing, extra)
            for case_id, missing, extra in rows
        }
    for case_id in all_case_ids:
        values = []
        for label, _, _ in systems:
            missing, extra = row_maps[label][case_id]
            values.extend((_format(missing), _format(extra)))
        print(f"{case_id} | " + " | ".join(values))

    print("\nTotals")
    for label, _, _ in systems:
        _metrics(label, *scores[label])

    print("\nMatched text for service-state observations")
    for label, _, observations in systems:
        ids = (
            ("service_stopped", "service_running")
            if label == "Rules after"
            else ("service_state",)
        )
        _print_service_text(label, cases, observations, ids)


if __name__ == "__main__":
    main()
