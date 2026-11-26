"""Interpret a report using analysis rules carried by a troubleshooting blueprint.

The report is bounded before regex matching. Python's ``re`` engine cannot be
interrupted, so a pathological pattern that passes blueprint validation can
still take a long time on the bounded input.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field

from app.models.blueprint import ProceduralBlueprint


MAX_REPORT_CHARS = 4000
MAX_MATCHED_TEXT_CHARS = 120


class InterpretedObservation(BaseModel):
    id: str
    description: str
    polarity: Literal["present", "absent"]
    matched_text: str
    captures: dict[str, str] = Field(default_factory=dict)
    supports_diagnoses: list[str] = Field(default_factory=list)


class SkippedPattern(BaseModel):
    rule_id: str
    reason: str


class SkippedComparison(BaseModel):
    id: str
    reason: str


class RuleInterpretation(BaseModel):
    observations: list[InterpretedObservation] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    matched_rule_ids: list[str] = Field(default_factory=list)
    clarification_rule_ids: list[str] = Field(default_factory=list)
    suppressed_rule_ids: list[str] = Field(default_factory=list)
    truncated: bool = False
    skipped_patterns: list[SkippedPattern] = Field(default_factory=list)
    skipped_comparisons: list[SkippedComparison] = Field(default_factory=list)


class RuleInterpreter:
    """Evaluate blueprint analysis rules without changing the report or blueprint."""

    def interpret(self, report_text: str, blueprint: ProceduralBlueprint) -> RuleInterpretation:
        """Return matched evidence and questions in blueprint order.

        Polarity labels the emitted observation; it does not change when a rule
        fires. ``matched_rule_ids`` contains only fired observation rules;
        fired clarification rules are listed separately.
        """
        truncated = len(report_text) > MAX_REPORT_CHARS
        bounded_text = report_text[:MAX_REPORT_CHARS]
        observations = []
        matched_rule_ids = []
        clarification_rule_ids = []
        suppressed_rule_ids = []
        fired_observation_ids = set()
        fired_observations = {}
        skipped_patterns = []

        for rule in blueprint.observation_rules:
            matched = None
            for pattern in rule.patterns:
                try:
                    match = re.search(pattern, bounded_text, re.IGNORECASE)
                except Exception as exc:
                    skipped_patterns.append(
                        SkippedPattern(
                            rule_id=rule.id,
                            reason=f"{type(exc).__name__}: {exc}",
                        )
                    )
                    continue

                if match is not None:
                    matched = match
                    break

            if matched is None:
                continue

            captures = {
                name: value[:64]
                for name, value in matched.groupdict().items()
                if value is not None
            }
            matched_text = matched.group(0)
            is_suppressed = False
            for pattern in rule.suppressed_by:
                try:
                    if re.search(pattern, bounded_text, re.IGNORECASE) is not None:
                        is_suppressed = True
                        break
                except Exception as exc:
                    skipped_patterns.append(
                        SkippedPattern(
                            rule_id=rule.id,
                            reason=f"{type(exc).__name__}: {exc}",
                        )
                    )

            if is_suppressed:
                suppressed_rule_ids.append(rule.id)
                continue

            observation = InterpretedObservation(
                id=rule.id,
                description=rule.description,
                polarity=rule.polarity,
                matched_text=matched_text[:MAX_MATCHED_TEXT_CHARS],
                captures=captures,
                supports_diagnoses=list(rule.supports_diagnoses),
            )
            observations.append(observation)
            fired_observations[rule.id] = observation
            matched_rule_ids.append(rule.id)
            fired_observation_ids.add(rule.id)

        skipped_comparisons = []
        for rule in blueprint.comparison_rules:
            left_rule_id, left_group = rule.left.split(".")
            right_rule_id, right_group = rule.right.split(".")
            left_observation = fired_observations.get(left_rule_id)
            right_observation = fired_observations.get(right_rule_id)
            if left_observation is None or right_observation is None:
                continue

            left_value = left_observation.captures.get(left_group)
            right_value = right_observation.captures.get(right_group)
            if left_value is None or right_value is None:
                continue

            if rule.numeric:
                try:
                    left_comparable = int(left_value)
                    right_comparable = int(right_value)
                except (TypeError, ValueError):
                    skipped_comparisons.append(
                        SkippedComparison(
                            id=rule.id,
                            reason="numeric comparison requires integer capture values",
                        )
                    )
                    continue
            else:
                left_comparable = left_value
                right_comparable = right_value

            comparison_matches = (
                left_comparable == right_comparable
                if rule.op == "equal"
                else left_comparable != right_comparable
            )
            if comparison_matches:
                observations.append(
                    InterpretedObservation(
                        id=rule.id,
                        description=rule.description,
                        polarity="present",
                        matched_text=(
                            f"{left_value} {rule.op} {right_value}"
                        )[:MAX_MATCHED_TEXT_CHARS],
                        captures={"left": left_value, "right": right_value},
                        supports_diagnoses=list(rule.supports_diagnoses),
                    )
                )

        clarification_questions = []
        for rule in blueprint.clarification_rules:
            if not rule.when_missing or any(
                observation_id in fired_observation_ids
                for observation_id in rule.when_missing
            ):
                continue
            clarification_rule_ids.append(rule.id)
            if rule.question not in clarification_questions:
                clarification_questions.append(rule.question)

        return RuleInterpretation(
            observations=observations,
            clarification_questions=clarification_questions,
            matched_rule_ids=matched_rule_ids,
            clarification_rule_ids=clarification_rule_ids,
            suppressed_rule_ids=suppressed_rule_ids,
            truncated=truncated,
            skipped_patterns=skipped_patterns,
            skipped_comparisons=skipped_comparisons,
        )
