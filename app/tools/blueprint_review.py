import argparse
from datetime import date
import getpass
import json
from pathlib import Path
import sys

from pydantic import ValidationError

from app.models.blueprint import BlueprintStatus, ProceduralBlueprint
from app.services.blueprint_gate import provisional_gate
from app.services.blueprint_loader import (
    BLUEPRINT_DIR,
    PROVISIONAL_BLUEPRINT_DIR,
    REJECTED_BLUEPRINT_DIR,
)
from app.services.blueprint_reindex import reindex


def _read_blueprint(path: Path) -> ProceduralBlueprint:
    with path.open("r", encoding="utf-8") as file:
        return ProceduralBlueprint.model_validate(json.load(file))


def _find_blueprint(blueprint_id: str, directories: tuple[Path, ...]) -> tuple[Path, ProceduralBlueprint]:
    for directory in directories:
        for path in sorted(directory.glob("*.json")):
            blueprint = _read_blueprint(path)
            if blueprint.blueprint_id == blueprint_id:
                return path, blueprint
    raise ValueError("blueprint not found")


def list_provisional() -> int:
    for path in sorted(PROVISIONAL_BLUEPRINT_DIR.glob("*.json")):
        blueprint = _read_blueprint(path)
        print(f"{blueprint.blueprint_id}\t{blueprint.title}\t{blueprint.category}")
    return 0


def show_blueprint(blueprint_id: str) -> int:
    _, blueprint = _find_blueprint(
        blueprint_id,
        (PROVISIONAL_BLUEPRINT_DIR, BLUEPRINT_DIR, REJECTED_BLUEPRINT_DIR),
    )
    print(json.dumps(blueprint.model_dump(mode="json"), indent=2, ensure_ascii=False))
    return 0


def _write_and_move(
    source: Path,
    destination_dir: Path,
    blueprint: ProceduralBlueprint,
) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    if destination.exists():
        raise FileExistsError("destination blueprint already exists")

    temporary = destination.with_name(destination.name + ".tmp")
    with temporary.open("x", encoding="utf-8") as file:
        json.dump(blueprint.model_dump(mode="json", exclude_none=True), file, indent=2)
        file.write("\n")
    try:
        temporary.replace(destination)
        source.unlink()
    except OSError:
        if temporary.exists():
            temporary.unlink()
        raise
    return destination


def approve_blueprint(blueprint_id: str) -> int:
    source, blueprint = _find_blueprint(blueprint_id, (PROVISIONAL_BLUEPRINT_DIR,))
    issues = provisional_gate(blueprint)
    if issues:
        print("Refusing approval: " + ", ".join(issues), file=sys.stderr)
        return 1

    approved = blueprint.model_copy(update={
        "status": BlueprintStatus.APPROVED,
        "active": True,
        "reviewed_by": getpass.getuser(),
        "reviewed_on": date.today().isoformat(),
    })
    _write_and_move(source, BLUEPRINT_DIR, approved)
    if reindex():
        print(f"Approved {blueprint_id}; the in-process retriever was reindexed.")
    else:
        print(f"Approved {blueprint_id}; restart the application to load it.")
    return 0


def reject_blueprint(blueprint_id: str) -> int:
    source, blueprint = _find_blueprint(blueprint_id, (PROVISIONAL_BLUEPRINT_DIR,))
    rejected = blueprint.model_copy(update={
        "status": BlueprintStatus.REJECTED,
        "active": False,
    })
    _write_and_move(source, REJECTED_BLUEPRINT_DIR, rejected)
    if reindex():
        print(f"Rejected {blueprint_id}; the in-process retriever was reindexed.")
    else:
        print(f"Rejected {blueprint_id}; restart the application to remove it.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review provisional troubleshooting blueprints.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    for name in ("show", "approve", "reject"):
        command = commands.add_parser(name)
        command.add_argument("blueprint_id")
    args = parser.parse_args(argv)

    try:
        if args.command == "list":
            return list_provisional()
        if args.command == "show":
            return show_blueprint(args.blueprint_id)
        if args.command == "approve":
            return approve_blueprint(args.blueprint_id)
        return reject_blueprint(args.blueprint_id)
    except (OSError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        print(f"Blueprint review failed: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())