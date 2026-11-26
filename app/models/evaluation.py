from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ValidationStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class EvaluationDimension(str, Enum):
    SCHEMA_VALIDITY = "schema_validity"
    DIAGNOSIS_CORRECTNESS = "diagnosis_correctness"
    EVIDENCE_ID_VALIDITY = "evidence_id_validity"
    EVIDENCE_COMPLETENESS = "evidence_completeness"
    EVIDENCE_SUPPORT = "evidence_support"
    ACTION_CORRECTNESS = "action_correctness"
    ACTION_SAFETY = "action_safety"
    PREREQUISITE_SATISFACTION = "prerequisite_satisfaction"
    UNCERTAINTY_CALIBRATION = "uncertainty_calibration"
    CLARIFICATION_QUALITY = "clarification_quality"
    RESOLUTION_CLAIM = "resolution_claim"


class DecisionOutcome(str, Enum):
    ACCEPT = "accept"
    REPAIR = "repair"
    CLARIFY = "clarify"
    REJECT = "reject"
    ESCALATE = "escalate"


class ValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: EvaluationDimension
    status: ValidationStatus
    reason_codes: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    action_refs: list[str] = Field(default_factory=list)
    observation_refs: list[str] = Field(default_factory=list)


class RuntimeEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_mode: Literal["runtime"] = "runtime"
    candidate: str
    dimension_results: list[ValidationResult] = Field(default_factory=list)
    diagnosis_correctness: Literal["pass", "fail", "unknown"] = "unknown"
    ground_truth_source: str | None = Field(default=None, min_length=1)
    ground_truth_independently_verified: bool = False

    @model_validator(mode="after")
    def validate_diagnosis_correctness(self) -> "RuntimeEvaluationResult":
        has_verified_ground_truth = (
            self.ground_truth_independently_verified
            and self.ground_truth_source is not None
        )
        if self.ground_truth_independently_verified and self.ground_truth_source is None:
            raise ValueError("Verified ground truth requires a source reference.")
        if self.diagnosis_correctness != "unknown" and not has_verified_ground_truth:
            raise ValueError(
                "Runtime diagnosis correctness requires independently verified ground truth."
            )

        diagnosis_results = [
            result for result in self.dimension_results
            if result.dimension == EvaluationDimension.DIAGNOSIS_CORRECTNESS
        ]
        if diagnosis_results and diagnosis_results[-1].status.value != self.diagnosis_correctness:
            raise ValueError(
                "Diagnosis correctness must agree with its per-dimension result."
            )
        return self


class ControlledEvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_mode: Literal["controlled"] = "controlled"
    scenario_id: str
    candidate: str
    dimension_results: list[ValidationResult] = Field(default_factory=list)
    case_pass: bool | None = None


class CandidateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evaluation_mode: Literal["runtime"] = "runtime"
    outcome: DecisionOutcome
    reasons: list[str] = Field(default_factory=list)
    repairable_defects: list[str] = Field(default_factory=list)
    unresolved_facts: list[str] = Field(default_factory=list)
    dimension_results: list[ValidationResult] = Field(default_factory=list)
