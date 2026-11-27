from pydantic import BaseModel, ConfigDict, Field


class Persona(BaseModel):
    model_config = ConfigDict(frozen=True)

    persona_id: str
    name: str
    description: str
    experience_level: str
    technical_familiarity: str
    patience_level: str
    reading_preference: str
    navigation_behaviour: str
    accessibility_requirements: list[str] = Field(default_factory=list)
    viewport: dict[str, int]
    interaction_preferences: list[str] = Field(default_factory=list)
    task_completion_expectations: list[str] = Field(default_factory=list)
