import json
import logging
from typing import Any

from openai import OpenAIError
from pydantic import ValidationError

from app.config import settings
from app.llm.azure_client import create_client, uses_responses_api
from app.llm.token_usage import TokenUsageTracker
from app.llm.ux_analysis_prompt import UX_ANALYSIS_PROMPT
from app.models.persona import Persona
from app.models.ux_analysis import UXAnalysis
from app.models.test_case import TestCase

logger = logging.getLogger(__name__)


class UXAnalyzer:
    def __init__(self, token_usage: TokenUsageTracker | None = None) -> None:
        self.client, self.extra_query = create_client()
        self.token_usage = token_usage

    def analyze(
        self,
        test_case: TestCase,
        persona: Persona,
        run_result: dict[str, Any],
        action_history: dict[str, Any],
    ) -> UXAnalysis:
        request_options = {"extra_query": self.extra_query} if self.extra_query else {}
        user_input = json.dumps(
            {
                "test_case": test_case.model_dump(mode="json"),
                "persona": persona.model_dump(),
                "run_result": {
                    key: run_result.get(key)
                    for key in ("status", "completed", "step_count", "result", "error")
                },
                "action_history": action_history,
            },
            ensure_ascii=True,
        )
        user_input = (
            "Analyze this executed persona UX test and return a JSON object matching "
            f"the required evidence-citation schema.\n{user_input}"
        )
        for attempt in range(2):
            try:
                content = self._request_content(user_input, request_options)
                if not content:
                    raise ValueError("The UX analysis response was empty.")
                analysis = UXAnalysis.model_validate_json(content)
                self._validate_evidence(analysis, action_history, run_result)
                return analysis
            except OpenAIError as exc:
                status = getattr(exc, "status_code", None)
                logger.warning(
                    "UX analysis request failed (exception=%s, status=%s)",
                    type(exc).__name__,
                    status,
                )
                status_detail = f"HTTP {status}" if status else type(exc).__name__
                raise RuntimeError(
                    f"UX analysis request failed ({status_detail})."
                ) from exc
            except (ValidationError, ValueError, IndexError) as exc:
                reason = self._rejection_reason(exc)
                if attempt == 0:
                    logger.info(
                        "UX analysis output rejected; retrying once (reason=%s)",
                        reason,
                    )
                    user_input = (
                        f"{user_input}\n\nThe previous response was rejected: {reason}\n"
                        "Return a corrected JSON object matching the exact schema. "
                        "Use only exact quotes from the supplied recorded evidence. "
                        "If a finding cannot be supported by an exact citation, remove "
                        "that finding and list the uncertainty in not_assessed."
                    )
                    continue

                logger.warning(
                    "UX analysis output rejected after retry (reason=%s)",
                    reason,
                )
                if any(
                    marker in reason.casefold()
                    for marker in ("citation", "quote", "evidence", "observation")
                ):
                    message = (
                        "The UX analysis response did not cite recorded evidence "
                        "correctly after one retry."
                    )
                else:
                    message = (
                        "The UX analysis response remained invalid after one retry."
                    )
                raise RuntimeError(message) from exc

        raise RuntimeError("UX analysis did not produce a valid report.")

    def _request_content(
        self,
        user_input: str,
        request_options: dict[str, Any],
    ) -> str | None:
        if uses_responses_api():
            response = self.client.responses.create(
                model=settings.azure_ai_model_deployment,
                instructions=UX_ANALYSIS_PROMPT,
                input=user_input,
                text={"format": {"type": "json_object"}},
                max_output_tokens=2500,
                **request_options,
            )
            if self.token_usage is not None:
                self.token_usage.record_response("ux_analysis", response)
            return response.output_text

        response = self.client.chat.completions.create(
            model=settings.azure_ai_model_deployment,
            messages=[
                {"role": "system", "content": UX_ANALYSIS_PROMPT},
                {"role": "user", "content": user_input},
            ],
            response_format={"type": "json_object"},
            max_tokens=2500,
            **request_options,
        )
        if self.token_usage is not None:
            self.token_usage.record_response("ux_analysis", response)
        return response.choices[0].message.content

    @staticmethod
    def _rejection_reason(exc: Exception) -> str:
        if isinstance(exc, ValidationError):
            issues = exc.errors(include_input=False)
            return "; ".join(
                f"{'.'.join(str(part) for part in issue.get('loc', ())) or 'response'}:"
                f" {issue.get('type', 'invalid')}"
                for issue in issues[:5]
            )
        return str(exc) or type(exc).__name__

    @staticmethod
    def _validate_evidence(
        analysis: UXAnalysis,
        action_history: dict[str, Any],
        run_result: dict[str, Any],
    ) -> None:
        actions = {
            entry["step"]: entry for entry in action_history.get("actions", [])
        }
        initial = action_history.get("initial_observation")
        for finding in [*analysis.strengths, *analysis.improvements]:
            for citation in finding.evidence:
                if citation.source == "run_result":
                    if citation.step != 0 or citation.stage != "initial":
                        raise ValueError("Run-result citations must use initial step 0.")
                    source_text = " ".join(
                        str(run_result.get(field) or "")
                        for field in ("status", "result", "error")
                    )
                elif citation.step == 0:
                    if citation.stage != "initial" or not initial:
                        raise ValueError("Citation references missing initial observation.")
                    source_text = UXAnalyzer._citation_source(
                        initial, citation.source
                    )
                else:
                    if citation.stage == "initial":
                        raise ValueError("Only step 0 can cite the initial observation.")
                    entry = actions.get(citation.step)
                    if entry is None:
                        raise ValueError("Citation references an unknown action step.")
                    if citation.source == "action":
                        source_text = UXAnalyzer._action_citation_source(entry)
                    else:
                        snapshot = entry.get(
                            "observation_before"
                            if citation.stage == "before"
                            else "observation_after"
                        )
                        if snapshot is None:
                            raise ValueError("Citation references a missing observation.")
                        source_text = UXAnalyzer._citation_source(
                            snapshot, citation.source
                        )
                if citation.quote.casefold() not in source_text.casefold():
                    raise ValueError("Citation quote was not found in recorded evidence.")

    @staticmethod
    def _citation_source(snapshot: dict[str, Any], source: str) -> str:
        if source == "page_text":
            return str(snapshot.get("visible_text", ""))
        if source == "control":
            return json.dumps(snapshot.get("interactive_elements", []), ensure_ascii=False)
        raise ValueError("This citation source is not valid for an observation.")

    @staticmethod
    def _action_citation_source(entry: dict[str, Any]) -> str:
        evidence = {
            key: entry.get(key)
            for key in (
                "step",
                "execution",
                "error",
                "result",
                "before",
                "after",
            )
            if entry.get(key) is not None
        }
        evidence["decision"] = {
            key: entry.get("decision", {}).get(key)
            for key in ("action", "target", "value")
            if entry.get("decision", {}).get(key) is not None
        }
        return json.dumps(
            evidence,
            ensure_ascii=False,
        )
