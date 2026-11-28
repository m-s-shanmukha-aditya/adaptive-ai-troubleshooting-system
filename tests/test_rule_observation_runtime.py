import json
from unittest.mock import MagicMock

import pytest

import app.services.troubleshooter as troubleshooter_module
from app.models.blueprint import ObservationRule, ProceduralBlueprint
from app.services.blueprint_loader import load_blueprints
from app.services.rule_interpreter import (
    MAX_REPORT_CHARS,
    RuleInterpreter,
    explain_inapplicability,
    interpreter_applicable,
    validate_patterns,
)
from app.services.solution_evaluator import SolutionEvaluator
from app.services.troubleshooter import Troubleshooter


def redis_blueprint() -> ProceduralBlueprint:
    return ProceduralBlueprint(
        blueprint_id="redis_connection_refused",
        title="Redis connection refused",
        problem_summary="A Redis client cannot connect.",
        category="redis",
        symptoms=["Redis connection refused"],
        steps=[
            {
                "step_number": 1,
                "instruction": "Check the Redis endpoint.",
                "expected_result": "The endpoint is confirmed.",
            }
        ],
        verification_checks=[
            {
                "description": "Check connectivity.",
                "success_condition": "The client can connect.",
            }
        ],
        diagnoses={
            "redis_connection_refused": {
                "meaning": "The Redis connection was refused.",
                "supporting_evidence": ["redis_connection_refused"],
            }
        },
        actions={
            "inspect_redis_service": {
                "meaning": "Inspect Redis service availability.",
                "diagnosis_ids": ["redis_connection_refused"],
            }
        },
        observation_rules=[
            ObservationRule(
                id="redis_connection_refused",
                description="The report says Redis refused the connection.",
                patterns=[r"Redis.{0,30}connection refused"],
                supports_diagnoses=["redis_connection_refused"],
            )
        ],
    )


def candidate(evidence_id: str) -> str:
    return json.dumps(
        {
            "schema_version": "1",
            "diagnosis": {
                "id": "redis_connection_refused",
                "summary": "The Redis connection was refused.",
                "status": "supported",
            },
            "evidence": [
                {
                    "observation_id": evidence_id,
                    "interpretation": "The report says Redis refused the connection.",
                }
            ],
            "recommended_actions": [
                {
                    "action_id": "inspect_redis_service",
                    "description": "Inspect Redis service availability.",
                }
            ],
            "resolution_claim": "unverified",
            "follow_up_questions": [],
        }
    )


def test_pattern_validation_rejects_repeated_alternation_and_accepts_benign_regex():
    def blueprint_for(pattern: str) -> ProceduralBlueprint:
        rule = ObservationRule.model_construct(
            id="signal",
            description="A signal.",
            patterns=[pattern],
            polarity="present",
            supports_diagnoses=[],
            suppressed_by=[],
        )
        return ProceduralBlueprint.model_construct(
            observation_rules=[rule],
            clarification_rules=[],
            comparison_rules=[],
            diagnoses={},
        )

    assert validate_patterns(blueprint_for(r"(a|aa)+$")) == ["unsafe_quantifier"]
    assert validate_patterns(blueprint_for(r"redis.{0,30}connection refused")) == []


def test_interpreter_fails_closed_before_using_an_unsafe_pattern():
    rule = ObservationRule.model_construct(
        id="unsafe",
        description="A repeated alternative.",
        patterns=[r"(a|aa)+$"],
        polarity="present",
        supports_diagnoses=[],
        suppressed_by=[],
    )
    blueprint = ProceduralBlueprint.model_construct(
        observation_rules=[rule],
        clarification_rules=[],
        comparison_rules=[],
        diagnoses={},
    )

    with pytest.raises(ValueError, match="unsafe_quantifier"):
        RuleInterpreter().interpret("aaaaaaaa", blueprint)


def test_pattern_validation_reports_invalid_regex_and_oversize_patterns():
    rule = ObservationRule.model_construct(
        id="signal",
        description="A signal.",
        patterns=["(", "x" * 301],
        polarity="present",
        supports_diagnoses=[],
        suppressed_by=[],
    )
    blueprint = ProceduralBlueprint.model_construct(
        observation_rules=[rule],
        clarification_rules=[],
        comparison_rules=[],
        diagnoses={},
    )

    assert validate_patterns(blueprint) == ["invalid_regex", "pattern_too_long"]


def test_pattern_validation_rejects_more_than_fifty_total_rules():
    rules = [
        ObservationRule.model_construct(
            id=f"signal_{index}",
            description="A signal.",
            patterns=["signal"],
            polarity="present",
            supports_diagnoses=[],
            suppressed_by=[],
        )
        for index in range(51)
    ]
    blueprint = ProceduralBlueprint.model_construct(
        observation_rules=rules,
        clarification_rules=[],
        comparison_rules=[],
        diagnoses={},
    )

    assert validate_patterns(blueprint) == ["too_many_rules"]


