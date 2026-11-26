
from app.models.blueprint import (
    ProceduralBlueprint,
    TroubleshootingStep,
    VerificationCheck,
    BlueprintStatus,
)


def test_blueprint_creation():
    blueprint = ProceduralBlueprint(
        title="PostgreSQL Connection Failure",
        problem_summary="Spring Boot cannot connect to PostgreSQL.",
        category="database",
        symptoms=[
            "Connection refused",
            "Application startup failure",
        ],
        environment={
            "framework": "Spring Boot",
            "database": "PostgreSQL",
        },
        preconditions=[
            "PostgreSQL is installed",
        ],
        steps=[
            TroubleshootingStep(
                step_number=1,
                instruction="Check whether PostgreSQL is running.",
                expected_result="PostgreSQL service is active.",
                failure_action="Start the PostgreSQL service.",
            )
        ],
        verification_checks=[
            VerificationCheck(
                description="Check database connectivity.",
                success_condition="Application connects successfully.",
            )
        ],
    )

    assert blueprint.title == "PostgreSQL Connection Failure"
    assert blueprint.status == BlueprintStatus.DRAFT
    assert len(blueprint.steps) == 1
    assert blueprint.success_count == 0
    assert blueprint.blueprint_id is not None
