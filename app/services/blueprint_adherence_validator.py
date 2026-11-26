from app.models.blueprint import ProceduralBlueprint
from app.models.generated_response import (
    GeneratedTroubleshootingResponse,
)


class BlueprintAdherenceValidator:

    @staticmethod
    def validate(
        generated: GeneratedTroubleshootingResponse,
        blueprint: ProceduralBlueprint,
    ) -> bool:

        # Generated output must contain exactly as many steps
        # as the retrieved blueprint.
        if len(generated.steps) != len(blueprint.steps):
            return False

        # Step numbers and ordering must match exactly.
        blueprint_step_numbers = [
            step.step_number for step in blueprint.steps
        ]

        generated_step_numbers = [
            step.step_number for step in generated.steps
        ]

        if generated_step_numbers != blueprint_step_numbers:
            return False

        # Final verification must be present.
        if not generated.final_verification.strip():
            return False

        return True