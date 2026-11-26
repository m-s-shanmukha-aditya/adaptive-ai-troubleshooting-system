import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import app.services.troubleshooter as troubleshooter_module
from app.models.experience import CandidateEvaluation, IssueContext
from app.services.blueprint_loader import load_blueprints
from app.services.candidate_selector import CandidateSelector
from app.services.issue_analyzer import IssueAnalyzer
from app.services.solution_evaluator import SolutionEvaluator
from app.services.test_case_generator import TestCaseGenerator
from app.services.troubleshooter import Troubleshooter
from app.services.experience_repository import ExperienceRepository


def structured(diagnosis="database_credentials_invalid", evidence="authentication_failed", action="verify_credentials_securely"):
    return json.dumps({
        "schema_version": "1",
        "diagnosis": {"id": diagnosis, "summary": "Authentication failed", "status": "supported"},
        "evidence": [{"observation_id": evidence, "interpretation": "The log reports authentication failure."}],
        "recommended_actions": [{"action_id": action, "description": "Verify configured credentials without exposing them."}],
        "resolution_claim": "unverified",
        "follow_up_questions": [],
    })


def test_controlled_evaluation_requires_exactly_one_scenario():
    evaluator = SolutionEvaluator()
    cases = TestCaseGenerator().controlled_scenarios()
    for case in cases:
        result = evaluator.evaluate_controlled(structured(case.expected_diagnosis_id), case)
        assert len(result.results) == 1
        assert result.mode == "controlled"
        assert result.diagnosis_correct is True
    with pytest.raises((TypeError, ValueError)):
        evaluator.evaluate_controlled(structured(), cases)


def test_runtime_evaluator_never_uses_fixture_answer_key():
    result = SolutionEvaluator().evaluate_runtime(
        structured(), {"authentication_failed": "password authentication failed"}
    )
    assert result.mode == "runtime"
    assert result.diagnosis_correct is None
    assert result.score == 0
    assert result.repair_reasons == []
    assert result.resolution_verified is False


def test_runtime_invalid_evidence_is_a_repairable_deterministic_failure():
    result = SolutionEvaluator().evaluate_runtime(structured(evidence="made_up_observation"), {"authentication_failed": "failed"})
    assert result.diagnosis_correct is None
    assert "invalid_evidence_references" in result.repair_reasons


def test_runtime_rejects_unknown_diagnosis_and_action_ids():
    evaluator = SolutionEvaluator()
    observations = {"service_state": "stopped"}
    bad_diagnosis = evaluator.evaluate_runtime(
        structured("SpringBootPostgreSQLConnectionFailure", "service_state", "verify_service_status"), observations
    )
    bad_action = evaluator.evaluate_runtime(
        structured("postgres_service_stopped", "service_state", "CheckPostgreSQLServiceStatus"), observations
    )
    assert "unknown_diagnosis_id" in bad_diagnosis.repair_reasons
    assert "unsafe_or_unknown_action" in bad_action.repair_reasons


def test_runtime_rejects_numeric_confidence_and_uses_qualitative_status():
    result = SolutionEvaluator().evaluate_runtime(
        structured().replace("Authentication failed", "Authentication failed with 92% confidence"),
        {"authentication_failed": "failed"},
    )
    assert "quantified_confidence_not_allowed" in result.repair_reasons


def test_runtime_checks_diagnosis_support_and_action_relevance_from_knowledge():
    blueprint = next(item for item in load_blueprints() if item.category == "database")
    evaluator = SolutionEvaluator()
    unsupported = evaluator.evaluate_runtime(
        structured("postgres_service_stopped", "authentication_failed", "verify_credentials_securely"),
        {"authentication_failed": "authentication rejected"}, blueprint,
    )
    assert "diagnosis_not_supported_by_cited_observations" in unsupported.repair_reasons
    assert "actions_not_relevant_to_diagnosis" in unsupported.repair_reasons


