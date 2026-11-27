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
from app.browser.playwright_controller import PlaywrightController, UnsafeNavigationError
from app.config import PROJECT_ROOT, settings
from app.models.browser_action import BrowserAction, BrowserActionType
from app.models.test_case import TestCase
from app.personas.persona_manager import get_persona

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


def run_test_case(
    test_case: TestCase,
    *,
    headless: bool = True,
    agent: BrowserAgent | None = None,
    artifacts_directory: Path | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    is_precreated_run = run_id is not None
    run_id = run_id or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    root = artifacts_directory or settings.artifacts_dir / test_case.test_case_id
    run_directory = root / run_id
    run_directory.mkdir(parents=True, exist_ok=is_precreated_run)
    history: list[dict[str, Any]] = []
    started_at = datetime.now(timezone.utc)
    result: dict[str, Any] = {
        "run_id": run_id,
        "test_case_id": test_case.test_case_id,
        "name": test_case.name,
        "website": str(test_case.website),
        "persona_id": test_case.persona_id,
        "goal": test_case.goal,
        "status": "failed",
        "completed": False,
        "step_count": 0,
        "started_at": started_at.isoformat(),
        "finished_at": None,
        "result": None,
        "error": None,
        "artifacts": {
            "directory": str(run_directory.relative_to(PROJECT_ROOT))
            if run_directory.is_relative_to(PROJECT_ROOT)
            else str(run_directory),
            "screenshots": [],
            "video": None,
            "action_history": "action_history.json",
        },
    }
    result["status"] = "running"
    _write_json(run_directory / "result.json", result)

    try:
        persona = get_persona(test_case.persona_id)
        if persona is None:
            raise ValueError(f"Unknown persona id '{test_case.persona_id}'.")
        if agent is None:
            agent = BrowserAgent()

        with PlaywrightController(
            test_case,
            run_directory,
            timeout_ms=settings.browser_timeout,
            headless=headless,
        ) as browser:
            browser.open()
            if browser.blocked_navigation:
                result["status"] = "blocked"
                result["result"] = browser.blocked_navigation
            observation = browser.observe()
            screenshot = browser.save_screenshot(0)
            result["artifacts"]["screenshots"].append(screenshot)

            step_limit = min(test_case.max_steps, settings.max_agent_steps)
            for step in range(
                1,
                (step_limit if result["status"] != "blocked" else 0) + 1,
            ):
                result["step_count"] = step
                obstacle = _obstacle(observation)
                if obstacle:
                    result["status"] = "blocked"
                    result["result"] = obstacle
                    break

                if _is_cart_confirmed(observation, test_case):
                    result["status"] = "completed"
                    result["completed"] = True
                    result["result"] = "The requested product was confirmed in the cart."
                    break

                decision_started = time.perf_counter()
                action = agent.decide(test_case, persona, observation, history)
                entry: dict[str, Any] = {
                    "step": step,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "decision": action.model_dump(mode="json"),
                    "decision_duration_ms": round(
                        (time.perf_counter() - decision_started) * 1000
                    ),
                    "before": {
                        "url": observation["url"],
                        "title": observation["title"],
                    },
                }

                if action.action == BrowserActionType.FINISH:
                    entry["result"] = "Agent stopped before cart confirmation."
                    history.append(entry)
                    result["status"] = "incomplete"
                    result["result"] = (
                        "The agent stopped, but the page did not confirm the item in the cart."
                    )
                    break

                try:
                    execution_started = time.perf_counter()
                    entry["execution"] = browser.execute(action)
                    entry["execution_duration_ms"] = round(
                        (time.perf_counter() - execution_started) * 1000
                    )
                    observation = browser.observe()
                    entry["after"] = {
                        "url": observation["url"],
                        "title": observation["title"],
                    }
                    entry["screenshot"] = browser.save_screenshot(step)
                    result["artifacts"]["screenshots"].append(entry["screenshot"])
                except UnsafeNavigationError as exc:
                    entry["error"] = str(exc)
                    entry["result"] = "Unsafe action blocked."
                    entry["screenshot"] = browser.save_screenshot(step)
                    result["artifacts"]["screenshots"].append(entry["screenshot"])
                    history.append(entry)
                    result["status"] = "blocked"
                    result["result"] = str(exc)
                    break
                except (PlaywrightError, ValueError) as exc:
                    entry["error"] = str(exc)
                    observation = browser.observe()
                    entry["after"] = {
                        "url": observation["url"],
                        "title": observation["title"],
                    }
                    entry["screenshot"] = browser.save_screenshot(step)
                    result["artifacts"]["screenshots"].append(entry["screenshot"])
                history.append(entry)

                if browser.blocked_navigation:
                    result["status"] = "blocked"
                    result["result"] = browser.blocked_navigation
                    break
                obstacle = _obstacle(observation)
                if obstacle:
                    result["status"] = "blocked"
                    result["result"] = obstacle
                    break
                if _is_cart_confirmed(observation, test_case):
                    result["status"] = "completed"
                    result["completed"] = True
                    result["result"] = (
                        "The requested product was confirmed in the cart."
                    )
                    break
            else:
                if result["status"] != "blocked":
                    result["status"] = "max_steps"
                    result["result"] = "The configured agent step limit was reached."

    except (ValueError, RuntimeError, FileNotFoundError, PlaywrightError, OpenAIError) as exc:
        result["status"] = "failed"
        result["error"] = str(exc)
        logger.error("Test run %s failed (%s)", run_id, type(exc).__name__)
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = "Unexpected test-run failure."
        logger.exception("Unexpected test run failure (%s)", run_id)
    finally:
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
        result["duration_seconds"] = round(
            (datetime.now(timezone.utc) - started_at).total_seconds(), 2
        )
        _write_json(run_directory / "action_history.json", {"actions": history})
        video_files = sorted((run_directory / "video").glob("*.webm"))
        if video_files:
            result["artifacts"]["video"] = str(video_files[0].relative_to(run_directory))
        _write_json(run_directory / "result.json", result)

    return result
