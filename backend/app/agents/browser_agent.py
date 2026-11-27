import json
import logging
from typing import Any

from openai import OpenAIError
from pydantic import ValidationError

from app.config import settings
from app.llm.azure_client import create_client, uses_responses_api
from app.llm.prompts import SYSTEM_PROMPT
from app.models.browser_action import BrowserAction
from app.models.adaptive_retry import StrategyProposal
from app.models.persona import Persona
from app.models.test_case import TestCase

logger = logging.getLogger(__name__)


class BrowserAgent:
    def __init__(self) -> None:
        self.client, self.extra_query = create_client()

    def decide(
        self,
        test_case: TestCase,
        persona: Persona,
        observation: dict[str, Any],
        history: list[dict[str, Any]],
        learning_context: dict[str, Any] | None = None,
    ) -> BrowserAction:
        request_options = {"extra_query": self.extra_query} if self.extra_query else {}
        try:
            system_prompt = (
                f"{SYSTEM_PROMPT}\n"
                f"Allowed website domain: {test_case.allowed_domain}. "
                "Do not navigate the top-level page outside this domain. "
                "Use retrieved learning experiences and prior attempt history when provided. "
                "Do not repeat a failed action or locator without new evidence."
            )
            user_input = json.dumps(
                {
                    "test_case": {
                        "id": test_case.test_case_id,
                        "name": test_case.name,
                        "goal": test_case.goal,
                        "completion_criteria": test_case.completion_criteria,
                        "product_keywords": test_case.product_keywords,
                        "cart_confirmation_phrases": (
                            test_case.cart_confirmation_phrases
                        ),
                    },
                    "persona": persona.model_dump(),
                    "observation": observation,
                    "recent_actions": history[-8:],
                    "learning_context": learning_context or {},
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
                logger.warning("Agent decision validation failed (fields=%s)", details)
            else:
                logger.warning("Agent decision failed (%s)", type(exc).__name__)
            raise RuntimeError(
                "The AI response did not contain a valid browser action."
            ) from exc

    def decide_with_learning_context(
        self,
        test_case: TestCase,
        persona: Persona,
        observation: dict[str, Any],
        history: list[dict[str, Any]],
        learning_context: dict[str, Any],
    ) -> BrowserAction:
        return self.decide(
            test_case,
            persona,
            observation,
            history,
            learning_context=learning_context,
        )

    def decide_retry(
        self,
        test_case: TestCase,
        persona: Persona,
        learning_context: dict[str, Any],
    ) -> StrategyProposal:
        request_options = {"extra_query": self.extra_query} if self.extra_query else {}
        system_prompt = (
            "You are an adaptive Playwright UX testing agent. Analyze all prior "
            "attempts, retrieved experiences, and current browser evidence. Use "
            "only evidence-supported strategies; do not invent locators or page "
            "states. Do not repeat a failed action plan without new evidence and "
            "an explicit justification. Stop if evidence shows an external "
            "application, server, network, or infrastructure failure. Mark only "
            "agent-related outcomes as learning-eligible. Return only a JSON "
            "object with fields: failure_assessment, failure_category, root_cause, "
            "learning_eligibility, retrieved_experience_ids, strategy_id, "
            "strategy_description, reasoning, supporting_evidence, "
            "differences_from_previous_strategy, proposed_actions, expected_result, "
            "verification_conditions, confidence, retry_recommendation. Use one "
            "of the six FailureCategory values and set retry_recommendation to "
            "continue or stop. A continuing retry needs one or more validated "
            "browser actions; a stopped retry may use an empty action list."
        )
        user_input = json.dumps(
            {
                "test_case": {
                    "id": test_case.test_case_id,
                    "goal": test_case.goal,
                    "completion_criteria": test_case.completion_criteria,
                },
                "learning_context": learning_context,
            },
            ensure_ascii=True,
        )
        try:
            if uses_responses_api():
                response = self.client.responses.create(
                    model=settings.azure_ai_model_deployment,
                    instructions=system_prompt,
                    input=user_input,
                    text={"format": {"type": "json_object"}},
                    max_output_tokens=1200,
                    **request_options,
                )
                content = response.output_text
            else:
                response = self.client.chat.completions.create(
                    model=settings.azure_ai_model_deployment,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_input},
                    ],
                    response_format={"type": "json_object"},
                    max_tokens=1200,
                    **request_options,
                )
                content = response.choices[0].message.content
            if not content:
                raise ValueError("Azure AI returned an empty retry strategy.")
            return StrategyProposal.model_validate_json(content)
        except OpenAIError as exc:
            raise RuntimeError("The LLM retry decision request failed.") from exc
        except (ValidationError, ValueError, IndexError) as exc:
            raise RuntimeError(
                "The AI response did not contain a valid adaptive retry strategy."
            ) from exc
