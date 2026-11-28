import json
import logging
from typing import Any

from openai import OpenAIError
from pydantic import ValidationError

from app.config import settings
from app.llm.azure_client import create_client, uses_responses_api
from app.llm.prompts import SYSTEM_PROMPT
from app.llm.token_usage import TokenUsageTracker
from app.models.browser_action import BrowserAction
from app.models.persona import Persona
from app.models.test_case import TestCase

logger = logging.getLogger(__name__)


class BrowserAgent:
    def __init__(self, token_usage: TokenUsageTracker | None = None) -> None:
        self.client, self.extra_query = create_client()
        self.token_usage = token_usage

    def decide(
        self,
        test_case: TestCase,
        persona: Persona,
        observation: dict[str, Any],
        history: list[dict[str, Any]],
    ) -> BrowserAction:
        request_options = {"extra_query": self.extra_query} if self.extra_query else {}
        try:
            system_prompt = (
                f"{SYSTEM_PROMPT}\n"
                f"Allowed website domain: {test_case.allowed_domain}. "
                "Do not navigate the top-level page outside this domain."
            )
            user_input = json.dumps(
                {
                    "test_case": {
                        "id": test_case.test_case_id,
                        "name": test_case.name,
                        "goal": test_case.goal,
                        "completion_criteria": test_case.completion_criteria,
                        "completion_mode": test_case.completion_mode,
                        "product_keywords": test_case.product_keywords,
                        "cart_confirmation_phrases": (
                            test_case.cart_confirmation_phrases
                        ),
                    },
                    "persona": persona.model_dump(),
                    "observation": observation,
                    "recent_actions": history[-8:],
                },
                ensure_ascii=True,
            )
            user_input = (
                "Return the next browser action as a JSON object matching the "
                f"required action schema.\n{user_input}"
            )
            if uses_responses_api():
                response = self.client.responses.create(
                    model=settings.azure_ai_model_deployment,
                    instructions=system_prompt,
                    input=user_input,
                    text={"format": {"type": "json_object"}},
                    max_output_tokens=500,
                    **request_options,
                )
                if self.token_usage is not None:
                    self.token_usage.record_response("browser_action", response)
                content = response.output_text
            else:
                response = self.client.chat.completions.create(
                    model=settings.azure_ai_model_deployment,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_input},
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=500,
                    **request_options,
                )
                if self.token_usage is not None:
                    self.token_usage.record_response("browser_action", response)
                content = response.choices[0].message.content
            if not content:
                raise ValueError("Azure AI returned an empty action.")
            return BrowserAction.model_validate_json(content)
        except OpenAIError as exc:
            status = getattr(exc, "status_code", None)
            logger.warning(
                "Azure AI request failed (exception=%s, status=%s)",
                type(exc).__name__,
                status,
            )
            status_detail = f"HTTP {status}" if status else type(exc).__name__
            raise RuntimeError(
                f"Azure AI request failed ({status_detail}). Check the endpoint, "
                "API mode, deployment name, and model API support."
            ) from exc
        except (ValidationError, ValueError, IndexError) as exc:
            if isinstance(exc, ValidationError):
                details = [
                    f"{'.'.join(str(part) for part in error['loc'])}:{error['type']}"
                    for error in exc.errors(include_input=False)
                ]
                logger.warning(
                    "Agent decision validation failed (fields=%s)", details
                )
            else:
                logger.warning("Agent decision failed (%s)", type(exc).__name__)
            raise RuntimeError(
                "The AI response did not contain a valid browser action."
            ) from exc
