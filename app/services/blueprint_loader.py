
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from app.models.blueprint import ProceduralBlueprint


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BLUEPRINT_DIR = PROJECT_ROOT / "data" / "blueprints"


def load_blueprints(*, eligible_only: bool = False) -> list[ProceduralBlueprint]:
    blueprints = []

    for file_path in sorted(BLUEPRINT_DIR.glob("*.json")):
        with file_path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        # Legacy files without an ID get a stable path-derived identity. Existing
        # checked-in records also persist this value directly in their JSON.
        data.setdefault(
            "blueprint_id",
            str(uuid5(NAMESPACE_URL, f"adaptive-troubleshooter:blueprint:{file_path.stem}")),
        )

        blueprint = ProceduralBlueprint.model_validate(data)
        if eligible_only and not blueprint.runtime_eligible:
            continue
        blueprints.append(blueprint)

    return blueprints
