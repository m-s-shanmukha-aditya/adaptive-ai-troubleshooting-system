import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ExperienceRepository:
    """Append-only local record. Heuristic outcomes are never promoted to verified."""
    CANONICAL_HASH_SCHEME = "sha256-canonical-content-v1"
    LEGACY_HASH_SCHEME = "legacy-unversioned"

    def __init__(self, path: str | Path = "data/experience.jsonl"):
        self.path = Path(path)

    @staticmethod
    def version(blueprint) -> str:
        if hasattr(blueprint, "content_hash"):
            return blueprint.content_hash()
        content = dict(blueprint) if isinstance(blueprint, dict) else blueprint
        if isinstance(content, dict):
            for key in (
                "blueprint_id", "version", "active", "status", "success_count",
                "failure_count", "created_at", "updated_at",
            ):
                content.pop(key, None)
        payload = json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def save(self, record: dict) -> None:
        # This application has no live-resolution verifier yet. Prevent heuristic
        # or controlled evaluation records from becoming verified experience.
        record = dict(record)
        if record.get("blueprint_version") and not record.get("blueprint_hash_scheme"):
            record["blueprint_hash_scheme"] = self.CANONICAL_HASH_SCHEME
        evidence = record.get("resolution_evidence")
        has_resolution_proof = (
            isinstance(evidence, dict)
            and evidence.get("kind") == "live_connection_check"
            and evidence.get("connection_established") is True
        )
        record["verified"] = bool(record.get("verified") is True and has_resolution_proof)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    @classmethod
    def hash_scheme(cls, record: dict) -> str:
        """Return a record's declared scheme without rewriting legacy records."""
        return record.get("blueprint_hash_scheme") or cls.LEGACY_HASH_SCHEME

    def load_records(self) -> list[dict]:
        """Read JSONL rows as stored; missing IDs and scheme markers stay missing."""
        if not self.path.exists():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                logger.warning("Ignoring malformed experience record")
                continue
            if isinstance(record, dict):
                records.append(record)
            else:
                logger.warning("Ignoring non-object experience record")
        return records

    def save_evaluation(self, *, mode: str, issue: str, blueprint_version: str,
                        candidate: str, diagnosis_correct: bool | None,
                        action_quality: bool | None, evidence_consistent: bool | None,
                        resolution_verified: bool, evaluator_version: str,
                        scenario_id: str | None = None,
                        repair_reasons: list[str] | None = None,
                        diagnostic_status: str | None = None) -> None:
        """Persist mode-scoped results while keeping resolution verification separate."""
        self.save({
            "mode": mode, "issue": issue, "blueprint_version": blueprint_version,
            "scenario_id": scenario_id, "candidate": candidate,
            "diagnostic_status": diagnostic_status,
            "diagnosis_correct": diagnosis_correct, "action_quality": action_quality,
            "evidence_consistent": evidence_consistent,
            "resolution_verified": bool(resolution_verified), "verified": False,
            "evaluation_kind": mode, "evaluator_version": evaluator_version,
            "blueprint_hash_scheme": self.CANONICAL_HASH_SCHEME,
            "repair_reasons": repair_reasons or [],
        })

    def reusable_patterns(self, blueprint_version: str, issue: str) -> list[str]:
        result = []
        for row in self.load_records():
            if (
                self.hash_scheme(row) == self.CANONICAL_HASH_SCHEME
                and row.get("verified")
                and row.get("blueprint_version") == blueprint_version
                and row.get("issue") == issue
            ):
                result.extend(row.get("failure_patterns", []))
        return result
