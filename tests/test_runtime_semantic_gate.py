import json
from unittest.mock import MagicMock

import pytest

import app.services.troubleshooter as troubleshooter_module
from app.services.blueprint_loader import load_blueprints
from app.services.troubleshooter import Troubleshooter
from app.models.blueprint import BlueprintStatus
from app.models.experience import StructuredCandidate


def candidate(diagnosis, *, status="supported", evidence=(), actions=(), questions=()):
    return json.dumps({
        "schema_version": "1",
        "diagnosis": {"id": diagnosis, "summary": "Based on the reported information.", "status": status},
        "evidence": [{"observation_id": key, "interpretation": "User-reported evidence."} for key in evidence],
        "recommended_actions": [{"action_id": key, "description": "Check using the approved safe process."} for key in actions],
        "resolution_claim": "unverified",
        "follow_up_questions": list(questions),
    })


@pytest.mark.parametrize("issue,raw,diagnosis", [
    # Paraphrase: analyzer does not recognize the stopped-state phrasing.
    ("Our app cannot reach Postgres; the database daemon isn't active.",
     candidate("postgres_connection_issue_uncertain", status="insufficient_evidence", evidence=(), actions=("inspect_database_error",)),
     "postgres_connection_issue_uncertain"),
    # Less explicit, informative symptom: candidate can retain uncertainty and ask.
    ("Spring app starts, then its database handshake immediately fails.",
     candidate("postgres_connection_issue_uncertain", status="insufficient_evidence", actions=("inspect_database_error",), questions=("What exact database error appears in the application log?",)),
     "postgres_connection_issue_uncertain"),
    # Existing explicit service-state diagnosis.
    ("PostgreSQL service is stopped and my application cannot connect.",
     candidate("postgres_service_stopped", evidence=("service_state",), actions=("start_postgres_service",)),
     "postgres_service_stopped"),
    # Existing datasource/listener port mismatch.
    ("Spring datasource is configured to use port 5433, while PostgreSQL is listening on port 5432.",
     candidate("datasource_port_incorrect", evidence=("datasource_port", "listener_port", "port_mismatch"), actions=("align_datasource_port",)),
     "datasource_port_incorrect"),
    # Existing credential report.
    ("The configured database credentials are incorrect.",
     candidate("database_credentials_invalid", evidence=("credentials_state",), actions=("verify_credentials_securely",)),
     "database_credentials_invalid"),
    # Existing incorrect database name.
    ("The requested database does not exist on the server.",
     candidate("database_name_incorrect", evidence=("database_not_found",), actions=("verify_database_exists",)),
     "database_name_incorrect"),
    # Healthy/negative control must not trigger a configuration change.
    ("The Spring Boot application connected successfully to PostgreSQL.",
     candidate("connection_successful", evidence=("connection_success",), actions=("make_no_configuration_change",)),
     "connection_successful"),
])
def test_issue_flows_to_structured_candidate_generation(issue, raw, diagnosis, monkeypatch):
    blueprints = load_blueprints()

    class FakeRetriever:
        def __init__(self, _blueprints):
            pass

        def retrieve(self, _query, top_k=1):
            return [(next(item for item in blueprints if item.category == "database"), 1.0)]

    llm = MagicMock()
    llm.generate_response.return_value = raw
    repository = MagicMock()
    monkeypatch.setattr(troubleshooter_module, "SemanticRetriever", FakeRetriever)
    monkeypatch.setattr(troubleshooter_module, "LocalLLM", lambda: llm)
    monkeypatch.setattr(troubleshooter_module, "CloudLLM", lambda: (_ for _ in ()).throw(AssertionError("valid mocked candidate must not escalate")))

    response = Troubleshooter(repository=repository).troubleshoot(issue)

    llm.generate_response.assert_called_once()
    assert diagnosis in response
    repository.save_evaluation.assert_called_once()
    assert repository.save_evaluation.call_args.kwargs["diagnosis_correct"] is None
    assert repository.save_evaluation.call_args.kwargs["resolution_verified"] is False


