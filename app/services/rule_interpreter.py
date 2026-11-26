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
    supports_diagnoses: list[str] = Field(default_factory=list)


class SkippedPattern(BaseModel):
    rule_id: str
    reason: str


class RuleInterpretation(BaseModel):
    observations: list[InterpretedObservation] = Field(default_factory=list)
    clarification_questions: list[str] = Field(default_factory=list)
    matched_rule_ids: list[str] = Field(default_factory=list)
    truncated: bool = False
    skipped_patterns: list[SkippedPattern] = Field(default_factory=list)


class RuleInterpreter:
    """Evaluate blueprint analysis rules without changing the report or blueprint."""

    def interpret(self, report_text: str, blueprint: ProceduralBlueprint) -> RuleInterpretation:
        """Return matched evidence and questions in blueprint order.

        Polarity labels the emitted observation; it does not change when a rule
        fires.
        """
        truncated = len(report_text) > MAX_REPORT_CHARS
        bounded_text = report_text[:MAX_REPORT_CHARS]
        observations = []
        matched_rule_ids = []
        fired_observation_ids = set()
        skipped_patterns = []

        for rule in blueprint.observation_rules:
            for pattern in rule.patterns:
                try:
                    match = re.search(pattern, bounded_text, re.IGNORECASE)
                    matched_text = match.group(0) if match is not None else None
                except Exception as exc:
                    skipped_patterns.append(
                        SkippedPattern(
                            rule_id=rule.id,
                            reason=f"{type(exc).__name__}: {exc}",
                        )
                    )
                    continue

                if matched_text is not None:
                    observations.append(
                        InterpretedObservation(
                            id=rule.id,
                            description=rule.description,
                            polarity=rule.polarity,
                            matched_text=matched_text[:MAX_MATCHED_TEXT_CHARS],
                            supports_diagnoses=list(rule.supports_diagnoses),
                        )
                    )
                    matched_rule_ids.append(rule.id)
                    fired_observation_ids.add(rule.id)
                    break

        clarification_questions = []
        for rule in blueprint.clarification_rules:
            if not rule.when_missing or any(
                observation_id in fired_observation_ids
                for observation_id in rule.when_missing
            ):
                continue
            matched_rule_ids.append(rule.id)
            if rule.question not in clarification_questions:
                clarification_questions.append(rule.question)

        return RuleInterpretation(
            observations=observations,
            clarification_questions=clarification_questions,
            matched_rule_ids=matched_rule_ids,
            truncated=truncated,
            skipped_patterns=skipped_patterns,
        )
