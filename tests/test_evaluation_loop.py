import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.models.experience import CandidateEvaluation, IssueContext
from app.services.candidate_selector import CandidateSelector
from app.services.failure_diagnoser import FailureDiagnoser
from app.services.solution_evaluator import SolutionEvaluator
from app.services.test_case_generator import TestCaseGenerator
from app.services.experience_repository import ExperienceRepository


SCENARIOS = TestCaseGenerator().generate(IssueContext(issue="controlled postgres scenario"))


def candidate_for(test, **overrides):
    row = {
        "schema_version": "1",
        "diagnosis": {"id": test.expected_diagnosis_id, "summary": "Fixture diagnosis"},
        "evidence": [{"observation_id": key} for key in test.expected_evidence_ids],
        "recommended_actions": [{"action_id": key} for key in test.allowed_action_ids[:1]],
        "resolution_claim": "unverified",
    }
    row.update(overrides)
    return json.dumps(row)


@pytest.mark.parametrize("test", SCENARIOS, ids=lambda test: test.test_id)
def test_all_controlled_scenario_oracles(test):
    result = SolutionEvaluator().evaluate_controlled(candidate_for(test), test)
    assert result.diagnosis_correct is True
    assert result.results[0].action_quality is True
    assert result.resolution_verified is False
    assert result.verified is False


def test_keyword_rich_candidate_with_wrong_diagnosis_fails():
    test = SCENARIOS[0]
    raw = candidate_for(test, diagnosis={"id": "database_credentials_invalid"})
    result = SolutionEvaluator().evaluate_controlled(raw, test)
    assert result.diagnosis_correct is False
    assert result.score == 0


@pytest.mark.parametrize("raw", ["not-json", "{}", '{"schema_version":"9","diagnosis":{"id":"bad"}}'])
def test_malformed_or_unsupported_candidate_is_not_passed(raw):
    result = SolutionEvaluator().evaluate_controlled(raw, SCENARIOS[0])
    assert result.schema_valid is False
    assert result.results[0].passed is None


def test_unsafe_action_fails_action_dimension_without_changing_diagnosis():
    test = SCENARIOS[0]
    raw = candidate_for(test, recommended_actions=[{"action_id": "disable authentication", "description": "Disable authentication"}])
    result = SolutionEvaluator().evaluate_controlled(raw, test)
    assert result.diagnosis_correct is True
    assert result.action_quality is False


def test_repair_feedback_is_deterministic_and_selector_prioritizes_diagnosis():
    bad = SolutionEvaluator().evaluate_controlled(candidate_for(SCENARIOS[0], diagnosis={"id": "database_name_incorrect"}), SCENARIOS[0])
    feedback = FailureDiagnoser().diagnose(bad)
    assert feedback.failed_tests[0].diagnosis_correct is False
    wrong = CandidateEvaluation(candidate="wrong", results=[], score=0, mode="controlled", diagnosis_correct=False, action_quality=True)
    correct = CandidateEvaluation(candidate="right", results=[], score=1, mode="controlled", diagnosis_correct=True, action_quality=False)
    assert CandidateSelector.select_controlled([wrong, correct]) is correct


def test_candidate_generation_prompt_does_not_include_expected_answers(monkeypatch):
    from app.services.troubleshooter import Troubleshooter
    context = IssueContext(issue="Spring Boot PostgreSQL issue")
    prompt = Troubleshooter._candidate_prompt("Spring Boot PostgreSQL issue", context, MagicMock(title="Postgres", steps=[]), SCENARIOS)
    assert "expected_diagnosis_id" not in prompt
    assert "Diagnosis must identify" not in prompt
    assert all(test.oracle not in prompt for test in SCENARIOS)


def test_experience_record_preserves_dimensions_without_marking_verified():
    path = Path(".experience-test.jsonl")
    repository = ExperienceRepository(path)
    result = SolutionEvaluator().evaluate_controlled(candidate_for(SCENARIOS[0]), SCENARIOS[0])
    try:
        repository.save({"candidate": result.candidate, "verified": False,
                         "diagnosis_correct": result.diagnosis_correct,
                         "action_quality": result.action_quality,
                         "resolution_verified": result.resolution_verified,
                         "scenario_results": [item.model_dump() for item in result.results]})
        row = json.loads(path.read_text())
        assert row["diagnosis_correct"] is True
        assert row["action_quality"] is True
        assert row["resolution_verified"] is False
        assert row["verified"] is False
    finally:
        path.unlink(missing_ok=True)
