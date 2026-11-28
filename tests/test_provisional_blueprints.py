import json
from datetime import date
from unittest.mock import MagicMock
import weakref

import numpy as np
import pytest

import app.retrieval.semantic_retriever as retrieval_module
import app.services.blueprint_loader as loader_module
import app.services.blueprint_reindex as reindex_module
import app.services.troubleshooter as troubleshooter_module
import app.tools.blueprint_review as review_module
from app.models.blueprint import BlueprintStatus, ProceduralBlueprint
from app.services.blueprint_gate import provisional_gate
from app.services.blueprint_loader import load_blueprints
from app.services.troubleshooter import Troubleshooter


def make_blueprint(**updates):
    values = {
        "blueprint_id": "provisional-test",
        "version": "1.0.0",
        "active": True,
        "title": "Provisional test blueprint",
        "problem_summary": "A test connection issue.",
        "category": "database",
        "symptoms": ["connection refused"],
        "steps": [{
            "step_number": 1,
            "instruction": "Verify the service status.",
            "expected_result": "The observed status is recorded.",
        }],
        "verification_checks": [{
            "description": "Check the connection.",
            "success_condition": "The expected result is confirmed.",
        }],
        "status": BlueprintStatus.PROVISIONAL,
        "diagnoses": {
            "postgres_connection_issue_uncertain": {
                "meaning": "The cause is not yet known.",
                "supporting_evidence": [],
            }
        },
        "actions": {
            "verify_service_status": {
                "meaning": "Check whether the database service is running.",
                "diagnosis_ids": ["postgres_connection_issue_uncertain"],
            }
        },
    }
    values.update(updates)
    return ProceduralBlueprint.model_validate(values)


def write_blueprint(directory, blueprint):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{blueprint.blueprint_id}.json"
    path.write_text(
        json.dumps(blueprint.model_dump(mode="json", exclude_none=True), indent=2),
        encoding="utf-8",
    )
    return path


def isolated_blueprint_dirs(monkeypatch, tmp_path):
    blueprints = tmp_path / "blueprints"
    provisional = tmp_path / "blueprints_provisional"
    rejected = tmp_path / "blueprints_rejected"
    monkeypatch.setattr(loader_module, "BLUEPRINT_DIR", blueprints)
    monkeypatch.setattr(loader_module, "PROVISIONAL_BLUEPRINT_DIR", provisional)
    monkeypatch.setattr(loader_module, "REJECTED_BLUEPRINT_DIR", rejected)
    monkeypatch.setattr(review_module, "BLUEPRINT_DIR", blueprints)
    monkeypatch.setattr(review_module, "PROVISIONAL_BLUEPRINT_DIR", provisional)
    monkeypatch.setattr(review_module, "REJECTED_BLUEPRINT_DIR", rejected)
    return blueprints, provisional, rejected


def test_flag_off_does_not_load_provisional_blueprints(monkeypatch, tmp_path):
    blueprints, provisional, _ = isolated_blueprint_dirs(monkeypatch, tmp_path)
    approved = make_blueprint(
        blueprint_id="approved",
        status=BlueprintStatus.APPROVED,
    )
    write_blueprint(blueprints, approved)
    write_blueprint(provisional, make_blueprint())
    monkeypatch.delenv("ENABLE_PROVISIONAL_BLUEPRINTS", raising=False)

    assert make_blueprint().runtime_eligible is False
    loaded = load_blueprints()
    eligible = load_blueprints(eligible_only=True)

    assert [item.blueprint_id for item in loaded] == ["approved"]
    assert [item.blueprint_id for item in eligible] == ["approved"]


def test_flag_on_loads_provisionals_and_adds_response_label(monkeypatch, tmp_path):
    _, provisional_dir, _ = isolated_blueprint_dirs(monkeypatch, tmp_path)
    blueprint = make_blueprint()
    write_blueprint(provisional_dir, blueprint)
    monkeypatch.setenv("ENABLE_PROVISIONAL_BLUEPRINTS", "1")

    loaded = load_blueprints(eligible_only=True)
    assert [item.blueprint_id for item in loaded] == [blueprint.blueprint_id]

    candidate = json.dumps({
        "schema_version": "1",
        "diagnosis": {
            "id": "postgres_connection_issue_uncertain",
            "summary": "The cause is not yet known.",
            "status": "insufficient_evidence",
        },
        "evidence": [],
        "recommended_actions": [{
            "action_id": "verify_service_status",
            "description": "Check the service status.",
        }],
        "resolution_claim": "unverified",
        "follow_up_questions": [],
    })

    class FakeRetriever:
        def __init__(self, _blueprints, model_name=None):
            self.model_name = model_name

        def rebuild(self, _blueprints):
            return False

        def retrieve(self, _query, top_k=5):
            return [(blueprint, 1.0)]

    llm = type("FakeLLM", (), {"generate_response": lambda self, _prompt: candidate})()
    monkeypatch.setattr(troubleshooter_module, "SemanticRetriever", FakeRetriever)
    monkeypatch.setattr(troubleshooter_module, "LocalLLM", lambda: llm)
    monkeypatch.setattr(troubleshooter_module, "CloudLLM", lambda: pytest.fail("cloud call is not expected"))
    response = Troubleshooter(repository=MagicMock()).troubleshoot(
        "The PostgreSQL service is stopped and the application cannot connect."
    )

    assert response.startswith(
        "[Provisional blueprint: auto-generated, not yet reviewed]\n"
        "[Local candidate selected; runtime diagnosis is not ground-truth verified]"
    )


