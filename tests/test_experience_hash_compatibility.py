import json
from pathlib import Path

from app.services.experience_repository import ExperienceRepository


def test_legacy_records_remain_unchanged_and_are_not_treated_as_canonical():
    path = Path(".experience-hash-compat-legacy.jsonl")
    path.unlink(missing_ok=True)
    legacy = {
        "mode": "runtime",
        "issue": "same issue",
        "blueprint_version": "same-hash-value",
        "verified": True,
        "failure_patterns": ["legacy pattern"],
    }
    original_line = json.dumps(legacy) + "\n"
    try:
        path.write_text(original_line, encoding="utf-8")
        repository = ExperienceRepository(path)

        loaded = repository.load_records()

        assert loaded == [legacy]
        assert repository.hash_scheme(loaded[0]) == ExperienceRepository.LEGACY_HASH_SCHEME
        assert "blueprint_hash_scheme" not in loaded[0]
        assert "blueprint_id" not in loaded[0]
        # Even if a legacy digest equals a current digest string, its missing scheme
        # marker prevents it from being reused as a canonical reference.
        assert repository.reusable_patterns("same-hash-value", "same issue") == []
        assert path.read_text(encoding="utf-8") == original_line
    finally:
        path.unlink(missing_ok=True)


def test_new_experience_records_identify_canonical_hash_scheme():
    path = Path(".experience-hash-compat-new.jsonl")
    path.unlink(missing_ok=True)
    repository = ExperienceRepository(path)
    try:
        repository.save({
            "mode": "runtime",
            "issue": "same issue",
            "blueprint_version": "same-hash-value",
            "verified": True,
            "resolution_evidence": {
                "kind": "live_connection_check",
                "connection_established": True,
            },
            "failure_patterns": ["canonical pattern"],
        })

        rows = repository.load_records()

        assert len(rows) == 1
        assert rows[0]["blueprint_hash_scheme"] == ExperienceRepository.CANONICAL_HASH_SCHEME
        assert repository.hash_scheme(rows[0]) == ExperienceRepository.CANONICAL_HASH_SCHEME
        assert "blueprint_id" not in rows[0]
        assert repository.reusable_patterns("same-hash-value", "same issue") == ["canonical pattern"]
    finally:
        path.unlink(missing_ok=True)


def test_save_evaluation_marks_canonical_hash_without_fabricating_blueprint_id():
    path = Path(".experience-hash-compat-evaluation.jsonl")
    path.unlink(missing_ok=True)
    repository = ExperienceRepository(path)
    try:
        repository.save_evaluation(
            mode="runtime",
            issue="an issue",
            blueprint_version="canonical-digest",
            candidate="{}",
            diagnosis_correct=None,
            action_quality=None,
            evidence_consistent=None,
            resolution_verified=False,
            evaluator_version="test",
        )

        record = repository.load_records()[0]
        assert record["blueprint_version"] == "canonical-digest"
        assert record["blueprint_hash_scheme"] == ExperienceRepository.CANONICAL_HASH_SCHEME
        assert "blueprint_id" not in record
    finally:
        path.unlink(missing_ok=True)
