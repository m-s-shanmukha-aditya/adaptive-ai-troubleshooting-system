import json
from unittest.mock import MagicMock

import app.services.troubleshooter as troubleshooter_module
from app.models.blueprint import BlueprintStatus
from app.services.blueprint_loader import load_blueprints
from app.services.troubleshooter import Troubleshooter


def test_cloud_escalation_when_local_response_fails(monkeypatch):
    approved = next(item for item in load_blueprints() if item.category == "database").model_copy(
        update={"status": BlueprintStatus.APPROVED, "active": True}
    )

    class FakeRetriever:
        def __init__(self, blueprints):
            self.blueprint = next(item for item in blueprints if item.category == "database")

        def retrieve(self, issue, top_k=1):
            return [(self.blueprint, 1.0)]

    monkeypatch.setattr(troubleshooter_module, "load_blueprints", lambda eligible_only=False: [approved])
    monkeypatch.setattr(troubleshooter_module, "SemanticRetriever", FakeRetriever)
    # Keep this integration-path test isolated from the user's append-only
    # runtime experience history.
    troubleshooter = Troubleshooter(repository=MagicMock())

    # Local LLM returns malformed JSON.
    troubleshooter.llm.generate_response = MagicMock(
        return_value="This is not valid structured JSON."
    )

    # Cloud LLM returns valid structured JSON.
    cloud_response = {
        "schema_version": "1",
        "diagnosis": {"id": "postgres_service_stopped", "summary": "The reported PostgreSQL service is stopped."},
        "evidence": [{"observation_id": "service_state", "interpretation": "The issue says the service is stopped."}],
        "recommended_actions": [{"action_id": "verify_service_status", "description": "Confirm PostgreSQL service status."}],
        "resolution_claim": "unverified",
    }

    mock_cloud = MagicMock()
    mock_cloud.generate_response.return_value = json.dumps(cloud_response)

    monkeypatch.setattr(
        troubleshooter_module,
        "CloudLLM",
        lambda: mock_cloud,
    )

    result = troubleshooter.troubleshoot(
        "My Spring Boot application's PostgreSQL service is stopped."
    )

    assert "Cloud candidate selected" in result
    assert "postgres_service_stopped" in result
    assert troubleshooter.llm.generate_response.call_count == troubleshooter.max_repairs + 1
    repair_prompt = troubleshooter.llm.generate_response.call_args_list[1].args[0]
    assert "REPAIR DEFECTS:" in repair_prompt

    mock_cloud.generate_response.assert_called_once()
