import pytest
from pydantic import ValidationError

from app.models.evaluation import (
    CandidateDecision,
    ControlledEvaluationResult,
    DecisionOutcome,
    EvaluationDimension,
    RuntimeEvaluationResult,
    ValidationResult,
    ValidationStatus,
)
from app.models.experience import (
    CandidateEvaluation,
    FailureFeedback,
    TestResult as LegacyTestResult,
)
from app.services.candidate_selector import CandidateSelector
from app.services.failure_diagnoser import FailureDiagnoser


@pytest.mark.parametrize("status", list(ValidationStatus))
def test_validation_result_accepts_each_status(status):
    result = ValidationResult(
        dimension=EvaluationDimension.SCHEMA_VALIDITY,
        status=status,
    )

    assert result.status is status


def test_validation_result_rejects_unknown_status_and_extra_fields():
    with pytest.raises(ValidationError):
        ValidationResult(
            dimension=EvaluationDimension.SCHEMA_VALIDITY,
            status="invalid",
        )

    with pytest.raises(ValidationError):
        ValidationResult(
            dimension=EvaluationDimension.SCHEMA_VALIDITY,
            status=ValidationStatus.PASS,
            unexpected=True,
        )


@pytest.mark.parametrize("outcome", list(DecisionOutcome))
def test_candidate_decision_accepts_each_outcome(outcome):
    decision = CandidateDecision(
        outcome=outcome,
        reasons=["example_reason"],
        repairable_defects=["example_defect"],
        unresolved_facts=["example_fact"],
        dimension_results=[
            ValidationResult(
                dimension=EvaluationDimension.SCHEMA_VALIDITY,
                status=ValidationStatus.PASS,
            )
        ],
    )

    assert decision.outcome is outcome
    assert decision.evaluation_mode == "runtime"


def test_runtime_diagnosis_correctness_defaults_to_unknown():
    result = RuntimeEvaluationResult(candidate="{}")

    assert result.diagnosis_correctness == "unknown"
    assert result.evaluation_mode == "runtime"


@pytest.mark.parametrize(
    "values",
    [
        {"diagnosis_correctness": "pass"},
        {"diagnosis_correctness": "fail", "ground_truth_source": "test-key"},
        {
            "diagnosis_correctness": "pass",
            "ground_truth_independently_verified": True,
        },
    ],
)
def test_runtime_rejects_unverified_diagnosis_correctness(values):
    with pytest.raises(ValidationError):
        RuntimeEvaluationResult(candidate="{}", **values)


def test_runtime_accepts_correctness_with_independently_verified_source():
    result = RuntimeEvaluationResult(
        candidate="{}",
        diagnosis_correctness="pass",
        ground_truth_source="independent-test-key-v1",
        ground_truth_independently_verified=True,
        dimension_results=[
            ValidationResult(
                dimension=EvaluationDimension.DIAGNOSIS_CORRECTNESS,
                status=ValidationStatus.PASS,
            )
        ],
    )

    assert result.diagnosis_correctness == "pass"


def test_runtime_rejects_conflicting_diagnosis_dimension_result():
    with pytest.raises(ValidationError):
        RuntimeEvaluationResult(
            candidate="{}",
            dimension_results=[
                ValidationResult(
                    dimension=EvaluationDimension.DIAGNOSIS_CORRECTNESS,
                    status=ValidationStatus.FAIL,
                )
            ],
        )


@pytest.mark.parametrize(
    "model",
    [
        ValidationResult(
            dimension=EvaluationDimension.EVIDENCE_SUPPORT,
            status=ValidationStatus.UNKNOWN,
            reason_codes=["no_authored_rule"],
            evidence_refs=["service_state"],
            action_refs=["verify_service_status"],
            observation_refs=["obs-1"],
        ),
        RuntimeEvaluationResult(candidate="{}"),
        ControlledEvaluationResult(scenario_id="scenario-1", candidate="{}"),
        CandidateDecision(
            outcome=DecisionOutcome.CLARIFY,
            unresolved_facts=["listener port"],
        ),
    ],
)
def test_contracts_round_trip_json(model):
    restored = type(model).model_validate_json(model.model_dump_json())

    assert restored == model


def test_controlled_result_cannot_be_parsed_as_runtime_result():
    controlled = ControlledEvaluationResult(
        scenario_id="scenario-1",
        candidate="{}",
    )

    with pytest.raises(ValidationError):
        RuntimeEvaluationResult.model_validate(controlled.model_dump())


def test_candidate_decision_rejects_controlled_mode():
    with pytest.raises(ValidationError):
        CandidateDecision(
            evaluation_mode="controlled",
            outcome=DecisionOutcome.ACCEPT,
        )


def test_existing_candidate_evaluation_consumers_remain_compatible():
    evaluation = CandidateEvaluation(
        candidate="candidate",
        results=[
            LegacyTestResult(
                test_id="runtime_validation",
                passed=False,
                evidence="example failure",
                evaluation_kind="runtime",
            )
        ],
        score=0,
        mode="runtime",
        schema_valid=True,
        repair_reasons=["example_failure"],
    )

    assert CandidateSelector.select_runtime([evaluation]) is None
    feedback = FailureDiagnoser().diagnose(evaluation)
    assert isinstance(feedback, FailureFeedback)
    assert feedback.failed_tests == evaluation.results