def test_conflicting_objective_port_values_still_clarify_before_generation(monkeypatch):
    llm = MagicMock()
    monkeypatch.setattr(troubleshooter_module, "LocalLLM", lambda: llm)
    issue = (
        "The datasource is configured to use port 5433 and is also configured to use port 5434; "
        "PostgreSQL listens on port 5432."
    )

    response = Troubleshooter(repository=MagicMock()).troubleshoot(issue)

    llm.generate_response.assert_not_called()
    assert "conflicting port values" in response
    assert "No diagnosis or resolution has been verified." in response


def test_empty_knowledge_returns_structured_abstention_without_local_or_cloud_inference(monkeypatch):
    llm = MagicMock()

    class EmptyRetriever:
        def __init__(self, _blueprints):
            pass

        def retrieve(self, _issue, top_k=5):
            return []

    monkeypatch.setattr(troubleshooter_module, "load_blueprints", lambda eligible_only=False: [])
    monkeypatch.setattr(troubleshooter_module, "SemanticRetriever", EmptyRetriever)
    monkeypatch.setattr(troubleshooter_module, "LocalLLM", lambda: llm)
    cloud = MagicMock(side_effect=AssertionError("empty knowledge must not trigger cloud inference"))
    monkeypatch.setattr(troubleshooter_module, "CloudLLM", cloud)

    response = Troubleshooter(repository=MagicMock()).troubleshoot(
        "Spring Boot cannot connect to PostgreSQL; startup reports connection refused."
    )
    candidate_result = StructuredCandidate.model_validate_json(response)

    assert candidate_result.diagnosis.id == "undetermined"
    assert candidate_result.diagnosis.status == "insufficient_evidence"
    assert "no relevant approved troubleshooting blueprint" in candidate_result.diagnosis.summary.lower()
    assert candidate_result.evidence == []
    assert candidate_result.recommended_actions == []
    assert candidate_result.resolution_claim == "unverified"
    assert candidate_result.follow_up_questions
    assert any("error message" in question.lower() or "log excerpt" in question.lower()
               for question in candidate_result.follow_up_questions)
    llm.generate_response.assert_not_called()
    cloud.assert_not_called()


def test_low_relevance_knowledge_returns_abstention_without_escalation(monkeypatch):
    llm = MagicMock()

    class LowRelevanceRetriever:
        def __init__(self, _blueprints):
            pass

        def retrieve(self, _issue, top_k=5):
            return [(load_blueprints()[0], 0.1)]

    monkeypatch.setattr(troubleshooter_module, "SemanticRetriever", LowRelevanceRetriever)
    monkeypatch.setattr(troubleshooter_module, "LocalLLM", lambda: llm)
    cloud = MagicMock(side_effect=AssertionError("low retrieval relevance alone must not trigger cloud inference"))
    monkeypatch.setattr(troubleshooter_module, "CloudLLM", cloud)

    result = StructuredCandidate.model_validate_json(
        Troubleshooter(repository=MagicMock()).troubleshoot("A tool has stopped working")
    )
    assert result.diagnosis.id == "undetermined"
    assert result.evidence == []
    assert result.recommended_actions == []
    llm.generate_response.assert_not_called()
    cloud.assert_not_called()


def test_nonempty_approved_knowledge_still_reaches_local_generation(monkeypatch):
    blueprint = next(item for item in load_blueprints() if item.category == "database").model_copy(
        update={"status": BlueprintStatus.APPROVED, "active": True}
    )
    raw = candidate(
        "postgres_service_stopped",
        evidence=("service_state",),
        actions=("verify_service_status",),
    )
    llm = MagicMock()
    llm.generate_response.return_value = raw

    class MatchingRetriever:
        def __init__(self, _blueprints):
            pass

        def retrieve(self, _issue, top_k=5):
            return [(blueprint, 1.0)]

    monkeypatch.setattr(troubleshooter_module, "load_blueprints", lambda eligible_only=False: [blueprint])
    monkeypatch.setattr(troubleshooter_module, "SemanticRetriever", MatchingRetriever)
    monkeypatch.setattr(troubleshooter_module, "LocalLLM", lambda: llm)
    cloud = MagicMock(side_effect=AssertionError("valid local candidate must not escalate"))
    monkeypatch.setattr(troubleshooter_module, "CloudLLM", cloud)

    response = Troubleshooter(repository=MagicMock()).troubleshoot("PostgreSQL service is stopped")

    assert "[Local candidate selected" in response
    assert "postgres_service_stopped" in response
    llm.generate_response.assert_called_once()
    cloud.assert_not_called()
