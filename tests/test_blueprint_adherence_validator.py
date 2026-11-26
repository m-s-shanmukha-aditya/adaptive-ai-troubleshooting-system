import pytest

from app.models.generated_response import (
    GeneratedTroubleshootingResponse,
)
from app.services.blueprint_adherence_validator import (
    BlueprintAdherenceValidator,
)
from app.services.blueprint_loader import load_blueprints


@pytest.fixture
def blueprint():
    blueprints = load_blueprints()
    return next(item for item in blueprints if item.category == "database")


def create_generated_response(step_numbers):
    return GeneratedTroubleshootingResponse(
        steps=[
            {
                "step_number": number,
                "instruction": f"Generated instruction {number}",
                "expected_result": f"Expected result {number}",
            }
            for number in step_numbers
        ],
        final_verification="Verify database connectivity.",
    )


def test_accepts_matching_step_numbers(blueprint):
    generated = create_generated_response([1, 2, 3, 4])

    assert BlueprintAdherenceValidator.validate(
        generated, blueprint
    ) is True


def test_rejects_missing_step(blueprint):
    generated = create_generated_response([1, 2, 3])

    assert BlueprintAdherenceValidator.validate(
        generated, blueprint
    ) is False


def test_rejects_extra_step(blueprint):
    generated = create_generated_response([1, 2, 3, 4, 5])

    assert BlueprintAdherenceValidator.validate(
        generated, blueprint
    ) is False


def test_rejects_wrong_order(blueprint):
    generated = create_generated_response([1, 2, 4, 3])

    assert BlueprintAdherenceValidator.validate(
        generated, blueprint
    ) is False
