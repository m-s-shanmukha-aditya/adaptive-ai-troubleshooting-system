"""Interpret a report using analysis rules carried by a troubleshooting blueprint.

The report is bounded before regex matching. Python's ``re`` engine cannot be
interrupted, so a pathological pattern that passes blueprint validation can
still take a long time on the bounded input.
"""

import re
from typing import Literal

from pydantic import BaseModel, Field

from app.models.blueprint import (
    ProceduralBlueprint,
    _has_nested_unbounded_quantifier,
)
from app.services.redaction import redact


MAX_REPORT_CHARS = 4000
MAX_MATCHED_TEXT_CHARS = 120


def _has_repeated_alternation_group(pattern: str) -> bool:
    """Conservatively reject quantified groups containing alternatives."""
    group_has_alternation: list[bool] = []
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
            group_has_alternation.append(False)
            index += 1
            continue
        if char == "|" and group_has_alternation:
            group_has_alternation[-1] = True
            index += 1
            continue
        if char == ")":
            if not group_has_alternation:
                index += 1
                continue
            has_alternation = group_has_alternation.pop()
            if group_has_alternation and has_alternation:
                group_has_alternation[-1] = True

            quantifier_index = index + 1
            if quantifier_index < len(pattern) and pattern[quantifier_index] in "*+":
                if has_alternation:
                    return True
            elif quantifier_index < len(pattern) and pattern[quantifier_index] == "{":
                quantifier = re.match(r"\{(\d+)(?:,(\d*)?)?\}", pattern[quantifier_index:])
                if quantifier and has_alternation:
                    lower = int(quantifier.group(1))
                    upper_text = quantifier.group(2)
                    is_repeated = (
                        lower > 1
                        or ("," in quantifier.group(0) and (upper_text == "" or upper_text is None or int(upper_text) > 1))
                    )
                    if is_repeated:
                        return True
            index += 1
            continue
        index += 1

    return False


def validate_patterns(blueprint: ProceduralBlueprint) -> list[str]:
    """Return safety issue codes for regexes and rule counts on a blueprint."""
    issues = []
    rules = (
        list(blueprint.observation_rules)
        + list(blueprint.clarification_rules)
        + list(blueprint.comparison_rules)
    )
    if len(rules) > 50:
        issues.append("too_many_rules")

    for rule in blueprint.observation_rules:
        for pattern in list(rule.patterns) + list(rule.suppressed_by):
            if not isinstance(pattern, str):
                issues.append("invalid_regex")
                continue
            if len(pattern) > 300:
                issues.append("pattern_too_long")
                continue
            try:
                re.compile(pattern)
            except re.error:
                issues.append("invalid_regex")
                continue
            if (
                _has_nested_unbounded_quantifier(pattern)
                or _has_repeated_alternation_group(pattern)
            ):
                issues.append("unsafe_quantifier")

    return list(dict.fromkeys(issues))


def interpreter_applicable(blueprint: ProceduralBlueprint) -> bool:
    """Whether rule observations can satisfy every diagnosis evidence ID."""
    has_analysis_rules = bool(
        blueprint.observation_rules
        or blueprint.clarification_rules
        or blueprint.comparison_rules
    )
    if not has_analysis_rules or validate_patterns(blueprint):
        return False

    emittable_ids = {rule.id for rule in blueprint.observation_rules}
    emittable_ids.update(rule.id for rule in blueprint.comparison_rules)
    required_ids = {
        evidence_id
        for definition in blueprint.diagnoses.values()
        for evidence_id in definition.get("supporting_evidence", [])
    }
    return required_ids.issubset(emittable_ids)


def explain_inapplicability(blueprint: ProceduralBlueprint) -> list[str]:
    """Return bounded, value-free reasons rule observations cannot apply."""
    issues = []
    has_analysis_rules = bool(
        blueprint.observation_rules
        or blueprint.clarification_rules
        or blueprint.comparison_rules
    )
    if not has_analysis_rules:
        issues.append("no_rules")

    emittable_ids = {rule.id for rule in blueprint.observation_rules}
    emittable_ids.update(rule.id for rule in blueprint.comparison_rules)
    required_ids = {
        evidence_id
        for definition in blueprint.diagnoses.values()
        for evidence_id in definition.get("supporting_evidence", [])
    }
    for evidence_id in sorted(required_ids, key=lambda value: str(value)):
        if evidence_id in emittable_ids:
            continue
        safe_id = (
            evidence_id
            if isinstance(evidence_id, str)
            and re.fullmatch(r"[a-z0-9_]{1,60}", evidence_id)
            and redact(evidence_id)[0] == evidence_id
            else "?"
        )
        issues.append(f"unemitted_evidence:{safe_id}")

    issues.extend(
        f"pattern:{issue_code}" for issue_code in validate_patterns(blueprint)
    )
    return issues[:10]


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


def observations_by_id(interpretation: RuleInterpretation) -> dict[str, str]:
    """Map interpreted evidence IDs to matched text, matching runtime adapter behavior."""
    return {
        observation.id: observation.matched_text
        for observation in interpretation.observations
    }


class RuleInterpreter:
    """Evaluate blueprint analysis rules without changing the report or blueprint."""

    def interpret(self, report_text: str, blueprint: ProceduralBlueprint) -> RuleInterpretation:
        """Return matched evidence and questions in blueprint order.

        Polarity labels the emitted observation; it does not change when a rule
        fires. ``matched_rule_ids`` contains only fired observation rules;
        fired clarification rules are listed separately.
        """
        pattern_issues = validate_patterns(blueprint)
        if pattern_issues:
            raise ValueError(
                "blueprint patterns failed safety validation: "
                + ", ".join(pattern_issues)
            )

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
