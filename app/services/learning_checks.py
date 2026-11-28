"""Deterministic, in-memory checks for a proposed learned blueprint."""

from dataclasses import dataclass
from datetime import date
import re
from typing import Any

from pydantic import ValidationError

from app.models.blueprint import BlueprintStatus, ProceduralBlueprint
from app.models.experience import StructuredCandidate
from app.services.blueprint_gate import provisional_gate
from app.services.rule_interpreter import (
    RuleInterpreter,
    interpreter_applicable,
    observations_by_id,
)
from app.services.solution_evaluator import SolutionEvaluator


BLUEPRINT_ID_PATTERN = re.compile(r"^[a-z0-9_]{3,60}$")
MAX_DIAGNOSES = 10
MAX_ACTIONS = 30
MAX_RULES = 50


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    reason_code: str | None
    blueprint: ProceduralBlueprint | None
    candidate_json: str | None
    observations: dict[str, str]


def _failed(
    reason_code: str,
    blueprint: ProceduralBlueprint | None = None,
    candidate_json: str | None = None,
    observations: dict[str, str] | None = None,
) -> CheckResult:
    return CheckResult(False, reason_code, blueprint, candidate_json, observations or {})


def _generated_blueprint_id(title: Any, existing_ids) -> str | None:
    title_text = title if isinstance(title, str) else ""
    base_id = re.sub(r"[^a-z0-9]+", "_", title_text.lower()).strip("_")[:40].strip("_")
    if len(base_id) < 3:
        base_id = "learned_blueprint"

    existing = set(existing_ids)
    if base_id not in existing:
        blueprint_id = base_id
    else:
        blueprint_id = next(
            (
                f"{base_id}_{suffix}"
                for suffix in range(2, 100)
                if f"{base_id}_{suffix}" not in existing
            ),
            None,
        )
    if blueprint_id is None:
        return None
    assert BLUEPRINT_ID_PATTERN.fullmatch(blueprint_id)
    return blueprint_id


def _candidate_document(answer: dict[str, Any], blueprint: ProceduralBlueprint) -> dict:
    diagnosis_id = answer["diagnosis_id"]
    interpretations = answer["interpretations"]
    return {
        "schema_version": "1",
        "diagnosis": {
            "id": diagnosis_id,
            "summary": blueprint.diagnoses.get(diagnosis_id, {}).get("summary", ""),
            "status": answer["status"],
        },
        "evidence": [
            {
                "observation_id": evidence_id,
                "interpretation": interpretations.get(evidence_id, ""),
            }
            for evidence_id in answer["evidence_ids"]
        ],
        "recommended_actions": [
            {
                "action_id": action_id,
                "description": blueprint.actions.get(action_id, {}).get("meaning", ""),
            }
            for action_id in answer["action_ids"]
        ],
        "resolution_claim": "unverified",
        "follow_up_questions": answer["follow_up_questions"],
    }


def _answer_is_well_formed(answer: Any) -> bool:
    if not isinstance(answer, dict):
        return False
    if not isinstance(answer.get("diagnosis_id"), str):
        return False
    status = answer.get("status")
    if not isinstance(status, str) or status not in {
        "supported",
        "uncertain",
        "insufficient_evidence",
    }:
        return False
    for field in ("evidence_ids", "action_ids", "follow_up_questions"):
        value = answer.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            return False
    interpretations = answer.get("interpretations")
    return isinstance(interpretations, dict) and all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in interpretations.items()
    )


def check_learned_blueprint(
    original_report: str,
    learned: Any,
    generated_by_model: str,
    existing_ids,
) -> CheckResult:
    """Check a model proposal without storing or logging the original report."""
    if not isinstance(learned, dict) or not isinstance(learned.get("blueprint"), dict):
        return _failed("missing_blueprint")
    if not _answer_is_well_formed(learned.get("answer")):
        return _failed("invalid_answer")

    raw_blueprint = dict(learned["blueprint"])
    blueprint_id = _generated_blueprint_id(raw_blueprint.get("title"), existing_ids)
    if blueprint_id is None:
        return _failed("id_collision")
    raw_blueprint["blueprint_id"] = blueprint_id

    raw_blueprint.update(
        status=BlueprintStatus.PROVISIONAL.value,
        active=True,
        source="cloud",
        generated_by=generated_by_model,
        generated_on=date.today().isoformat(),
        reviewed_by=None,
        reviewed_on=None,
    )

    diagnoses = raw_blueprint.get("diagnoses")
    actions = raw_blueprint.get("actions")
    rules = raw_blueprint.get("observation_rules", [])
    clarifications = raw_blueprint.get("clarification_rules", [])
    comparisons = raw_blueprint.get("comparison_rules", [])
    if isinstance(diagnoses, dict) and len(diagnoses) > MAX_DIAGNOSES:
        return _failed("too_many_diagnoses")
    if isinstance(actions, dict) and len(actions) > MAX_ACTIONS:
        return _failed("too_many_actions")
    if all(isinstance(collection, list) for collection in (rules, clarifications, comparisons)):
        if len(rules) + len(clarifications) + len(comparisons) > MAX_RULES:
            return _failed("too_many_rules")

    try:
        blueprint = ProceduralBlueprint.model_validate(raw_blueprint)
    except (ValidationError, TypeError, ValueError):
        return _failed("invalid_schema")

    gate_issues = provisional_gate(blueprint)
    if gate_issues:
        return _failed(gate_issues[0], blueprint)
    if not interpreter_applicable(blueprint):
        return _failed("interpreter_not_applicable", blueprint)

    try:
        interpreted = RuleInterpreter().interpret(original_report, blueprint)
    except (TypeError, ValueError):
        return _failed("interpreter_error", blueprint)
    observations = observations_by_id(interpreted)

    answer = learned["answer"]
    try:
        candidate = StructuredCandidate.model_validate(
            _candidate_document(answer, blueprint)
        )
    except (ValidationError, TypeError, ValueError, KeyError):
        return _failed("invalid_candidate", blueprint, observations=observations)

    candidate_json = candidate.model_dump_json()
    evaluation = SolutionEvaluator().evaluate_runtime(
        candidate_json,
        observations,
        blueprint,
    )
    if evaluation.repair_reasons:
        return _failed(
            evaluation.repair_reasons[0],
            blueprint,
            candidate_json,
            observations,
        )
    return CheckResult(True, None, blueprint, candidate_json, observations)
