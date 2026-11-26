
from app.services.blueprint_loader import load_blueprints
from app.models.blueprint import BlueprintStatus
from app.services.experience_repository import ExperienceRepository


def test_load_blueprints():
    blueprints = load_blueprints()

    assert len(blueprints) >= 1

    blueprint = next(item for item in blueprints if item.category == "database")

    assert blueprint.title
    assert blueprint.steps
    assert blueprint.verification_checks


def test_file_blueprint_ids_are_stable_across_repeated_loads():
    first = {item.title: item.blueprint_id for item in load_blueprints()}
    second = {item.title: item.blueprint_id for item in load_blueprints()}
    assert first == second
    assert all(first.values())


def test_existing_blueprints_remain_drafts_and_are_not_runtime_eligible():
    blueprints = load_blueprints()
    assert all(item.status == BlueprintStatus.DRAFT for item in blueprints)
    assert all(not item.active for item in blueprints)
    assert load_blueprints(eligible_only=True) == []


def test_blueprint_content_hash_is_stable_across_loads():
    first = {item.blueprint_id: item.content_hash() for item in load_blueprints()}
    second = {item.blueprint_id: item.content_hash() for item in load_blueprints()}
    assert first == second
    assert {
        item.blueprint_id: ExperienceRepository.version(item)
        for item in load_blueprints()
    } == first


def test_blueprint_content_hash_changes_when_authored_content_changes():
    blueprint = load_blueprints()[0]
    modified = blueprint.model_copy(update={"problem_summary": blueprint.problem_summary + " Updated."})
    assert modified.content_hash() != blueprint.content_hash()


def test_content_hash_excludes_lifecycle_and_generated_metadata():
    blueprint = load_blueprints()[0]
    metadata_only = blueprint.model_copy(update={
        "status": BlueprintStatus.APPROVED,
        "active": True,
        "success_count": 8,
        "updated_at": blueprint.updated_at.replace(year=blueprint.updated_at.year - 1),
    })
    assert metadata_only.content_hash() == blueprint.content_hash()