@pytest.mark.parametrize(
    "actions,expected",
    [
        ({"start_database": {"meaning": "Start the database."}}, "action_id_not_prefixed"),
        ({"verify_secret": {"meaning": "Share password with support."}}, "unsafe_action"),
    ],
)
def test_provisional_gate_rejects_unsafe_or_unprefixed_actions(actions, expected):
    blueprint = make_blueprint(actions=actions)

    assert expected in provisional_gate(blueprint)


def test_provisional_gate_rejects_unsafe_patterns():
    blueprint = make_blueprint(observation_rules=[{
        "id": "ambiguous_pattern",
        "description": "An unsafe pattern.",
        "patterns": ["(a|aa)+"],
    }])

    assert "unsafe_quantifier" in provisional_gate(blueprint)


def test_approve_revalidates_gate_and_moves_with_reviewer_metadata(monkeypatch, tmp_path):
    blueprints, provisional, _ = isolated_blueprint_dirs(monkeypatch, tmp_path)
    blueprint = make_blueprint()
    source = write_blueprint(provisional, blueprint)
    monkeypatch.setattr(review_module, "reindex", lambda: False)
    monkeypatch.setattr(review_module.getpass, "getuser", lambda: "reviewer")

    assert review_module.approve_blueprint(blueprint.blueprint_id) == 0

    destination = blueprints / source.name
    approved = ProceduralBlueprint.model_validate_json(destination.read_text(encoding="utf-8"))
    assert not source.exists()
    assert approved.status == BlueprintStatus.APPROVED
    assert approved.active is True
    assert approved.reviewed_by == "reviewer"
    assert approved.reviewed_on == date.today().isoformat()


def test_reject_moves_blueprint_to_rejected_directory(monkeypatch, tmp_path):
    _, provisional, rejected = isolated_blueprint_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(review_module, "reindex", lambda: False)
    blueprint = make_blueprint()
    source = write_blueprint(provisional, blueprint)

    assert review_module.reject_blueprint(blueprint.blueprint_id) == 0

    moved = ProceduralBlueprint.model_validate_json(
        (rejected / source.name).read_text(encoding="utf-8")
    )
    assert not source.exists()
    assert moved.status == BlueprintStatus.REJECTED
    assert moved.active is False


def test_approve_refuses_invalid_file(monkeypatch, tmp_path):
    blueprints, provisional, _ = isolated_blueprint_dirs(monkeypatch, tmp_path)
    source = provisional / "invalid.json"
    provisional.mkdir(parents=True)
    source.write_text(json.dumps({"blueprint_id": "invalid"}), encoding="utf-8")

    assert review_module.main(["approve", "invalid"]) == 1
    assert source.exists()
    assert not (blueprints / "invalid.json").exists()


def test_reindex_makes_new_provisional_blueprint_retrievable(monkeypatch, tmp_path):
    blueprints, provisional, _ = isolated_blueprint_dirs(monkeypatch, tmp_path)
    blueprints.mkdir()
    monkeypatch.setenv("ENABLE_PROVISIONAL_BLUEPRINTS", "1")
    blueprint = make_blueprint()
    write_blueprint(provisional, blueprint)

    class MockSentenceTransformer:
        def __init__(self, _model_name):
            pass

        def encode(self, documents, convert_to_numpy=True, normalize_embeddings=True):
            return np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (len(documents), 1))

    monkeypatch.setattr(retrieval_module, "SentenceTransformer", MockSentenceTransformer)
    monkeypatch.setattr(
        reindex_module,
        "_troubleshooters",
        weakref.WeakSet(),
    )
    troubleshooter = Troubleshooter(repository=object())

    assert reindex_module.reindex() is True
    assert troubleshooter.retriever is not None
    results = troubleshooter.retriever.retrieve("connection issue", top_k=1)
    assert [item.blueprint_id for item, _ in results] == [blueprint.blueprint_id]


def test_provenance_fields_do_not_change_approved_blueprint_hashes():
    blueprint = next(
        item for item in load_blueprints()
        if item.title == "Spring Boot PostgreSQL Connection Failure"
    )
    with_metadata = blueprint.model_copy(update={
        "source": "generator",
        "generated_by": "test",
        "generated_on": "2026-01-01",
        "reviewed_by": "reviewer",
        "reviewed_on": "2026-01-02",
    })

    assert with_metadata.content_hash() == blueprint.content_hash()