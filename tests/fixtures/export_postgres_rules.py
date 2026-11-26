import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = PROJECT_ROOT / "tests"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from fixtures.postgres_rules import build_postgres_rules_blueprint


POSTGRES_ALIASES = ["postgres", "postgresql", "spring boot", "jdbc"]


def export_postgres_rules() -> dict[str, object]:
    blueprint = build_postgres_rules_blueprint()
    return {
        "domain": "postgresql",
        "aliases": POSTGRES_ALIASES.copy(),
        "observation_rules": [
            rule.model_dump(mode="json")
            for rule in blueprint.observation_rules
        ],
        "clarification_rules": [
            rule.model_dump(mode="json")
            for rule in blueprint.clarification_rules
        ],
        "comparison_rules": [
            rule.model_dump(mode="json")
            for rule in blueprint.comparison_rules
        ],
    }


if __name__ == "__main__":
    print(json.dumps(export_postgres_rules(), ensure_ascii=False, indent=2))
