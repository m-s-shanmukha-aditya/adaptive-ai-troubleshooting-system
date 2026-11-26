
import numpy as np
import pytest

from app.services.blueprint_loader import load_blueprints
import app.retrieval.semantic_retriever as retrieval_module
from app.models.blueprint import BlueprintStatus


def test_semantic_retrieval(monkeypatch):
    class MockSentenceTransformer:
        def __init__(self, model_name):
            self.model_name = model_name

        def encode(self, documents, convert_to_numpy=True, normalize_embeddings=True):
            # Deterministic mock embedding keeps this unit test offline.
            return np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (len(documents), 1))

    monkeypatch.setattr(retrieval_module, "SentenceTransformer", MockSentenceTransformer)
    blueprints = [
        item.model_copy(update={"status": BlueprintStatus.APPROVED, "active": True})
        for item in load_blueprints() if item.category == "database"
    ]

    retriever = retrieval_module.SemanticRetriever(blueprints)

    query = (
        "My Java backend application cannot connect "
        "to the PostgreSQL database."
    )

    results = retriever.retrieve(query, top_k=1)

    assert len(results) == 1

    blueprint, score = results[0]

    assert blueprint.category == "database"
    assert score > 0.0


def test_retriever_indexes_only_approved_active_blueprints(monkeypatch):
    class MockSentenceTransformer:
        def __init__(self, _model_name):
            pass

        def encode(self, documents, convert_to_numpy=True, normalize_embeddings=True):
            return np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (len(documents), 1))

    monkeypatch.setattr(retrieval_module, "SentenceTransformer", MockSentenceTransformer)
    loaded = load_blueprints()
    base = loaded[0]
    candidates = [
        base.model_copy(update={"blueprint_id": "draft", "status": BlueprintStatus.DRAFT, "active": True}),
        base.model_copy(update={"blueprint_id": "approved", "status": BlueprintStatus.APPROVED, "active": True}),
        base.model_copy(update={"blueprint_id": "inactive", "status": BlueprintStatus.APPROVED, "active": False}),
        base.model_copy(update={"blueprint_id": "deprecated", "status": BlueprintStatus.DEPRECATED, "active": True}),
        base.model_copy(update={"blueprint_id": "archived", "status": BlueprintStatus.ARCHIVED, "active": True}),
    ]

    retriever = retrieval_module.SemanticRetriever(candidates)

    assert [item.blueprint_id for item in retriever.blueprints] == ["approved"]
    assert retriever.index.ntotal == 1
    assert retriever.index_metadata == [{
        "blueprint_id": "approved",
        "version": "1.0.0",
        "content_hash": candidates[1].content_hash(),
    }]


def test_rebuild_replaces_snapshot_and_removes_stale_revisions(monkeypatch):
    class MockSentenceTransformer:
        def __init__(self, _model_name):
            pass

        def encode(self, documents, convert_to_numpy=True, normalize_embeddings=True):
            return np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (len(documents), 1))

    monkeypatch.setattr(retrieval_module, "SentenceTransformer", MockSentenceTransformer)
    blueprints = [
        item.model_copy(update={"blueprint_id": f"approved-{i}", "status": BlueprintStatus.APPROVED, "active": True})
        for i, item in enumerate(load_blueprints())
    ]
    retriever = retrieval_module.SemanticRetriever(blueprints)
    assert len(retriever.index_metadata) == len(blueprints)

    remaining = blueprints[-1]
    assert retriever.rebuild([remaining]) is True
    assert retriever.rebuild([remaining]) is False
    assert [metadata["blueprint_id"] for metadata in retriever.index_metadata] == [remaining.blueprint_id]
    assert retriever.index.ntotal == 1
    results = retriever.retrieve("gateway issue", top_k=5)
    assert [item.blueprint_id for item, _ in results] == [remaining.blueprint_id]

    revised = remaining.model_copy(update={"problem_summary": remaining.problem_summary + " Revised."})
    assert retriever.rebuild([revised]) is True
    assert retriever.index_metadata[0]["content_hash"] == revised.content_hash()
    assert retriever.index_metadata[0]["version"] == revised.version

    assert retriever.rebuild([]) is True
    assert retriever.index is None
    assert retriever.index_metadata == []
    assert retriever.retrieve("gateway issue") == []


def test_failed_rebuild_fails_closed_instead_of_serving_stale_index(monkeypatch):
    class MockSentenceTransformer:
        def __init__(self, _model_name):
            self.fail = False

        def encode(self, documents, convert_to_numpy=True, normalize_embeddings=True):
            if self.fail:
                raise RuntimeError("embedding unavailable")
            return np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (len(documents), 1))

    monkeypatch.setattr(retrieval_module, "SentenceTransformer", MockSentenceTransformer)
    blueprint = load_blueprints()[0].model_copy(update={
        "status": BlueprintStatus.APPROVED, "active": True,
    })
    retriever = retrieval_module.SemanticRetriever([blueprint])
    retriever.model.fail = True

    changed = blueprint.model_copy(update={"version": "2.0.0"})
    with pytest.raises(RuntimeError, match="embedding unavailable"):
        retriever.rebuild([changed])

    assert retriever.index is None
    assert retriever.blueprints == []
    assert retriever.index_metadata == []
    assert retriever.retrieve("any issue") == []
