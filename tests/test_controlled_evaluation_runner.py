import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.controlled_evaluation import ControlledEvaluationRunner
from app.services.experience_repository import ExperienceRepository
from app.services.solution_evaluator import SolutionEvaluator
from app.services.test_case_generator import TestCaseGenerator


def candidate(diagnosis, evidence, action):
    return json.dumps({
        "schema_version": "1",
        "diagnosis": {"id": diagnosis, "summary": "Fixture candidate", "status": "supported"},
        "evidence": [{"observation_id": item, "interpretation": "Reported fixture evidence."} for item in evidence],
        "recommended_actions": [{"action_id": action, "description": "Use the safe documented check."}],
        "resolution_claim": "unverified",
        "follow_up_questions": [],
    })


class ScriptedLLM:
    model_name = "mock-controlled-model"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def generate_response(self, prompt):
        self.prompts.append(prompt)
        return self.outputs.pop(0)


def valid_outputs(scenarios):
    return [candidate(
        scenario.expected_diagnosis_id,
        scenario.expected_evidence_ids,
        scenario.allowed_action_ids[0],
    ) for scenario in scenarios]


def test_runner_executes_all_five_scenarios_and_writes_report_json():
    scenarios = TestCaseGenerator().controlled_scenarios()
    llm = ScriptedLLM(valid_outputs(scenarios))
    output = Path(".controlled-results-test.json")
    try:
        report = ControlledEvaluationRunner(llm=llm).run(output)

        assert report["metrics"]["total_scenarios_executed"] == 5
        assert [row["scenario_id"] for row in report["scenarios"]] == [row.test_id for row in scenarios]
        assert all(row["passed"] for row in report["scenarios"])
        assert len(llm.prompts) == 5
        assert report["model_name"] == "mock-controlled-model"
        assert report["evaluation_version"] == SolutionEvaluator.EVALUATION_VERSION
        assert json.loads(output.read_text(encoding="utf-8")) == report

        # The per-case prompt receives provided issue evidence, but never the oracle.
        for scenario, prompt in zip(scenarios, llm.prompts):
            assert scenario.input_evidence in prompt
            assert scenario.oracle not in prompt
            for diagnosis_id in SolutionEvaluator.DIAGNOSIS_IDS:
                assert diagnosis_id in prompt
            for action_id in SolutionEvaluator.ACTION_IDS:
                assert action_id in prompt
    finally:
        output.unlink(missing_ok=True)


def test_aggregates_each_metric_and_reports_per_scenario_scores():
    scenarios = TestCaseGenerator().controlled_scenarios()
    outputs = valid_outputs(scenarios)
    # Wrong but canonical diagnosis, partial evidence, and a safe but disallowed action.
    outputs[1] = candidate(
        "connection_successful",
        [scenarios[1].expected_evidence_ids[0]],
        "verify_service_status",
    )
    outputs[2] = "not JSON"
    report = ControlledEvaluationRunner(llm=ScriptedLLM(outputs), max_repairs=0).run()

    metrics = report["metrics"]
    assert metrics["total_scenarios_executed"] == 5
    assert metrics["diagnosis_accuracy"] == pytest.approx(0.6)
    assert metrics["evidence_id_consistency_rate"] == pytest.approx(0.6)
    assert metrics["action_quality_rate"] == pytest.approx(0.6)
    assert metrics["overall_pass_rate"] == pytest.approx(0.6)
    assert report["scenarios"][1]["evidence_score"] == pytest.approx(0.5)
    assert report["scenarios"][1]["diagnosis_match"] is False
    assert report["scenarios"][1]["action_score"] == 0.0
    assert report["scenarios"][1]["passed"] is False
    assert report["scenarios"][2]["schema_valid"] is False
    assert "malformed_or_invalid_schema" in report["scenarios"][2]["failure_reason"]


def test_runner_repairs_schema_defect_without_oracle_and_records_final_candidate():
    scenario = TestCaseGenerator().controlled_scenarios()[0]
    llm = ScriptedLLM(["bad output", valid_outputs([scenario])[0]])
    report = ControlledEvaluationRunner(llm=llm, max_repairs=1).run()

    row = report["scenarios"][0]
    assert row["passed"] is True
    assert row["repair_attempts"] == 1
    assert len(llm.prompts) == 6  # first scenario used two calls; four other fixtures one each
    assert scenario.oracle not in llm.prompts[1]


def test_runner_does_not_write_to_runtime_experience_repository():
    scenarios = TestCaseGenerator().controlled_scenarios()
    runtime_history = Path(".controlled-runtime-history-test.jsonl")
    repository = ExperienceRepository(runtime_history)
    with patch.object(repository, "save_evaluation", side_effect=AssertionError("must not persist runtime experience")):
        report = ControlledEvaluationRunner(llm=ScriptedLLM(valid_outputs(scenarios))).run()

    assert report["metrics"]["total_scenarios_executed"] == 5
    assert not runtime_history.exists()
    assert all(row["passed"] for row in report["scenarios"])


def test_scenario_order_is_reproducible_between_runs():
    scenarios = TestCaseGenerator().controlled_scenarios()
    outputs = valid_outputs(scenarios) * 2
    llm = ScriptedLLM(outputs)
    runner = ControlledEvaluationRunner(llm=llm)

    first = runner.run()
    second = runner.run()

    first_order = [row["scenario_id"] for row in first["scenarios"]]
    second_order = [row["scenario_id"] for row in second["scenarios"]]
    assert first_order == second_order == [scenario.test_id for scenario in scenarios]
