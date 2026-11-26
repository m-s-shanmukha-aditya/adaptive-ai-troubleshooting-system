import json
import logging

from app.services.solution_evaluator import SolutionEvaluator
from app.services.troubleshooter import Troubleshooter
from app.services.issue_analyzer import IssueAnalyzer
from app.services.blueprint_loader import load_blueprints


def test_invalid_json_logs_position_but_never_candidate_or_secret(caplog):
    raw = '{"password":"do-not-log-this-secret",'
    with caplog.at_level(logging.WARNING, logger="app.services.solution_evaluator"):
        result = SolutionEvaluator().evaluate_runtime(raw, {})

    assert "malformed_or_invalid_schema" in result.repair_reasons
    assert "invalid_json" in caplog.text
    assert "line=1" in caplog.text and "column=" in caplog.text
    assert "do-not-log-this-secret" not in caplog.text
    assert raw not in caplog.text


def test_schema_failure_logs_field_and_safe_shape_without_values(caplog):
    raw = json.dumps({
        "schema_version": "1",
        "diagnosis": {"summary": "private user-provided text", "status": "unsupported"},
        "evidence": [],
        "recommended_actions": [],
        "resolution_claim": "unverified",
    })
    with caplog.at_level(logging.WARNING, logger="app.services.solution_evaluator"):
        result = SolutionEvaluator().evaluate_runtime(raw, {})

    assert "malformed_or_invalid_schema" in result.repair_reasons
    assert "schema_validation" in caplog.text
    assert "diagnosis.id" in caplog.text
    assert "diagnosis.status" in caplog.text
    assert "private user-provided text" not in caplog.text
    assert raw not in caplog.text


def test_runtime_prompt_documents_complete_candidate_schema_and_credential_ids():
    context = IssueAnalyzer().analyze(
        "username or password is incorrect; PostgreSQL service is running and datasource host and port are correct"
    )
    prompt = Troubleshooter._candidate_prompt(
        context.issue, context, next(item for item in load_blueprints() if item.category == "database")
    )

    assert '"schema_version":"1"' in prompt
    assert '"status":"uncertain"' in prompt
    assert '"resolution_claim":"unverified"' in prompt
    assert "no markdown fences" in prompt
    assert "database_credentials_invalid" in prompt
    assert "verify_credentials_securely" in prompt