def test_runtime_vocabulary_can_come_from_another_domain_knowledge_file():
    blueprint = next(item for item in load_blueprints() if item.category == "database").model_copy(update={
        "diagnoses": {"cache_lock_contention": {"meaning": "reported cache lock contention", "supporting_evidence": ["reported_detail_1"]}},
        "actions": {"inspect_lock_metrics": {"meaning": "inspect lock wait metrics", "diagnosis_ids": ["cache_lock_contention"]}},
    })
    candidate = json.dumps({
        "schema_version": "1",
        "diagnosis": {"id": "cache_lock_contention", "summary": "Cache operations report lock waits", "status": "supported"},
        "evidence": [{"observation_id": "reported_detail_1", "interpretation": "Log reports lock wait"}],
        "recommended_actions": [{"action_id": "inspect_lock_metrics", "description": "Inspect metrics"}],
        "resolution_claim": "unverified", "follow_up_questions": [],
    })
    result = SolutionEvaluator().evaluate_runtime(candidate, {"reported_detail_1": "lock wait timeout"}, blueprint)
    assert result.repair_reasons == []


def test_issue_analyzer_preserves_unrecognized_reported_details():
    context = IssueAnalyzer().analyze("Worker fails with error: lock wait timeout; service v3.2 at localhost")
    assert any("lock wait timeout" in value for key, value in context.observations.items() if key.startswith("reported_detail_"))
    assert "localhost" in context.observations.values()


def test_ambiguous_connection_refused_reaches_generation_and_can_request_evidence(monkeypatch):
    raw = json.dumps({
        "schema_version": "1",
        "diagnosis": {"id": "postgres_connection_issue_uncertain", "summary": "The symptom does not distinguish a cause.", "status": "insufficient_evidence"},
        "evidence": [{"observation_id": "connection_refused", "interpretation": "The reported connection was refused."}],
        "recommended_actions": [{"action_id": "verify_service_status", "description": "Check whether the service is active."}],
        "resolution_claim": "unverified",
        "follow_up_questions": ["Is PostgreSQL running, and which port is it listening on?"],
    })
    troubleshooter, llm, _repository = _stub_runtime(monkeypatch, [raw])
    response = troubleshooter.troubleshoot("Spring Boot cannot connect to PostgreSQL: connection refused")
    assert "postgres_connection_issue_uncertain" in response
    assert llm.generate_response.call_count == 1


def test_authentication_error_is_extracted_without_deterministic_follow_up_gate():
    context = IssueAnalyzer().analyze("password authentication failed")
    assert context.diagnostic_status == "uncertain"
    assert context.follow_up_questions == []


def test_runtime_prompt_has_observations_but_no_controlled_fixture_answers():
    context = IssueAnalyzer().analyze("password authentication failed")
    prompt = Troubleshooter._candidate_prompt("password authentication failed", context, next(item for item in load_blueprints() if item.category == "database"))
    for scenario in TestCaseGenerator().controlled_scenarios():
        assert scenario.oracle not in prompt
        assert scenario.input_evidence not in prompt
    assert "authentication_failed" in prompt
    assert "probabilities" in prompt.lower()
    for canonical_id in SolutionEvaluator.DIAGNOSIS_MEANINGS | SolutionEvaluator.ACTION_MEANINGS:
        assert canonical_id in prompt


def test_runtime_selector_ignores_diagnosis_correctness_and_separates_modes():
    runtime_a = CandidateEvaluation(candidate="a", results=[], score=1, mode="runtime", diagnosis_correct=True, schema_valid=True, action_quality=False)
    runtime_b = CandidateEvaluation(candidate="b", results=[], score=0, mode="runtime", diagnosis_correct=None, schema_valid=True, action_quality=True)
    assert CandidateSelector.select_runtime([runtime_a, runtime_b]) is runtime_b
    controlled = CandidateEvaluation(candidate="c", results=[], score=1, mode="controlled", diagnosis_correct=True)
    with pytest.raises(ValueError):
        CandidateSelector.select([runtime_a, controlled])


def test_runtime_selector_rejects_candidates_with_repair_reasons():
    invalid = CandidateEvaluation(candidate="bad", results=[], score=0, mode="runtime",
                                  schema_valid=True, repair_reasons=["unknown_diagnosis_id"])
    assert CandidateSelector.select_runtime([invalid]) is None


def _stub_runtime(monkeypatch, responses):
    blueprints = load_blueprints()

    class FakeRetriever:
        def __init__(self, _blueprints):
            pass

        def retrieve(self, issue, top_k=1):
                return [(next(item for item in blueprints if item.category == "database"), 1.0)]

    llm = MagicMock()
    llm.generate_response.side_effect = responses
    repository = MagicMock()
    monkeypatch.setattr(troubleshooter_module, "SemanticRetriever", FakeRetriever)
    monkeypatch.setattr(troubleshooter_module, "LocalLLM", lambda: llm)
    troubleshooter = Troubleshooter(repository=repository)
    return troubleshooter, llm, repository


