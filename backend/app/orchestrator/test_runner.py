from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from openai import OpenAIError
from playwright.sync_api import Error as PlaywrightError

from app.agents.browser_agent import BrowserAgent
from app.agents.ux_analyzer import UXAnalyzer
from app.browser.playwright_controller import PlaywrightController, UnsafeNavigationError
from app.config import PROJECT_ROOT, settings
from app.models.browser_action import BrowserAction, BrowserActionType
from app.models.test_case import TestCase
from app.personas.persona_manager import get_persona
from app.learning.adaptive_retry_engine import AdaptiveRetryEngine
from app.models.adaptive_retry import FailureCategory

logger = logging.getLogger(__name__)
CASES_DIRECTORY = PROJECT_ROOT / "test_cases"


def _normalized_text(value: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", value.casefold()).split())


def _is_cart_confirmed(
    observation: dict[str, Any], test_case: TestCase
) -> bool:
    text = _normalized_text(observation.get("visible_text", ""))
    title = _normalized_text(observation.get("title", ""))
    url = observation.get("url", "").casefold()
    item_present = all(
        _normalized_text(keyword) in text for keyword in test_case.product_keywords
    )
    confirmation_present = any(
        _normalized_text(phrase) in text
        for phrase in test_case.cart_confirmation_phrases
    )
    cart_count_confirmed = any(
        "cart" in element.get("href", "").casefold()
        and re.search(r"\b1\s+items?\b", _normalized_text(element.get("label", "")))
        for element in observation.get("interactive_elements", [])
    )
    on_cart_page = (
        "cart" in title
        or bool(re.search(r"/(?:[^/?#]+/)?cart(?:[/?#]|$)", url))
    )
    return item_present and (
        confirmation_present or on_cart_page or cart_count_confirmed
    )


def _obstacle(observation: dict[str, Any]) -> str | None:
    text = observation.get("visible_text", "").casefold()
    if any(
        marker in text
        for marker in (
            "sorry, we just need to make sure you're not a robot",
            "robot check",
            "verify your browser",
            "verify you are human",
            "browser verification",
            "checking your browser",
            "security check",
            "unusual traffic",
            "enter the characters you see below",
            "type the characters you see in this image",
            "captcha",
        )
    ):
        return "The site presented a bot check or CAPTCHA; the agent stopped without bypassing it."
    if any(
        marker in text
        for marker in (
            "sign in to continue",
            "sign in to add this item",
            "sign in to complete your request",
        )
    ):
        return "The site requires sign-in to continue; no credentials were entered."
    return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _save_screenshot(browser, step_number: int) -> str | None:
    try:
        return browser.save_screenshot(step_number)
    except (PlaywrightError, OSError, AttributeError) as exc:
        logger.warning("Could not capture screenshot for step %s: %s", step_number, exc)
        return None


def _observation_evidence(observation: dict[str, Any]) -> dict[str, Any]:
    return {
        "url": observation.get("url", ""),
        "title": observation.get("title", ""),
        "visible_text": observation.get("visible_text", "")[:12000],
        "interactive_elements": [
            {
                key: element.get(key)
                for key in ("tag", "role", "type", "label", "placeholder", "href")
                if element.get(key) is not None
            }
            for element in observation.get("interactive_elements", [])[:100]
        ],
    }


def run_test_case(
    test_case: TestCase,
    *,
    headless: bool = True,
    agent: BrowserAgent | None = None,
    artifacts_directory: Path | None = None,
    run_directory: Path | None = None,
    run_id: str | None = None,
    batch_id: str | None = None,
) -> dict[str, Any]:
    is_precreated_run = run_id is not None
    run_id = run_id or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    root = artifacts_directory or settings.artifacts_dir / test_case.test_case_id
    run_directory = run_directory or root / run_id
    run_directory.mkdir(parents=True, exist_ok=is_precreated_run)
    history: list[dict[str, Any]] = []
    started_at = datetime.now(timezone.utc)
    persona = get_persona(test_case.persona_id)
    result: dict[str, Any] = {
        "run_id": run_id,
        "batch_id": batch_id,
        "test_case_id": test_case.test_case_id,
        "name": test_case.name,
        "website": str(test_case.website),
        "persona_id": test_case.persona_id,
        "persona_name": persona.name if persona else test_case.persona_id,
        "goal": test_case.goal,
        "status": "running",
        "completed": False,
        "step_count": 0,
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "result": None,
        "error": None,
        "analysis": {"status": "pending"},
        "artifacts": {
            "directory": str(run_directory.relative_to(PROJECT_ROOT))
            if run_directory.is_relative_to(PROJECT_ROOT)
            else str(run_directory),
            "screenshots": [],
            "video": None,
            "action_history": "action_history.json",
            "ux_analysis": "ux_analysis.json",
        },
    }
    _write_json(run_directory / "result.json", result)

    adaptive_engine = AdaptiveRetryEngine(
        max_retries=getattr(settings, "max_retries", 3)
    )
    adaptive_engine.start_run(run_id)
    _write_json(run_directory / "ux_analysis.json", {"status": "pending"})
    initial_observation: dict[str, Any] | None = None
    browser: PlaywrightController | None = None
    final_failure: dict[str, Any] | None = None
    try:
        if persona is None:
            raise ValueError(f"Unknown persona id '{test_case.persona_id}'.")
        if agent is None:
            agent = BrowserAgent()

        with PlaywrightController(
            test_case,
            run_directory,
            timeout_ms=settings.browser_timeout,
            headless=headless,
            viewport=persona.viewport,
        ) as browser:
            browser.open()
            observation = browser.observe()
            initial_observation = _observation_evidence(observation)
            screenshot = _save_screenshot(browser, 0)
            if screenshot:
                result["artifacts"]["screenshots"].append(screenshot)
            step_limit = min(test_case.max_steps, settings.max_agent_steps)
            if browser.blocked_navigation:
                result.update(status="blocked", result=browser.blocked_navigation)
                final_failure = {"reason": browser.blocked_navigation, "terminal": True}
            else:
                initial_experiences = adaptive_engine.retrieve_context(
                    test_case.test_case_id,
                    test_case.goal,
                    initial_observation,
                )
                previous_observation = observation
                max_attempts = adaptive_engine.max_attempts

                for attempt_number in range(1, max_attempts + 1):
                    if result["completed"] or result["status"] == "blocked":
                        break
                    attempt_actions: list[dict[str, Any]] = []
                    proposal = None
                    proposal_validation: dict[str, Any] | None = None
                    strategy_id = "STRATEGY_INITIAL_" + uuid.uuid4().hex[:12]
                    strategy_description = "LLM-guided baseline journey using the current page state and retrieved JSON experiences."
                    retry_context: dict[str, Any] | None = None
                    retrieved_before_attempt = list(adaptive_engine.retrieved_experiences)
                    failure_reason = "The journey objective was not verified."
                    terminal = False
                    action_error = False
                    unsafe_action = False
                    attempt_status = "failed"
                    attempt_started_at = datetime.now(timezone.utc).isoformat()

                    if attempt_number > 1:
                        retrieved_before_attempt = adaptive_engine.retrieve_context(
                            test_case.test_case_id,
                            test_case.goal,
                            _observation_evidence(observation),
                            failure_category=(final_failure or {}).get("category"),
                        )
                        retry_context = adaptive_engine.build_learning_context(
                            persona.model_dump(mode="json"),
                            test_case.goal,
                            _observation_evidence(observation),
                            (final_failure or {}).get("evidence", {}),
                        )
                        decide_retry = getattr(agent, "decide_retry", None)
                        if decide_retry is None:
                            result["status"] = "incomplete"
                            result["error"] = "The configured agent does not support adaptive retries."
                            break
                        proposal_validation = {
                            "status": "REJECTED",
                            "error": "No retry proposal was validated.",
                        }
                        retry_feedback = None
                        for generation in range(1, 4):
                            retry_context["strategy_validation_feedback"] = retry_feedback
                            candidate = None
                            try:
                                candidate = decide_retry(test_case, persona, retry_context)
                                adaptive_engine.validate_strategy(
                                    candidate,
                                    current_page_state=retry_context.get("browser_state"),
                                    current_evidence=retry_context.get("current_evidence"),
                                )
                                candidate.retrieved_experience_ids = [
                                    item.get("experience_id")
                                    for item in retrieved_before_attempt
                                    if item.get("experience_id")
                                ]
                                proposal = candidate
                                proposal_validation = {"status": "VALIDATED", "error": None}
                                break
                            except (RuntimeError, ValueError, PlaywrightError) as exc:
                                retry_feedback = str(exc)
                                proposal_validation = {
                                    "status": "REJECTED",
                                    "error": retry_feedback,
                                    "generation": generation,
                                }
                                adaptive_engine.record_retry_decision(
                                    {
                                        "attempt_number": attempt_number,
                                        "generation": generation,
                                        "retrieved_experience_ids": [
                                            item.get("experience_id") for item in retrieved_before_attempt
                                        ],
                                        "context": retry_context,
                                        "proposal": candidate.model_dump(mode="json") if candidate else None,
                                        "validation": proposal_validation,
                                        "executed": False,
                                        "timestamp": datetime.now(timezone.utc).isoformat(),
                                    }
                                )
                        if proposal_validation["status"] != "VALIDATED":
                            result["status"] = "incomplete"
                            result["error"] = "The retry strategy could not be validated after three proposals."
                            result["result"] = str(proposal_validation["error"])
                            break

                        assert proposal is not None
                        strategy_id = proposal.strategy_id
                        strategy_description = proposal.strategy_description
                        recommendation = proposal.retry_recommendation.strip().casefold()
                        stop_recommendation = recommendation.startswith(("stop", "do not retry", "no retry"))
                        if stop_recommendation or proposal.failure_category != FailureCategory.AGENT_RELATED:
                            adaptive_engine.record_retry_decision(
                                {
                                    "attempt_number": attempt_number,
                                    "retrieved_experience_ids": [
                                        item.get("experience_id") for item in retrieved_before_attempt
                                    ],
                                    "context": retry_context,
                                    "proposal": proposal.model_dump(mode="json"),
                                    "validation": proposal_validation,
                                    "executed": False,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                }
                            )
                            result["status"] = "incomplete"
                            result["result"] = (
                                proposal.root_cause
                                or "The retry strategy recommended stopping."
                            )
                            break

                    if attempt_number == 1:
                        objective_verified = _is_cart_confirmed(observation, test_case)
                        for step in range(1, step_limit + 1):
                            if objective_verified:
                                break
                            obstacle = _obstacle(observation)
                            if obstacle:
                                failure_reason = obstacle
                                terminal = True
                                attempt_status = "blocked"
                                break
                            result["step_count"] += 1
                            _write_json(run_directory / "result.json", result)
                            decision_started = time.perf_counter()
                            try:
                                decide_with_context = getattr(
                                    agent, "decide_with_learning_context", None
                                )
                                if decide_with_context:
                                    step_context = adaptive_engine.build_learning_context(
                                        persona.model_dump(mode="json"),
                                        test_case.goal,
                                        _observation_evidence(observation),
                                        {
                                            "attempt_number": attempt_number,
                                            "action_history": attempt_actions,
                                            "retrieved_experience_ids": [
                                                item.get("experience_id")
                                                for item in initial_experiences
                                                if item.get("experience_id")
                                            ],
                                        },
                                    )
                                    step_context["retrieved_experiences"] = initial_experiences
                                    action = decide_with_context(
                                        test_case,
                                        persona,
                                        observation,
                                        history,
                                        step_context,
                                    )
                                else:
                                    action = agent.decide(
                                        test_case, persona, observation, history
                                    )
                            except (RuntimeError, ValueError, OpenAIError) as exc:
                                failure_reason = str(exc)
                                action_error = True
                                break
                            entry = {
                                "attempt_number": attempt_number,
                                "step": result["step_count"],
                                "strategy_id": strategy_id,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "decision": action.model_dump(mode="json"),
                                "decision_duration_ms": round(
                                    (time.perf_counter() - decision_started) * 1000
                                ),
                                "before": {
                                    "url": observation.get("url", ""),
                                    "title": observation.get("title", ""),
                                },
                                "observation_before": _observation_evidence(observation),
                            }
                            if action.action == BrowserActionType.FINISH:
                                entry["result"] = "Agent stopped before objective confirmation."
                                attempt_actions.append(entry)
                                history.append(entry)
                                failure_reason = "The agent stopped before the journey objective was verified."
                                break
                            execution_started = time.perf_counter()
                            try:
                                entry["execution"] = browser.execute(action)
                                observation = browser.observe()
                                entry["after"] = {
                                    "url": observation.get("url", ""),
                                    "title": observation.get("title", ""),
                                }
                                entry["observation_after"] = _observation_evidence(observation)
                                objective_verified = _is_cart_confirmed(observation, test_case)
                                entry["objective_verified"] = objective_verified
                            except UnsafeNavigationError as exc:
                                entry["error"] = str(exc)
                                failure_reason = str(exc)
                                unsafe_action = True
                                terminal = True
                                attempt_status = "blocked"
                            except (PlaywrightError, ValueError, RuntimeError) as exc:
                                entry["error"] = str(exc)
                                failure_reason = str(exc)
                                action_error = True
                                try:
                                    observation = browser.observe()
                                    entry["after"] = {
                                        "url": observation.get("url", ""),
                                        "title": observation.get("title", ""),
                                    }
                                    entry["observation_after"] = _observation_evidence(observation)
                                except PlaywrightError:
                                    pass
                            finally:
                                entry["execution_duration_ms"] = round(
                                    (time.perf_counter() - execution_started) * 1000
                                )
                                screenshot = _save_screenshot(browser, result["step_count"])
                                if screenshot:
                                    entry["screenshot"] = screenshot
                                    result["artifacts"]["screenshots"].append(screenshot)
                                attempt_actions.append(entry)
                                history.append(entry)
                                _write_json(
                                    run_directory / "action_history.json",
                                    {"initial_observation": initial_observation, "actions": history},
                                )
                            if objective_verified or terminal or action_error:
                                break
                            if browser.blocked_navigation:
                                failure_reason = browser.blocked_navigation
                                terminal = True
                                attempt_status = "blocked"
                                break
                        else:
                            failure_reason = "The configured agent step limit was reached."
                    else:
                        assert proposal is not None
                        adaptive_engine.record_retry_decision(
                            {
                                "attempt_number": attempt_number,
                                "retrieved_experience_ids": [
                                    item.get("experience_id") for item in retrieved_before_attempt
                                ],
                                "context": retry_context,
                                "proposal": proposal.model_dump(mode="json"),
                                "validation": proposal_validation,
                                "executed": True,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                        )
                        objective_verified = _is_cart_confirmed(observation, test_case)
                        for action in proposal.proposed_actions:
                            if objective_verified:
                                break
                            if action.action == BrowserActionType.FINISH:
                                failure_reason = "The proposed strategy stopped before objective confirmation."
                                break
                            result["step_count"] += 1
                            entry = {
                                "attempt_number": attempt_number,
                                "step": result["step_count"],
                                "strategy_id": strategy_id,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "decision": action.model_dump(mode="json"),
                                "before": {
                                    "url": observation.get("url", ""),
                                    "title": observation.get("title", ""),
                                },
                                "observation_before": _observation_evidence(observation),
                            }
                            execution_started = time.perf_counter()
                            try:
                                entry["execution"] = browser.execute(action)
                                observation = browser.observe()
                                entry["after"] = {
                                    "url": observation.get("url", ""),
                                    "title": observation.get("title", ""),
                                }
                                entry["observation_after"] = _observation_evidence(observation)
                                objective_verified = _is_cart_confirmed(observation, test_case)
                                entry["objective_verified"] = objective_verified
                            except UnsafeNavigationError as exc:
                                entry["error"] = str(exc)
                                failure_reason = str(exc)
                                unsafe_action = True
                                terminal = True
                                attempt_status = "blocked"
                            except (PlaywrightError, ValueError, RuntimeError) as exc:
                                entry["error"] = str(exc)
                                failure_reason = str(exc)
                                action_error = True
                                try:
                                    observation = browser.observe()
                                    entry["after"] = {
                                        "url": observation.get("url", ""),
                                        "title": observation.get("title", ""),
                                    }
                                    entry["observation_after"] = _observation_evidence(observation)
                                except PlaywrightError:
                                    pass
                            finally:
                                entry["execution_duration_ms"] = round(
                                    (time.perf_counter() - execution_started) * 1000
                                )
                                screenshot = _save_screenshot(browser, result["step_count"])
                                if screenshot:
                                    entry["screenshot"] = screenshot
                                    result["artifacts"]["screenshots"].append(screenshot)
                                attempt_actions.append(entry)
                                history.append(entry)
                                _write_json(
                                    run_directory / "action_history.json",
                                    {"initial_observation": initial_observation, "actions": history},
                                )
                            if objective_verified or terminal or action_error:
                                break
                            if browser.blocked_navigation:
                                failure_reason = browser.blocked_navigation
                                terminal = True
                                attempt_status = "blocked"
                                break

                    objective_verified = _is_cart_confirmed(observation, test_case)
                    if objective_verified:
                        attempt_status = "completed"
                        result.update(
                            status="completed",
                            completed=True,
                            result="The requested product was confirmed in the cart.",
                        )
                    elif attempt_status != "blocked":
                        if attempt_number == 1 and failure_reason.startswith("The configured agent step limit"):
                            attempt_status = "max_steps"
                        else:
                            attempt_status = "incomplete"

                    diagnostic: dict[str, Any] = {}
                    if not objective_verified:
                        try:
                            diagnostic = browser.capture_diagnostics(
                                attempt_number, attempt_actions
                            )
                        except (PlaywrightError, OSError, AttributeError) as exc:
                            logger.warning("Could not capture attempt diagnostics: %s", exc)
                            diagnostic = {
                                "current_url": observation.get("url", ""),
                                "current_title": observation.get("title", ""),
                            }
                    evidence = {
                        **diagnostic,
                        "error": failure_reason if action_error else "",
                        "failure_reason": failure_reason,
                        "action_error": action_error,
                        "unsafe_action": unsafe_action,
                        "journey_incomplete": not objective_verified and not terminal,
                        "network_events": getattr(browser, "network_events", []),
                        "network_errors": getattr(browser, "network_errors", []),
                        "console_errors": getattr(browser, "console_errors", []),
                        "page_errors": [
                            event.get("message", "")
                            for event in getattr(browser, "browser_events", [])
                            if event.get("type") == "pageerror"
                        ],
                        "website_hostname": test_case.allowed_domain,
                    }
                    if objective_verified:
                        category = FailureCategory.AGENT_RELATED
                        root_cause = "Journey objective verified from the resulting page state."
                        learning_eligible = True
                    else:
                        category, root_cause, learning_eligible = adaptive_engine.triage_failure(evidence)
                    if terminal and not unsafe_action:
                        category = FailureCategory.UNKNOWN
                        learning_eligible = False
                    if terminal and result["status"] == "blocked":
                        attempt_status = "blocked"
                    references = [
                        value
                        for key, value in diagnostic.items()
                        if key.endswith("_reference") and isinstance(value, str)
                    ]
                    references.extend(
                        item.get("screenshot") for item in attempt_actions if item.get("screenshot")
                    )
                    references.append("action_history.json")
                    attempt_record = {
                        "attempt_number": attempt_number,
                        "started_at": attempt_started_at,
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "strategy_id": strategy_id,
                        "strategy_description": strategy_description,
                        "persona": persona.model_dump(mode="json"),
                        "journey_objective": test_case.goal,
                        "test_case_id": test_case.test_case_id,
                        "application_context": {
                            "application_id": test_case.test_case_id,
                            "website": str(test_case.website),
                            "allowed_domain": test_case.allowed_domain,
                        },
                        "page_context": _observation_evidence(observation),
                        "observed_state": _observation_evidence(observation),
                        "actions": [item["decision"] for item in attempt_actions],
                        "action_history": attempt_actions,
                        "execution_result": {
                            "status": attempt_status,
                            "expected": test_case.completion_criteria,
                            "actual": _observation_evidence(observation),
                            "objective_verified": objective_verified,
                            "failure_reason": failure_reason if not objective_verified else None,
                        },
                        "objective_verified": objective_verified,
                        "evidence": evidence,
                        "evidence_references": list(dict.fromkeys(references)),
                        "previous_failure_analysis": (final_failure or {}).get("root_cause", "Initial execution attempt."),
                        "failure_category": category.value,
                        "root_cause": root_cause,
                        "learning_eligible": learning_eligible,
                        "outcome": "SUCCESS" if objective_verified else "FAILURE",
                        "confidence": proposal.confidence if proposal else (0.9 if objective_verified else 0.8 if attempt_actions else 0.65),
                        "validation_status": "VALIDATED" if attempt_actions or objective_verified else "UNVERIFIED",
                        "retrieved_experience_ids": [item.get("experience_id") for item in retrieved_before_attempt],
                        "llm_proposal": proposal.model_dump(mode="json") if proposal else None,
                    }
                    attempt_record = adaptive_engine.record_attempt(attempt_record)
                    attempt_directory = run_directory / f"attempt_{attempt_number}"
                    attempt_directory.mkdir(parents=True, exist_ok=True)
                    _write_json(attempt_directory / "attempt.json", attempt_record)
                    _write_json(run_directory / "action_history.json", {
                        "initial_observation": initial_observation,
                        "actions": history,
                        "attempts": adaptive_engine.attempts,
                    })
                    if objective_verified:
                        break

                    final_failure = {
                        "category": category.value,
                        "root_cause": root_cause,
                        "reason": failure_reason,
                        "evidence": evidence,
                    }
                    if category != FailureCategory.AGENT_RELATED or not learning_eligible or terminal:
                        result["status"] = attempt_status
                        result["result"] = failure_reason
                        break
                    if not adaptive_engine.can_retry(attempt_number):
                        result["status"] = attempt_status
                        result["result"] = failure_reason
                        break
                    observation = observation or previous_observation
                    previous_observation = observation

            if result["status"] == "running":
                result["status"] = "failed"
                result["result"] = "The run ended without verifying the journey objective."

    except (ValueError, RuntimeError, FileNotFoundError, PlaywrightError, OpenAIError) as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        logger.error("Test run %s failed (%s)", run_id, type(exc).__name__)
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = "Unexpected test-run failure."
        logger.exception("Unexpected test run failure (%s)", run_id)
    finally:
        if not adaptive_engine.attempts and result["status"] in {"failed", "blocked"}:
            failure_reason = str(result.get("error") or result.get("result") or "Run setup failed.")
            evidence = {
                "error": failure_reason,
                "failure_reason": failure_reason,
                "current_observation": initial_observation or {},
            }
            category, root_cause, _ = adaptive_engine.triage_failure(evidence)
            try:
                attempt_record = {
                    "attempt_number": 1,
                    "strategy_id": "STRATEGY_SETUP_FAILURE_" + uuid.uuid4().hex[:12],
                    "strategy_description": "The journey could not start or observe the initial page.",
                    "persona": persona.model_dump(mode="json") if persona else {},
                    "journey_objective": test_case.goal,
                    "test_case_id": test_case.test_case_id,
                    "application_context": {
                        "application_id": test_case.test_case_id,
                        "website": str(test_case.website),
                        "allowed_domain": test_case.allowed_domain,
                    },
                    "page_context": initial_observation or {},
                    "observed_state": initial_observation or {},
                    "actions": [],
                    "execution_result": {"status": result["status"], "failure_reason": failure_reason},
                    "objective_verified": False,
                    "evidence": evidence,
                    "evidence_references": [],
                    "previous_failure_analysis": "Initial execution attempt.",
                    "failure_category": category.value,
                    "root_cause": root_cause,
                    "learning_eligible": False,
                    "outcome": "FAILURE",
                    "confidence": 0.0,
                    "validation_status": "UNVERIFIED",
                    "retrieved_experience_ids": [],
                }
                attempt_record = adaptive_engine.record_attempt(attempt_record)
                attempt_directory = run_directory / "attempt_1"
                attempt_directory.mkdir(parents=True, exist_ok=True)
                _write_json(attempt_directory / "attempt.json", attempt_record)
            except Exception:
                logger.exception("Could not persist setup failure for run %s", run_id)
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        result["duration_seconds"] = round(
            (datetime.now(timezone.utc) - started_at).total_seconds(), 2
        )
        action_history = {
            "initial_observation": initial_observation,
            "actions": history,
            "attempts": adaptive_engine.attempts,
        }
        _write_json(run_directory / "action_history.json", action_history)
        video_files = sorted((run_directory / "video").glob("*.webm"))
        if video_files:
            result["artifacts"]["video"] = str(video_files[0].relative_to(run_directory))
        if not settings.azure_ai_configured:
            analysis_record = {
                "status": "unavailable",
                "error": "Azure AI Foundry is not configured; UX analysis was not run.",
            }
        else:
            try:
                if persona is None:
                    raise ValueError(f"Unknown persona id '{test_case.persona_id}'.")
                analysis = UXAnalyzer().analyze(test_case, persona, result, action_history)
                analysis_record = analysis.model_dump(mode="json")
            except Exception as exc:
                logger.exception("UX analysis failed for run %s", run_id)
                analysis_record = {"status": "failed", "error": str(exc)}
        result["analysis"] = analysis_record
        _write_json(run_directory / "ux_analysis.json", analysis_record)
        _write_json(run_directory / "result.json", result)
        try:
            adaptive_engine.finish_run(result["status"], result)
        except Exception as exc:
            logger.exception("Could not persist adaptive run history for %s", run_id)
            if not result.get("error"):
                result["error"] = "Adaptive run history could not be persisted."
                result["persistence_error"] = str(exc)
                _write_json(run_directory / "result.json", result)

    return result
