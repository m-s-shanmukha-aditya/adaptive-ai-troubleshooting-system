import json
from unittest.mock import MagicMock

import app.services.troubleshooter as troubleshooter_module
from app.services.blueprint_loader import load_blueprints
from app.services.issue_analyzer import IssueAnalyzer
from app.services.troubleshooter import Troubleshooter


EXPLICIT_MISMATCH = (
    "My Spring Boot application is configured to connect to PostgreSQL on port 5433, "
    "but PostgreSQL is listening on port 5432. The PostgreSQL service is running."
)


def test_extracts_explicit_datasource_and_listener_ports_from_reported_issue():
    context = IssueAnalyzer().analyze(EXPLICIT_MISMATCH)
    assert context.observations["datasource_port"] == "5433"
    assert context.observations["listener_port"] == "5432"
    assert context.observations["port_mismatch"] == "configured=5433; listener=5432"
    assert context.observations["service_state"] == "running"
    assert context.diagnostic_status == "supported"
    assert context.follow_up_questions == []


def test_equal_role_associated_ports_do_not_create_mismatch_or_gate_generation():
    context = IssueAnalyzer().analyze(
        "Spring Boot is configured to use port 5432; PostgreSQL is listening on port 5432."
    )
    assert context.observations["datasource_port"] == "5432"
    assert context.observations["listener_port"] == "5432"
    assert "port_mismatch" not in context.observations
    assert context.diagnostic_status == "uncertain"
    assert context.follow_up_questions == []


def test_missing_port_role_is_left_for_semantic_candidate_generation():
    context = IssueAnalyzer().analyze(
        "Spring Boot cannot connect to PostgreSQL. The PostgreSQL service is running."
    )
    assert context.observations.get("service_state") == "running"
    assert "datasource_port" not in context.observations
    assert context.follow_up_questions == []


def test_unassociated_port_numbers_are_not_assumed_to_have_roles_or_gate_generation():
    context = IssueAnalyzer().analyze(
        "Spring Boot mentions port 5433 and PostgreSQL mentions port 5432; the service is running."
    )
    assert "datasource_port" not in context.observations
    assert "listener_port" not in context.observations
    assert "port_mismatch" not in context.observations
    assert context.follow_up_questions == []


def test_conflicting_configured_port_mentions_keep_follow_up_gate():
    context = IssueAnalyzer().analyze(
        "The datasource is configured to use port 5433; it is also configured to use port 5434. "
        "PostgreSQL is listening on port 5432."
    )
    assert "port_mismatch" not in context.observations
    assert context.diagnostic_status == "uncertain"
    assert context.follow_up_questions
    assert "conflicting" in context.follow_up_questions[0].lower()


def test_explicit_mismatch_reaches_llm_and_persistence_with_mocked_candidate(monkeypatch):
    candidate = json.dumps({
        "schema_version": "1",
        "diagnosis": {"id": "datasource_port_incorrect", "summary": "Datasource and listener ports differ.", "status": "supported"},
        "evidence": [
            {"observation_id": "datasource_port", "interpretation": "Configured port is 5433."},
            {"observation_id": "listener_port", "interpretation": "Listener port is 5432."},
            {"observation_id": "port_mismatch", "interpretation": "The values differ."},
            {"observation_id": "service_state", "interpretation": "Service is running."},
        ],
        "recommended_actions": [{"action_id": "align_datasource_port", "description": "Align with the confirmed listener port."}],
        "resolution_claim": "unverified",
        "follow_up_questions": [],
    })
    blueprints = load_blueprints()

    class FakeRetriever:
        def __init__(self, _blueprints):
            pass

        def retrieve(self, issue, top_k=1):
                return [(next(item for item in blueprints if item.category == "database"), 1.0)]

    llm = MagicMock()
    llm.generate_response.return_value = candidate
    repository = MagicMock()
    monkeypatch.setattr(troubleshooter_module, "SemanticRetriever", FakeRetriever)
    monkeypatch.setattr(troubleshooter_module, "LocalLLM", lambda: llm)
    monkeypatch.setattr(troubleshooter_module, "CloudLLM", lambda: (_ for _ in ()).throw(AssertionError("valid local candidate should not escalate")))

    result = Troubleshooter(repository=repository).troubleshoot(EXPLICIT_MISMATCH)

    llm.generate_response.assert_called_once()
    assert "datasource_port_incorrect" in result
    assert repository.save_evaluation.call_count == 1
    saved = repository.save_evaluation.call_args.kwargs
    assert saved["mode"] == "runtime"
    assert saved["diagnosis_correct"] is None
    assert saved["resolution_verified"] is False
