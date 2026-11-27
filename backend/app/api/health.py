import logging

from fastapi import APIRouter, HTTPException
from openai import OpenAIError

from app.config import settings
from app.llm.azure_client import check_connection

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
def health_check() -> dict[str, object]:
    return {
        "status": "ok",
        "configuration": {
            "azure_ai_ready": settings.azure_ai_configured,
            "artifacts_dir": str(settings.artifacts_dir),
        },
    }


@router.get("/llm")
def check_llm_connection() -> dict[str, str]:
    if not settings.azure_ai_configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "Azure AI Foundry is not configured. Set AZURE_AI_ENDPOINT, "
                "AZURE_AI_API_KEY, AZURE_AI_MODEL_DEPLOYMENT, and "
                "AZURE_AI_API_VERSION in the root .env file."
            ),
        )

    try:
        check_connection()
    except OpenAIError as exc:
        logger.warning("Azure AI connectivity check failed (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=502,
            detail="Azure AI Foundry connectivity check failed. Verify the endpoint, deployment, API version, and credentials.",
        ) from exc

    return {"status": "connected"}
