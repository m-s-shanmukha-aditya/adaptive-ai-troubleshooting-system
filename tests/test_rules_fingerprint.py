import hashlib
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BLUEPRINT_PATH = (
    PROJECT_ROOT / "data" / "blueprints" / "postgres_connection.json"
)
RULE_FIELDS = (
    "observation_rules",
    "clarification_rules",
    "comparison_rules",
)


def test_postgres_rules_fingerprint_is_frozen():
    blueprint = json.loads(BLUEPRINT_PATH.read_text(encoding="utf-8"))
    rules = {field: blueprint[field] for field in RULE_FIELDS}
    canonical = json.dumps(
        rules,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    fingerprint = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # frozen at rules-frozen-v3; any change must be a deliberate re-freeze
    assert fingerprint == "dc2c2f341b3b832ebda8bdb8b5f6575e7658c480c2aab3a5d5d36fb59517b36b"
