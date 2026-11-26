"""Run the fixed controlled PostgreSQL fixture set against a local model.

This experiment is isolated from runtime troubleshooting and never writes to the
runtime experience repository. Run with ``python -m app.controlled_evaluation``.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.candidate_selector import CandidateSelector
from app.services.local_llm import LocalLLM
from app.services.solution_evaluator import SolutionEvaluator
from app.services.test_case_generator import TestCaseGenerator


class ControlledEvaluationRunner:
    """Generate, validate, and score one candidate per fixed scenario."""

    def __init__(self, model_name: str = "mistral", max_repairs: int = 2,
                 llm=None, test_generator=None, evaluator=None):
        if max_repairs < 0:
            raise ValueError("max_repairs must be non-negative")
        self.model_name = model_name
        self.max_repairs = max_repairs
        self.llm = llm or LocalLLM(model_name=model_name)
        self.test_generator = test_generator or TestCaseGenerator()
        self.evaluator = evaluator or SolutionEvaluator()

    def run(self, output_path: str | Path | None = None) -> dict[str, Any]:
        scenarios = self.test_generator.controlled_scenarios()
        # This vocabulary is shared across every scenario. No scenario's expected
        # diagnosis/evidence/action selection is included in its generation prompt.
        evidence_vocabulary = sorted({
            evidence_id
            for scenario in scenarios
            for evidence_id in scenario.expected_evidence_ids
        })
        results = [self._run_scenario(scenario, evidence_vocabulary) for scenario in scenarios]
        total = len(results)

        def rate(predicate) -> float:
            return sum(bool(predicate(row)) for row in results) / total if total else 0.0

        report = {
            "evaluation_version": self.evaluator.EVALUATION_VERSION,
            "model_name": getattr(self.llm, "model_name", self.model_name),
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "metrics": {
                "total_scenarios_executed": total,
                # Correct diagnosis IDs / all scenarios; invalid candidates count as incorrect.
                "diagnosis_accuracy": rate(lambda row: row["diagnosis_match"]),
                # Fraction of scenarios citing every expected evidence ID; invalid candidates count as inconsistent.
                "evidence_id_consistency_rate": rate(lambda row: row["evidence_consistent"]),
                # Fraction of scenarios with a non-empty, allowed, safe action set.
                "action_quality_rate": rate(lambda row: row["action_quality"]),
                # Fraction meeting all three controlled criteria; this is not model accuracy.
                "overall_pass_rate": rate(lambda row: row["passed"]),
            },
            "scenarios": results,
        }
        if output_path is not None:
            path = Path(output_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return report

    def _run_scenario(self, scenario, evidence_vocabulary: list[str]) -> dict[str, Any]:
        issue = scenario.input_evidence
        prompt = self._candidate_prompt(issue, scenario.observations, evidence_vocabulary)
        evidence_ids = set(evidence_vocabulary)
        attempts = []
        candidate = ""
        validation_reasons: list[str] = []

        for attempt_number in range(self.max_repairs + 1):
            if attempt_number:
                repair_prompt = (
                    "Repair only the deterministic validation defects below. Do not use an answer key, "
                    "change a diagnosis merely to guess, or claim the issue is resolved. Return the full JSON object.\n"
                    f"ISSUE: {issue}\nOBSERVATIONS: {json.dumps(scenario.observations)}\n"
                    f"{prompt}\nCANDIDATE: {candidate}\n"
                    f"VALIDATION DEFECTS: {json.dumps(validation_reasons)}"
                )
                try:
                    candidate = self.llm.generate_response(repair_prompt)
                except Exception as exc:  # Model failure is recorded as a failed scenario.
                    validation_reasons = [f"generation_error:{type(exc).__name__}"]
                    break
            else:
                try:
                    candidate = self.llm.generate_response(prompt)
                except Exception as exc:
                    validation_reasons = [f"generation_error:{type(exc).__name__}"]
                    break

            checked = self.evaluator.evaluate_runtime(candidate, evidence_ids)
            attempts.append(checked)
            validation_reasons = checked.repair_reasons
            if not validation_reasons:
                break

        # The selector uses oracle-derived scores for controlled candidates. Do not
        # use it to choose among attempts: that would leak the answer into selection.
        selected = attempts[-1] if attempts else self.evaluator.evaluate_controlled("", scenario)
        controlled = self.evaluator.evaluate_controlled(selected.candidate, scenario)
        parsed = self.evaluator.parse(selected.candidate)
        cited_ids = {item.observation_id for item in parsed.evidence} if parsed else set()
        expected_ids = set(scenario.expected_evidence_ids)
        evidence_score = (
            len(expected_ids.intersection(cited_ids)) / len(expected_ids)
            if expected_ids else 0.0
        )
        diagnosis_match = controlled.diagnosis_correct is True
        evidence_consistent = controlled.evidence_consistent is True
        action_quality = controlled.action_quality is True
        failures = []
        if not controlled.schema_valid:
            failures.extend(controlled.repair_reasons or ["malformed_or_invalid_schema"])
        if not diagnosis_match:
            failures.append("diagnosis_mismatch")
        if not evidence_consistent:
            failures.append("expected_evidence_ids_missing")
        if not action_quality:
            failures.append("action_quality_failed")

        return {
            "scenario_id": scenario.test_id,
            "diagnosis_match": diagnosis_match,
            "evidence_score": evidence_score,
            "evidence_consistent": evidence_consistent,
            "action_score": float(action_quality),
            "action_quality": action_quality,
            "passed": diagnosis_match and evidence_consistent and action_quality,
            "failure_reason": ";".join(dict.fromkeys(failures)) or None,
            "schema_valid": controlled.schema_valid,
            "candidate": selected.candidate,
            "repair_attempts": max(0, len(attempts) - 1),
        }

    @staticmethod
    def _candidate_prompt(issue: str, observations: dict[str, str], evidence_vocabulary: list[str]) -> str:
        diagnoses = "\n".join(
            f"- {key}: {meaning}" for key, meaning in SolutionEvaluator.DIAGNOSIS_MEANINGS.items()
        )
        actions = "\n".join(
            f"- {key}: {meaning}" for key, meaning in SolutionEvaluator.ACTION_MEANINGS.items()
        )
        return (
            "You are completing a controlled troubleshooting case. Use only the issue and observations below. "
            "Choose exactly one diagnosis ID from the canonical list and only relevant action IDs from that list. "
            "Cite evidence using IDs from the shared evidence vocabulary only when supported by the issue. "
            "The evidence vocabulary is the same for every case; no case-specific answer is provided. "
            "Do not claim resolution; use resolution_claim unverified. Return one JSON object only, no markdown, "
            "matching this schema: "
            '{"schema_version":"1","diagnosis":{"id":"...","summary":"...","status":"supported|uncertain|insufficient_evidence"},'
            '"evidence":[{"observation_id":"...","interpretation":"..."}],'
            '"recommended_actions":[{"action_id":"...","description":"..."}],'
            '"resolution_claim":"unverified","follow_up_questions":[]}\n'
            f"CANONICAL DIAGNOSES:\n{diagnoses}\nCANONICAL ACTIONS:\n{actions}\n"
            f"SHARED EVIDENCE ID VOCABULARY: {json.dumps(evidence_vocabulary)}\n"
            f"OBSERVATIONS: {json.dumps(observations)}\nISSUE: {issue}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the controlled PostgreSQL troubleshooting experiment.")
    parser.add_argument("--model", default="mistral", help="Local Ollama model name (default: mistral).")
    parser.add_argument("--max-repairs", type=int, default=2, help="Maximum deterministic repair attempts per scenario.")
    parser.add_argument("--output", default="evaluation/controlled_results.json", help="JSON result path.")
    args = parser.parse_args(argv)

    report = ControlledEvaluationRunner(model_name=args.model, max_repairs=args.max_repairs).run(args.output)
    metrics = report["metrics"]
    print(f"Controlled evaluation: {metrics['total_scenarios_executed']} scenarios; model={report['model_name']}")
    print(
        "Diagnosis accuracy={diagnosis_accuracy:.1%}; evidence-ID consistency={evidence_id_consistency_rate:.1%}; "
        "action quality={action_quality_rate:.1%}; overall pass={overall_pass_rate:.1%}".format(**metrics)
    )
    print(f"Results written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