def test_runtime_repairs_malformed_output_but_does_not_repair_unknown_correctness(monkeypatch):
    service_candidate = structured("postgres_service_stopped", "service_state", "verify_service_status")
    troubleshooter, llm, repository = _stub_runtime(monkeypatch, ["bad json", service_candidate])
    result = troubleshooter.troubleshoot("PostgreSQL service is stopped")
    assert llm.generate_response.call_count == 2
    assert "REPAIR DEFECTS" in llm.generate_response.call_args_list[1].args[0]
    assert "Diagnosis correctness: unknown" in result
    # The repository's public persistence method is used by runtime mode.
    assert repository.save_evaluation.call_args.kwargs["mode"] == "runtime"
    assert repository.save_evaluation.call_args.kwargs["diagnosis_correct"] is None
    assert repository.save_evaluation.call_args.kwargs["resolution_verified"] is False


def test_runtime_valid_candidate_does_not_repair_or_escalate_for_unknown_accuracy(monkeypatch):
    service_candidate = structured("postgres_service_stopped", "service_state", "verify_service_status")
    troubleshooter, llm, repository = _stub_runtime(monkeypatch, [service_candidate])
    monkeypatch.setattr(troubleshooter_module, "CloudLLM", lambda: (_ for _ in ()).throw(AssertionError("should not escalate")))
    response = troubleshooter.troubleshoot("PostgreSQL service is stopped")
    assert llm.generate_response.call_count == 1
    assert "[Local candidate selected" in response
    assert "correctness: unknown" in response.lower()
    assert repository.save_evaluation.call_args.kwargs["diagnosis_correct"] is None


def test_runtime_returns_validation_failure_when_local_and_cloud_candidates_are_invalid(monkeypatch):
    invalid = structured("SpringBootPostgreSQLConnectionFailure", "service_state", "CheckPostgreSQLServiceStatus")
    troubleshooter, llm, repository = _stub_runtime(monkeypatch, [invalid, invalid, invalid])
    cloud = MagicMock()
    cloud.generate_response.return_value = invalid
    monkeypatch.setattr(troubleshooter_module, "CloudLLM", lambda: cloud)
    response = troubleshooter.troubleshoot("PostgreSQL service is stopped")
    assert "No candidate passed runtime validation" in response
    assert "unknown_diagnosis_id" in response
    assert "unsafe_or_unknown_action" in response
    assert "no resolution was verified" in response.lower()
    repository.save_evaluation.assert_not_called()


def test_both_modes_persist_separately_and_repository_requires_resolution_evidence():
    path = Path(".mode-experience-test.jsonl")
    repository = ExperienceRepository(path)
    case = TestCaseGenerator().controlled_scenarios()[2]
    controlled = SolutionEvaluator().evaluate_controlled(structured(case.expected_diagnosis_id), case)
    runtime = SolutionEvaluator().evaluate_runtime(structured(), {"authentication_failed": "failed"})
    try:
        repository.save_evaluation(
            mode="controlled", issue=case.input_evidence, blueprint_version="test",
            scenario_id=case.test_id, candidate=controlled.candidate,
            diagnosis_correct=controlled.diagnosis_correct,
            action_quality=controlled.action_quality,
            evidence_consistent=controlled.evidence_consistent,
            resolution_verified=False, evaluator_version=SolutionEvaluator.EVALUATION_VERSION,
        )
        repository.save_evaluation(
            mode="runtime", issue="authentication failed", blueprint_version="test",
            candidate=runtime.candidate, diagnosis_correct=runtime.diagnosis_correct,
            action_quality=runtime.action_quality,
            evidence_consistent=runtime.evidence_consistent,
            resolution_verified=False, evaluator_version=SolutionEvaluator.EVALUATION_VERSION,
        )
        repository.save({"mode": "runtime", "verified": True, "resolution_verified": True})
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert rows[0]["mode"] == "controlled" and rows[0]["scenario_id"] == case.test_id
        assert rows[1]["mode"] == "runtime" and rows[1]["diagnosis_correct"] is None
        assert all(row["verified"] is False for row in rows)
    finally:
        path.unlink(missing_ok=True)
