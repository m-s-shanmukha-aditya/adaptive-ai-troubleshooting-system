
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import re
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


_RULE_ID_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*\Z")
_ANALYSIS_CONTENT_FIELDS = (
    "domain",
    "aliases",
    "observation_rules",
    "clarification_rules",
    "extra_unsafe_phrases",
    "unverified_notice",
)


def _validate_rule_id(value: str) -> str:
    if not _RULE_ID_PATTERN.fullmatch(value):
        raise ValueError("rule id must be lowercase snake_case")
    return value


def _unbounded_quantifier_at(pattern: str, index: int) -> tuple[bool, int]:
    if index >= len(pattern):
        return False, index
    if pattern[index] in "*+":
        return True, index + 1
    if pattern[index] == "?":
        return False, index + 1
    if pattern[index] == "{":
        match = re.match(r"\{(\d+)(?:,(\d*))?\}", pattern[index:])
        if match:
            unbounded = match.group(2) is not None and match.group(2) == ""
            return unbounded, index + len(match.group(0))
    return False, index


def _has_nested_unbounded_quantifier(pattern: str) -> bool:
    """Reject quantified groups containing unbounded quantifiers.

    This lexical heuristic skips escaped characters and character classes and
    catches forms such as ``(a+)+`` and ``(.*)*``. It does not analyze regex
    semantics: ambiguous alternations such as ``(a|aa)+`` and other complex
    backtracking cases may still pass.
    """
    group_has_unbounded_quantifier: list[bool] = []
    in_character_class = False
    index = 0

    while index < len(pattern):
        char = pattern[index]
        if char == "\\":
            index += 2
            continue
        if in_character_class:
            if char == "]":
                in_character_class = False
            index += 1
            continue
        if char == "[":
            in_character_class = True
            index += 1
            continue
        if char == "(":
            group_has_unbounded_quantifier.append(False)
            index += 1
            continue
        if char == ")":
            if group_has_unbounded_quantifier:
                inner_has_unbounded_quantifier = group_has_unbounded_quantifier.pop()
                is_unbounded, quantifier_end = _unbounded_quantifier_at(pattern, index + 1)
                if is_unbounded and inner_has_unbounded_quantifier:
                    return True
                if group_has_unbounded_quantifier and (
                    inner_has_unbounded_quantifier or is_unbounded
                ):
                    group_has_unbounded_quantifier[-1] = True
                if quantifier_end > index + 1:
                    index = quantifier_end
                    if index < len(pattern) and pattern[index] == "?":
                        index += 1
                    continue
            index += 1
            continue

        if char in "*+{":
            is_unbounded, quantifier_end = _unbounded_quantifier_at(pattern, index)
            if is_unbounded and group_has_unbounded_quantifier:
                group_has_unbounded_quantifier[-1] = True
            if quantifier_end > index:
                index = quantifier_end
                if index < len(pattern) and pattern[index] == "?":
                    index += 1
                continue
        index += 1

    return False


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


class ObservationRule(BaseModel):
    id: str
    description: str
    patterns: list[str]
    polarity: Literal["present", "absent"] = "present"
    supports_diagnoses: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _validate_rule_id(value)

    @field_validator("patterns")
    @classmethod
    def validate_patterns(cls, patterns: list[str]) -> list[str]:
        if len(patterns) > 20:
            raise ValueError("an observation rule may have at most 20 patterns")
        for pattern in patterns:
            if len(pattern) > 200:
                raise ValueError("regex patterns may not exceed 200 characters")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"invalid regex pattern: {exc}") from exc
            if _has_nested_unbounded_quantifier(pattern):
                raise ValueError("regex may not contain nested unbounded quantifiers")
        return patterns


class ClarificationRule(BaseModel):
    id: str
    when_missing: list[str]
    question: str

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _validate_rule_id(value)


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

    domain: str | None = None
    aliases: list[str] = Field(default_factory=list)
    observation_rules: list[ObservationRule] = Field(default_factory=list, max_length=50)
    clarification_rules: list[ClarificationRule] = Field(default_factory=list, max_length=20)
    extra_unsafe_phrases: list[str] = Field(default_factory=list)
    unverified_notice: str | None = None

    status: BlueprintStatus = BlueprintStatus.DRAFT

    success_count: int = Field(default=0, ge=0)
    failure_count: int = Field(default=0, ge=0)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @model_validator(mode="after")
    def validate_analysis_rules(self) -> "ProceduralBlueprint":
        rule_ids = [
            rule.id for rule in self.observation_rules
        ] + [
            rule.id for rule in self.clarification_rules
        ]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("analysis rule ids must be unique within a blueprint")

        observation_ids = {rule.id for rule in self.observation_rules}
        for rule in self.clarification_rules:
            unknown_ids = set(rule.when_missing) - observation_ids
            if unknown_ids:
                raise ValueError(
                    "clarification rule references unknown observation rule ids: "
                    + ", ".join(sorted(unknown_ids))
                )

        if self.diagnoses:
            diagnosis_ids = set(self.diagnoses)
            for rule in self.observation_rules:
                unknown_diagnoses = set(rule.supports_diagnoses) - diagnosis_ids
                if unknown_diagnoses:
                    raise ValueError(
                        "observation rule references unknown diagnosis ids: "
                        + ", ".join(sorted(unknown_diagnoses))
                    )
        return self

    def content_hash(self) -> str:
        """Hash only authored troubleshooting content, not lifecycle metadata."""
        content = self.model_dump(
            mode="json",
            exclude={
                "blueprint_id", "version", "active", "status",
                "success_count", "failure_count", "created_at", "updated_at",
            },
        )
        for field in _ANALYSIS_CONTENT_FIELDS:
            if field not in self.model_fields_set:
                content.pop(field)
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def runtime_eligible(self) -> bool:
        return self.status == BlueprintStatus.APPROVED and self.active
