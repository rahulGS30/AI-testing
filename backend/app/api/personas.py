from fastapi import APIRouter

from app.personas.persona_manager import list_personas

router = APIRouter(prefix="/api/personas", tags=["personas"])


@router.get("")
def get_personas() -> list[dict[str, object]]:
    return [persona.model_dump() for persona in list_personas()]
