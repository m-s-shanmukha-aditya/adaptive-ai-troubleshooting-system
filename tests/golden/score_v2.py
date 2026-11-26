import json
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = PROJECT_ROOT / "tests"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from app.services.rule_interpreter import InterpretedObservation, RuleInterpreter
from fixtures.postgres_rules import build_postgres_rules_blueprint


GOLDEN_DIR = Path(__file__).resolve().parent
CASES_PATH = GOLDEN_DIR / "holdout_v2.json"
GENERIC_ID = re.compile(r"reported_detail_\d+\Z")


def _domain_ids(
    observations: list[InterpretedObservation],
) -> dict[str, InterpretedObservation]:
    return {
        observation.id: observation
        for observation in observations
        if observation.id != "connection_refused"
        and GENERIC_ID.fullmatch(observation.id) is None
    }


def _score(rows: list[dict]) -> dict[str, int | float]:
    true_positives = false_positives = false_negatives = 0
    for row in rows:
        truth = set(row["truth"])
        emitted = set(row["emitted"])
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
        "case_count": len(rows),
        "tp": true_positives,
        "fp": false_positives,
        "fn": false_negatives,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
    }


def collect_results() -> dict:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))["cases"]
    interpreter = RuleInterpreter()
    blueprint = build_postgres_rules_blueprint()
    rows = []

    for case in cases:
        interpreted = interpreter.interpret(case["issue"], blueprint)
        domain_observations = _domain_ids(interpreted.observations)
        truth = sorted(set(case["truth"]))
        emitted = sorted(domain_observations)
        truth_set = set(truth)
        emitted_set = set(emitted)
        rows.append(
            {
                "id": case["id"],
                "truth": truth,
                "emitted": emitted,
                "missing": sorted(truth_set - emitted_set),
                "extra": sorted(emitted_set - truth_set),
                "matched_text": {
                    observation_id: domain_observations[observation_id].matched_text
                    for observation_id in emitted
                },
            }
        )

    postgres_rows = [
        row for row in rows if row["id"].startswith("v2_pg_")
    ]
    non_database_rows = [
        row for row in rows if not row["id"].startswith("v2_pg_")
    ]
    return {
        "status": "retrained: v2 used for rule fixes; not a held-out score",
        "cases": rows,
        "totals": {
            "all": _score(rows),
            "postgresql": _score(postgres_rows),
            "non_database": _score(non_database_rows),
        },
    }


def _format_ids(values: list[str]) -> str:
    return ", ".join(values) if values else "(none)"


def main() -> None:
    results = collect_results()
    print("id | truth | emitted | missing | extra | matched text")
    for row in results["cases"]:
        matched_text = "; ".join(
            f"{observation_id}={text!r}"
            for observation_id, text in row["matched_text"].items()
        ) or "(none)"
        print(
            f"{row['id']} | {_format_ids(row['truth'])} | "
            f"{_format_ids(row['emitted'])} | "
            f"{_format_ids(row['missing'])} | "
            f"{_format_ids(row['extra'])} | {matched_text}"
        )

    for label, totals in results["totals"].items():
        print(
            f"{label}: TP={totals['tp']}, FP={totals['fp']}, "
            f"FN={totals['fn']}, precision={totals['precision']:.2%}, "
            f"recall={totals['recall']:.2%}"
        )


if __name__ == "__main__":
    main()
