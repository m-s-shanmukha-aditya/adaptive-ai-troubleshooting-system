import json
from unittest.mock import MagicMock

import app.services.troubleshooter as troubleshooter_module
from app.services.blueprint_loader import load_blueprints
from app.services.issue_analyzer import IssueAnalyzer
from app.services.troubleshooter import Troubleshooter


def test_explicit_invalid_username_or_password_is_structured_evidence():
    context = IssueAnalyzer().analyze(
        "My Spring Boot application is configured to connect to PostgreSQL, but the "
        "configured database username or password is incorrect. PostgreSQL is running "
        "and the datasource host and port are correct."
    )
    assert context.observations["credentials_state"] == "reported_invalid"
    assert context.observations["service_state"] == "running"
    assert context.observations["host_port_confirmed"] == "reported_correct"
    assert context.diagnostic_status == "supported"
    assert context.follow_up_questions == []


def test_authentication_failure_with_service_and_host_port_confirmed_proceeds_uncertainly():
    context = IssueAnalyzer().analyze(
        "My Spring Boot application reports password authentication failed. PostgreSQL is "
        "running and the datasource host and port are correct."
    )
    assert context.observations["authentication_failure"] == "password_authentication_failed"
    assert context.observations["service_state"] == "running"
    assert context.observations["host_port_confirmed"] == "reported_correct"
    assert context.diagnostic_status == "uncertain"
    assert context.follow_up_questions == []


def test_hedged_credentials_is_not_promoted_to_fact_and_reaches_semantic_interpretation():
    context = IssueAnalyzer().analyze(
        "My Spring Boot application cannot connect. Maybe the username or password is "
        "incorrect. PostgreSQL is running and the datasource host and port are correct."
    )
    assert "credentials_state" not in context.observations
    assert context.diagnostic_status == "uncertain"
    assert context.follow_up_questions == []


def test_confirmed_authentication_issue_reaches_candidate_generation_and_persistence(monkeypatch):
    issue = (
        "My Spring Boot application reports password authentication failed. PostgreSQL is "
        "running and the datasource host and port are correct."
    )
    candidate = json.dumps({
        "schema_version": "1",
        "diagnosis": {
            "id": "database_credentials_invalid",
            "summary": "Authentication is failing; verify configured credentials securely.",
            "status": "uncertain",
        },
        "evidence": [
            {"observation_id": "authentication_failure", "interpretation": "The reported database error is password authentication failure."},
            {"observation_id": "service_state", "interpretation": "The report says PostgreSQL is running."},
            {"observation_id": "host_port_confirmed", "interpretation": "The report says datasource host and port are correct."},
        ],
        "recommended_actions": [{
            "action_id": "verify_credentials_securely",
            "description": "Verify the configured username without sharing the password.",
        }],
        "resolution_claim": "unverified",
        "follow_up_questions": ["Check PostgreSQL authentication rules if the configured credentials are correct."],
    })
    blueprints = load_blueprints()

    class FakeRetriever:
        def __init__(self, _blueprints):
            pass

        def retrieve(self, query, top_k=1):
            return [(next(item for item in blueprints if item.category == "database"), 1.0)]

    llm = MagicMock()
    llm.generate_response.return_value = candidate
    repository = MagicMock()
    monkeypatch.setattr(troubleshooter_module, "SemanticRetriever", FakeRetriever)
    monkeypatch.setattr(troubleshooter_module, "LocalLLM", lambda: llm)

    response = Troubleshooter(repository=repository).troubleshoot(issue)

    llm.generate_response.assert_called_once()
    assert "database_credentials_invalid" in response
    repository.save_evaluation.assert_called_once()
    assert repository.save_evaluation.call_args.kwargs["mode"] == "runtime"
