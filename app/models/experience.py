from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class IssueContext(BaseModel):
    issue: str
    conditions: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    environment: dict[str, str] = Field(default_factory=dict)
    likely_causes: list[str] = Field(default_factory=list)
    observations: dict[str, str] = Field(default_factory=dict)
    follow_up_questions: list[str] = Field(default_factory=list)
    diagnostic_status: str = "uncertain"


class ScenarioTest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    test_id: str
    description: str
    input_evidence: str
    oracle: str
    kind: str = "controlled"
    observations: dict[str, str] = Field(default_factory=dict)
    expected_diagnosis_id: str = ""
    expected_evidence_ids: list[str] = Field(default_factory=list)
    allowed_action_ids: list[str] = Field(default_factory=list)


class CandidateEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_id: str
    interpretation: str = ""


class RecommendedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str
    description: str = ""


class CandidateDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    summary: str = ""
    status: Literal["supported", "uncertain", "insufficient_evidence"] = "uncertain"


class StructuredCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1"]
    diagnosis: CandidateDiagnosis
    evidence: list[CandidateEvidence] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    resolution_claim: str = "unverified"
    follow_up_questions: list[str] = Field(default_factory=list)


class TestResult(BaseModel):
    test_id: str
    passed: bool | None
    evidence: str
    evaluation_kind: str
    diagnosis_correct: bool | None = None
    action_quality: bool | None = None
    resolution_verified: bool = False
    evidence_consistent: bool | None = None


class CandidateEvaluation(BaseModel):
    candidate: str
    results: list[TestResult]
    score: float = Field(ge=0, le=1)
    verified: bool = False
    diagnosis_correct: bool | None = None
    action_quality: bool | None = None
    resolution_verified: bool = False
    evidence_consistent: bool | None = None
    schema_valid: bool = True
    mode: str = "runtime"
    repair_reasons: list[str] = Field(default_factory=list)


class FailureFeedback(BaseModel):
    failed_tests: list[TestResult]
    weak_assumptions: list[str]
    repair_instructions: list[str]