def test_explain_inapplicability_reports_rule_evidence_and_pattern_reasons():
    no_rules = ProceduralBlueprint.model_construct(
        observation_rules=[],
        clarification_rules=[],
        comparison_rules=[],
        diagnoses={},
    )
    assert explain_inapplicability(no_rules) == ["no_rules"]

    missing_evidence = ProceduralBlueprint.model_construct(
        observation_rules=[],
        clarification_rules=[],
        comparison_rules=[],
        diagnoses={
            "example": {
                "supporting_evidence": [
                    "unemitted_rule",
                    "ghp_abcdefghijklmnopqrstuvwxyz012345",
                ]
            }
        },
    )
    assert explain_inapplicability(missing_evidence) == [
        "no_rules",
        "unemitted_evidence:?",
        "unemitted_evidence:unemitted_rule",
    ]

    unsafe_rule = ObservationRule.model_construct(
        id="unsafe",
        description="A repeated alternative.",
        patterns=[r"(a|aa)+$"],
        polarity="present",
        supports_diagnoses=[],
        suppressed_by=[],
    )
    unsafe = ProceduralBlueprint.model_construct(
        observation_rules=[unsafe_rule],
        clarification_rules=[],
        comparison_rules=[],
        diagnoses={},
    )
    assert explain_inapplicability(unsafe) == ["pattern:unsafe_quantifier"]


def test_approved_blueprints_pass_pattern_validation():
    approved = [
        blueprint for blueprint in load_blueprints()
        if blueprint.status.value == "approved"
    ]

    assert len(approved) == 2
    assert all(validate_patterns(blueprint) == [] for blueprint in approved)
    assert all(not interpreter_applicable(blueprint) for blueprint in approved)


def test_interpreter_caps_oversize_input():
    blueprint = redis_blueprint()
    report = "x" * MAX_REPORT_CHARS + " Redis connection refused"

    result = RuleInterpreter().interpret(report, blueprint)

    assert result.truncated is True
    assert result.observations == []


def test_redis_rules_are_applicable_and_observations_pass_runtime_validation(
    monkeypatch,
):
    blueprint = redis_blueprint()
    issue = "Redis connection refused by the server."
    interpretation = RuleInterpreter().interpret(issue, blueprint)
    observations = {
        observation.id: observation.matched_text
        for observation in interpretation.observations
    }

    assert interpreter_applicable(blueprint) is True
    assert observations == {"redis_connection_refused": "Redis connection refused"}
    assert SolutionEvaluator().evaluate_runtime(
        candidate("redis_connection_refused"), observations, blueprint
    ).repair_reasons == []
    assert "invalid_evidence_references" in SolutionEvaluator().evaluate_runtime(
        candidate("wrong_observation"), observations, blueprint
    ).repair_reasons

    monkeypatch.setenv("ENABLE_RULE_OBSERVATIONS", "true")
    local_llm = MagicMock()
    local_llm.generate_response.return_value = candidate("redis_connection_refused")
    repository = MagicMock()

    class FakeRetriever:
        def __init__(self, _blueprints):
            pass

        def retrieve(self, _issue, top_k=1):
            return [(blueprint, 1.0)]

    monkeypatch.setattr(troubleshooter_module, "SemanticRetriever", FakeRetriever)
    monkeypatch.setattr(troubleshooter_module, "LocalLLM", lambda: local_llm)
    monkeypatch.setattr(
        troubleshooter_module,
        "CloudLLM",
        lambda: (_ for _ in ()).throw(AssertionError("cloud fallback must not run")),
    )

    service = Troubleshooter(repository=repository)
    response = service.troubleshoot(issue)

    assert "[Local candidate selected" in response
    assert '"redis_connection_refused":"Redis connection refused"' in (
        local_llm.generate_response.call_args.args[0]
    )
    assert repository.save_evaluation.call_args.kwargs["evidence_consistent"] is True


def test_postgres_uses_legacy_observations_when_flag_is_enabled(monkeypatch):
    postgres = next(
        blueprint for blueprint in load_blueprints()
        if blueprint.category == "database"
    )
    assert interpreter_applicable(postgres) is False
    monkeypatch.setenv("ENABLE_RULE_OBSERVATIONS", "true")

    postgres_candidate = json.dumps(
        {
            "schema_version": "1",
            "diagnosis": {
                "id": "postgres_service_stopped",
                "summary": "The PostgreSQL service is stopped.",
                "status": "supported",
            },
            "evidence": [
                {
                    "observation_id": "service_state",
                    "interpretation": "The service is reported stopped.",
                }
            ],
            "recommended_actions": [
                {
                    "action_id": "verify_service_status",
                    "description": "Check whether the PostgreSQL service is running.",
                }
            ],
            "resolution_claim": "unverified",
            "follow_up_questions": [],
        }
    )
    local_llm = MagicMock()
    local_llm.generate_response.return_value = postgres_candidate
    repository = MagicMock()

    class FakeRetriever:
        def __init__(self, _blueprints):
            pass

        def retrieve(self, _issue, top_k=1):
            return [(postgres, 1.0)]

    monkeypatch.setattr(troubleshooter_module, "SemanticRetriever", FakeRetriever)
    monkeypatch.setattr(troubleshooter_module, "LocalLLM", lambda: local_llm)
    monkeypatch.setattr(
        troubleshooter_module,
        "CloudLLM",
        lambda: (_ for _ in ()).throw(AssertionError("cloud fallback must not run")),
    )

    response = Troubleshooter(repository=repository).troubleshoot(
        "PostgreSQL service is stopped"
    )

    prompt = local_llm.generate_response.call_args.args[0]
    assert '"service_state":"stopped"' in prompt
    assert '"service_stopped"' not in prompt
    assert "[Local candidate selected" in response
