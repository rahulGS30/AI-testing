from app.models.persona import Persona

_PERSONAS = (
    Persona(
        persona_id="first_time_user",
        name="First-Time User",
        description="A new visitor who explores before acting and needs clear labels.",
        experience_level="beginner",
        technical_familiarity="low",
        patience_level="moderate",
        reading_preference="plain language, concise instructions",
        navigation_behaviour="explores obvious navigation before using search",
        accessibility_requirements=[],
        viewport={"width": 1365, "height": 900},
        interaction_preferences=["descriptive controls", "clear feedback"],
        task_completion_expectations=["find the requested product", "add it to cart"],
    ),
    Persona(
        persona_id="elderly_user",
        name="Elderly User",
        description="A user who benefits from readable text and uncomplicated navigation.",
        experience_level="beginner",
        technical_familiarity="low",
        patience_level="low",
        reading_preference="large, clear text and straightforward instructions",
        navigation_behaviour="uses clearly labelled controls and avoids dense menus",
        accessibility_requirements=["readable text", "large interactive targets"],
        viewport={"width": 1365, "height": 900},
        interaction_preferences=["large buttons", "visible feedback"],
        task_completion_expectations=["complete the requested task without checkout"],
    ),
    Persona(
        persona_id="low_vision_user",
        name="Low-Vision User",
        description="A user who needs readable content, contrast, and accessible names.",
        experience_level="intermediate",
        technical_familiarity="moderate",
        patience_level="moderate",
        reading_preference="highly readable text with clear hierarchy",
        navigation_behaviour="prefers accessible names and keyboard-friendly controls",
        accessibility_requirements=[
            "readable visual content",
            "sufficient contrast",
            "accessible names",
            "keyboard navigation",
        ],
        viewport={"width": 1280, "height": 960},
        interaction_preferences=["keyboard-accessible controls", "clear focus"],
        task_completion_expectations=["complete the requested task without checkout"],
    ),
    Persona(
        persona_id="non_native_english_speaker",
        name="Non-Native English Speaker",
        description="A user who prefers simple wording and unambiguous instructions.",
        experience_level="beginner",
        technical_familiarity="moderate",
        patience_level="moderate",
        reading_preference="simple, direct language",
        navigation_behaviour="checks labels carefully before selecting a control",
        accessibility_requirements=["plain language", "clear error messages"],
        viewport={"width": 1365, "height": 900},
        interaction_preferences=["unambiguous labels", "clear confirmation"],
        task_completion_expectations=["complete the requested task without checkout"],
    ),
    Persona(
        persona_id="impatient_power_user",
        name="Impatient Power User",
        description="An experienced user who values speed and minimal unnecessary steps.",
        experience_level="advanced",
        technical_familiarity="high",
        patience_level="low",
        reading_preference="brief and scannable",
        navigation_behaviour="uses direct search and avoids unnecessary detours",
        accessibility_requirements=[],
        viewport={"width": 1440, "height": 900},
        interaction_preferences=["fast search", "minimal steps", "immediate feedback"],
        task_completion_expectations=["add the requested item to cart; never purchase"],
    ),
)


def list_personas() -> list[Persona]:
    return list(_PERSONAS)


def get_persona(persona_id: str) -> Persona | None:
    return next(
        (persona for persona in _PERSONAS if persona.persona_id == persona_id), None
    )
