from pydantic import BaseModel, ConfigDict, Field


class GeneratedStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_number: int = Field(ge=1)
    instruction: str = Field(min_length=1)
    expected_result: str = Field(min_length=1)


class GeneratedTroubleshootingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[GeneratedStep] = Field(min_length=1)
    final_verification: str = Field(min_length=1)