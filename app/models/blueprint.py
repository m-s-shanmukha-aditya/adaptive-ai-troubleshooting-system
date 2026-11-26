
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from uuid import uuid4

from pydantic import BaseModel, Field


class BlueprintStatus(str, Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    DEPRECATED = "deprecated"
    ARCHIVED = "archived"
    # Legacy states remain parseable but are not runtime eligible.
    VERIFIED = "verified"
    REJECTED = "rejected"


class TroubleshootingStep(BaseModel):
    step_number: int = Field(ge=1)
    instruction: str
    expected_result: str
    failure_action: str | None = None


class VerificationCheck(BaseModel):
    description: str
    success_condition: str


class ProceduralBlueprint(BaseModel):
    blueprint_id: str = Field(
        default_factory=lambda: str(uuid4())
    )
    version: str = "1.0.0"
    active: bool = False

    title: str
    problem_summary: str
    category: str

    symptoms: list[str]
    environment: dict[str, str] = Field(default_factory=dict)

    preconditions: list[str] = Field(default_factory=list)

    steps: list[TroubleshootingStep]

    verification_checks: list[VerificationCheck]

    tags: list[str] = Field(default_factory=list)

    # Domain vocabulary is data: adding another knowledge file can extend the
    # diagnoses, evidence relationships, and actions without changing engine code.
    diagnoses: dict[str, dict] = Field(default_factory=dict)
    actions: dict[str, dict] = Field(default_factory=dict)

    status: BlueprintStatus = BlueprintStatus.DRAFT

    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def content_hash(self) -> str:
        """Hash only authored troubleshooting content, not lifecycle metadata."""
        content = self.model_dump(
            mode="json",
            exclude={
                "blueprint_id", "version", "active", "status",
                "success_count", "failure_count", "created_at", "updated_at",
            },
        )
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def runtime_eligible(self) -> bool:
        return self.status == BlueprintStatus.APPROVED and self.active
