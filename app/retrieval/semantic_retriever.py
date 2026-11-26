
import faiss
import numpy as np
import hashlib
import json
import threading


def SentenceTransformer(model_name: str):
    """Load the embedding dependency only when constructing the retriever."""
    from sentence_transformers import SentenceTransformer as Encoder
    return Encoder(model_name)

from app.models.blueprint import ProceduralBlueprint


class SemanticRetriever:
    def __init__(
        self,
        blueprints: list[ProceduralBlueprint],
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
    ):
        self.model_name = model_name
        self._lock = threading.RLock()
        self.model = None
        self.blueprints = []
        self.index = None
        self.index_metadata: list[dict[str, str]] = []
        self._snapshot_signature: str | None = None
        self.rebuild(blueprints)

    @staticmethod
    def _eligible(blueprint: ProceduralBlueprint) -> bool:
        return blueprint.runtime_eligible

    @staticmethod
    def _metadata(blueprint: ProceduralBlueprint) -> dict[str, str]:
        return {
            "blueprint_id": blueprint.blueprint_id,
            "version": blueprint.version,
            "content_hash": blueprint.content_hash(),
        }

    def rebuild(self, blueprints: list[ProceduralBlueprint]) -> bool:
        """Atomically replace the index; failed rebuilds clear stale results.

        Returns False when the eligible immutable snapshot is unchanged.
        """
        eligible = sorted(
            (item for item in blueprints if self._eligible(item)),
            key=lambda item: (item.blueprint_id, item.version, item.content_hash()),
        )
        metadata = [self._metadata(item) for item in eligible]
        signature = hashlib.sha256(
            json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        with self._lock:
            if signature == self._snapshot_signature:
                return False

            try:
                next_model = self.model
                next_index = None
                if eligible:
                    if next_model is None:
                        next_model = SentenceTransformer(self.model_name)
                    documents = [self._create_document(item) for item in eligible]
                    embeddings = next_model.encode(
                        documents,
                        convert_to_numpy=True,
                        normalize_embeddings=True,
                    ).astype(np.float32)
                    next_index = faiss.IndexFlatIP(embeddings.shape[1])
                    next_index.add(embeddings)
            except Exception:
                # Do not keep serving an index that may contain a retired revision.
                self.model = None
                self.blueprints = []
                self.index = None
                self.index_metadata = []
                self._snapshot_signature = None
                raise

            self.model = next_model
            self.blueprints = eligible
            self.index = next_index
            self.index_metadata = metadata
            self._snapshot_signature = signature
            return True

    @staticmethod
    def _create_document(
        blueprint: ProceduralBlueprint,
    ) -> str:
        return (
            f"Title: {blueprint.title}. "
            f"Problem: {blueprint.problem_summary}. "
            f"Symptoms: {' '.join(blueprint.symptoms)}. "
            f"Category: {blueprint.category}. "
            f"Tags: {' '.join(blueprint.tags)}."
        )

    def retrieve(
        self,
        query: str,
        top_k: int = 3,
    ) -> list[tuple[ProceduralBlueprint, float]]:
        if top_k <= 0 or not query.strip():
            return []
        with self._lock:
            if self.index is None or self.model is None:
                return []
            query_embedding = self.model.encode(
                [query],
                convert_to_numpy=True,
                normalize_embeddings=True,
            ).astype(np.float32)
            scores, indices = self.index.search(
                query_embedding,
                min(top_k, len(self.blueprints)),
            )
            return [
                (self.blueprints[index], float(score))
                for score, index in zip(scores[0], indices[0])
                if index != -1
            ]
