
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
import re
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


_RULE_ID_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)*\Z")
_GROUP_NAME_PATTERN = re.compile(r"[a-z_][a-z0-9_]*\Z")
_ANALYSIS_CONTENT_FIELDS = (
    "domain",
    "aliases",
    "observation_rules",
    "clarification_rules",
    "comparison_rules",
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
    PROVISIONAL = "provisional"
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
    """A positive evidence rule with optional whole-report suppression patterns.

    ``suppressed_by`` is intentionally matched against the whole scanned text,
    not just text preceding a claim; this errs toward asserting less.
    """

    id: str
    description: str
    patterns: list[str] = Field(max_length=20)
    polarity: Literal["present", "absent"] = "present"
    supports_diagnoses: list[str] = Field(default_factory=list)
    suppressed_by: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _validate_rule_id(value)

    @field_validator("patterns", "suppressed_by")
    @classmethod
    def validate_regex_patterns(cls, patterns: list[str]) -> list[str]:
        if len(patterns) > 20:
            raise ValueError("an observation rule may have at most 20 regexes per field")
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

    @model_validator(mode="after")
    def validate_named_groups(self) -> "ObservationRule":
        group_count = 0
        for pattern in self.patterns:
            named_groups = re.compile(pattern).groupindex
            group_count += len(named_groups)
            for name in named_groups:
                if not _GROUP_NAME_PATTERN.fullmatch(name):
                    raise ValueError("named capture group names must be lowercase identifiers")
        if group_count > 5:
            raise ValueError("an observation rule may define at most five named capture groups")
        return self


class ClarificationRule(BaseModel):
    id: str
    when_missing: list[str]
    question: str

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _validate_rule_id(value)


class ComparisonRule(BaseModel):
    id: str
    description: str
    left: str
    right: str
    op: Literal["equal", "not_equal"]
    numeric: bool = False
    supports_diagnoses: list[str] = Field(default_factory=list)

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
    comparison_rules: list[ComparisonRule] = Field(default_factory=list, max_length=20)
    extra_unsafe_phrases: list[str] = Field(default_factory=list)
    unverified_notice: str | None = None

    status: BlueprintStatus = BlueprintStatus.DRAFT
    source: str | None = None
    generated_by: str | None = None
    generated_on: str | None = None
    reviewed_by: str | None = None
    reviewed_on: str | None = None

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
        ] + [
            rule.id for rule in self.comparison_rules
        ]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("observation, clarification, and comparison rule ids must be unique")

        observation_ids = {rule.id for rule in self.observation_rules}
        observation_by_id = {rule.id: rule for rule in self.observation_rules}
        for rule in self.clarification_rules:
            unknown_ids = set(rule.when_missing) - observation_ids
            if unknown_ids:
                raise ValueError(
                    "clarification rule references unknown observation rule ids: "
                    + ", ".join(sorted(unknown_ids))
                )

        for comparison in self.comparison_rules:
            for reference in (comparison.left, comparison.right):
                parts = reference.split(".")
                if len(parts) != 2:
                    raise ValueError(
                        "comparison references must use the form observation_id.group"
                    )
                observation_id, group_name = parts
                observation = observation_by_id.get(observation_id)
                if observation is None:
                    raise ValueError(
                        f"comparison references unknown observation rule: {observation_id}"
                    )
                if not _GROUP_NAME_PATTERN.fullmatch(group_name):
                    raise ValueError("comparison references must name a lowercase capture group")
                available_groups = {
                    name
                    for pattern in observation.patterns
                    for name in re.compile(pattern).groupindex
                }
                if group_name not in available_groups:
                    raise ValueError(
                        f"comparison references unknown capture group: {reference}"
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
            for rule in self.comparison_rules:
                unknown_diagnoses = set(rule.supports_diagnoses) - diagnosis_ids
                if unknown_diagnoses:
                    raise ValueError(
                        "comparison rule references unknown diagnosis ids: "
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
                "source", "generated_by", "generated_on", "reviewed_by", "reviewed_on",
            },
        )
        for field in _ANALYSIS_CONTENT_FIELDS:
            if field not in self.model_fields_set:
                content.pop(field)
        if "observation_rules" in self.model_fields_set:
            for rule_content, rule in zip(content["observation_rules"], self.observation_rules):
                if "suppressed_by" not in rule.model_fields_set:
                    rule_content.pop("suppressed_by", None)
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @property
    def runtime_eligible(self) -> bool:
        if self.status == BlueprintStatus.APPROVED and self.active:
            return True
        return (
            self.status == BlueprintStatus.PROVISIONAL
            and self.active
            and os.getenv("ENABLE_PROVISIONAL_BLUEPRINTS", "").strip() == "1"
        )
