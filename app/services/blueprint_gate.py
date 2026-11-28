from app.models.blueprint import BlueprintStatus, ProceduralBlueprint
from app.models.experience import CandidateDiagnosis, RecommendedAction, StructuredCandidate
from app.services.rule_interpreter import validate_patterns
from app.services.solution_evaluator import SolutionEvaluator


_ALLOWED_ACTION_PREFIXES = (
    "verify_",
    "inspect_",
    "check_",
    "confirm_",
    "collect_",
    "request_",
)


def provisional_gate(blueprint: ProceduralBlueprint) -> list[str]:
    issues = []
    if blueprint.status != BlueprintStatus.PROVISIONAL:
        issues.append("status_not_provisional")

    recommended_actions = []
    for action_id, definition in blueprint.actions.items():
        if action_id != "make_no_configuration_change" and not action_id.startswith(
            _ALLOWED_ACTION_PREFIXES
        ):
            issues.append("action_id_not_prefixed")
        meaning = definition.get("meaning", "") if isinstance(definition, dict) else ""
        recommended_actions.append(
            RecommendedAction(action_id=action_id, description=meaning)
        )

    candidate = StructuredCandidate(
        schema_version="1",
        diagnosis=CandidateDiagnosis(id="provisional_gate"),
        recommended_actions=recommended_actions,
    )
    if not SolutionEvaluator()._actions_safe(candidate):
        issues.append("unsafe_action")

    issues.extend(validate_patterns(blueprint))
    return list(dict.fromkeys(issues))