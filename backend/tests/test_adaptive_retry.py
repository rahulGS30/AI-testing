import json

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from app.config import PROJECT_ROOT
from app.learning.adaptive_retry_engine import (
    AdaptiveRetryEngine,
    StrategyValidationError,
)
from app.learning.learning_manager import (
    JSONLearningManager,
    LearningStoreCorruptionError,
)
from app.models.adaptive_retry import FailureCategory, StrategyProposal
from app.models.browser_action import BrowserAction
from app.models.test_case import load_test_case
from app.orchestrator import test_runner


def _experience(**overrides):
    record = {
        "experience_id": "EXP-1",
        "run_id": "run-1",
        "persona": {"name": "First-Time User"},
        "journey_objective": "Add jacket to cart",
        "attempt_number": 1,
        "strategy_id": "STRATEGY-1",
        "strategy_description": "Use the visible add button",
        "page_context": {"url": "https://shop.example/product", "title": "Product"},
        "application_context": {
            "application_id": "TC-1",
            "website": "https://shop.example",
        },
        "observed_state": {"title": "Product"},
        "evidence_references": ["attempt_1/dom_snapshot.json"],
        "previous_failure_analysis": "Initial attempt",
        "action_details": {"action": "click", "target": "#add"},
        "execution_result": {"objective_verified": False},
        "failure_category": "AGENT_RELATED",
        "root_cause": "Incorrect locator",
        "learning_eligible": True,
        "outcome": "FAILURE",
        "confidence": 0.9,
        "validation_status": "VALIDATED",
        "timestamp": "2026-01-01T00:00:00Z",
    }
    record.update(overrides)
    return record


def _proposal(**overrides):
    values = {
        "failure_assessment": "The initial locator timed out.",
        "failure_category": FailureCategory.AGENT_RELATED,
        "root_cause": "The accessible name did not identify the control.",
        "learning_eligibility": True,
        "retrieved_experience_ids": [],
        "strategy_id": "STRATEGY-RETRY-1",
        "strategy_description": "Use the observed product button selector.",
        "reasoning": "The DOM shows a unique button with the expected label.",
        "supporting_evidence": ["The current observation includes the button."],
        "differences_from_previous_strategy": "Use the observed DOM selector instead of the guessed role name.",
        "proposed_actions": [
            BrowserAction(
                action="click",
                target="#add-to-cart",
                reason="Click the observed add-to-cart control.",
                expected_result="The cart confirmation appears.",
                confidence=0.9,
            )
        ],
        "expected_result": "The jacket is added to the cart.",
        "verification_conditions": ["The page confirms the jacket was added."],
        "confidence": 0.9,
        "retry_recommendation": "continue",
    }
    values.update(overrides)
    return StrategyProposal(**values)


def test_corrupt_experience_store_is_preserved(tmp_path):
    manager = JSONLearningManager(tmp_path / "learning")
    manager.experiences_path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(LearningStoreCorruptionError):
        manager.store_experience(_experience())

    assert manager.experiences_path.read_text(encoding="utf-8") == "{not-json"


def test_experience_deduplication_updates_search_index(tmp_path):
    manager = JSONLearningManager(tmp_path / "learning")
    manager.store_experience(_experience())
    manager.store_experience(_experience(root_cause="Refined locator analysis"))

    records = manager.get_similar_experiences(
        {"application_id": "TC-1", "journey_objective": "Add jacket to cart"},
        {"url": "https://shop.example/product", "title": "Product"},
    )

    assert len(manager.get_experiences()) == 1
    assert records[0]["root_cause"] == "Refined locator analysis"
    index = json.loads(manager.index_path.read_text(encoding="utf-8"))
    assert len(index["entries"]) == 1


def test_retry_cap_and_failure_triage():
    engine = AdaptiveRetryEngine(learning_manager=JSONLearningManager(), max_retries=99)

    assert engine.max_attempts == 4
    assert engine.triage_failure(
        {
            "error": "Locator timed out",
            "action_error": True,
            "network_errors": [{"failure": "net::ERR_ABORTED"}],
        }
    )[0] == FailureCategory.AGENT_RELATED
    assert engine.triage_failure(
        {"network_events": [{"type": "response", "status": 503}]}
    )[0] == FailureCategory.SERVER_RELATED


def test_strategy_rejects_repeated_failed_actions_without_justification():
    engine = AdaptiveRetryEngine(learning_manager=JSONLearningManager())
    repeated = _proposal(
        proposed_actions=[
            BrowserAction(
                action="click",
                target="#add",
                reason="Click",
                expected_result="Added",
                confidence=0.8,
            )
        ],
        supporting_evidence=[],
        differences_from_previous_strategy="",
    )
    engine.attempts = [
        {
            "strategy_id": "STRATEGY-OLD",
            "outcome": "FAILURE",
            "actions": [{"action": "click", "target": "#add", "value": None}],
        }
    ]

    with pytest.raises(StrategyValidationError):
        engine.validate_strategy(repeated)

    engine.attempts[0]["observed_state"] = {"url": "https://shop.example/product"}
    engine.attempts[0]["evidence"] = {"dom_snapshot_excerpt": "Add"}
    extended_repeat = _proposal(
        proposed_actions=[
            repeated.proposed_actions[0],
            BrowserAction(
                action="wait",
                reason="Wait for a change.",
                expected_result="The page updates.",
                confidence=0.8,
            ),
        ]
    )
    with pytest.raises(StrategyValidationError):
        engine.validate_strategy(
            extended_repeat,
            current_page_state={"url": "https://shop.example/product"},
            current_evidence={"dom_snapshot_excerpt": "Add"},
        )


def test_stop_proposal_may_have_no_actions():
    stopped = _proposal(proposed_actions=[], retry_recommendation="stop")
    assert stopped.proposed_actions == []


def test_runner_executes_full_retry_strategy_and_verifies_goal(tmp_path, monkeypatch):
    case = load_test_case(PROJECT_ROOT / "test_cases", "TC-003")
    initial = {
        "url": str(case.website),
        "title": "Product",
        "visible_text": "Argentina jacket",
        "interactive_elements": [{"tag": "button", "label": "Add", "visible": True}],
    }
    completed = {
        **initial,
        "url": "https://www.adidas.com/cart",
        "title": "Cart",
        "visible_text": "Argentina jacket added to cart",
    }

    class FakeController:
        def __init__(self, *args, **kwargs):
            self.blocked_navigation = None
            self.calls = 0
            self.network_events = []
            self.network_errors = []
            self.console_errors = []
            self.browser_events = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def open(self):
            return None

        def observe(self):
            return completed if self.calls > 2 else initial

        def save_screenshot(self, step):
            return f"screenshots/step_{step:03d}.png"

        def execute(self, action):
            self.calls += 1
            if self.calls == 1:
                raise PlaywrightTimeoutError("Locator timed out")
            return {"url": completed["url"], "title": completed["title"]}

        def capture_diagnostics(self, attempt_number, action_history=None):
            return {
                "screenshot_reference": f"attempt_{attempt_number}/screenshot.png",
                "dom_snapshot_reference": f"attempt_{attempt_number}/dom_snapshot.json",
                "accessibility_snapshot_reference": f"attempt_{attempt_number}/accessibility_snapshot.json",
                "browser_events_reference": f"attempt_{attempt_number}/browser_events.json",
                "action_history_reference": f"attempt_{attempt_number}/action_history.json",
                "accessibility_snapshot": "button Add to cart",
                "network_events": [],
                "network_errors": [],
                "console_errors": [],
                "browser_events": [],
            }

    class FakeAgent:
        def __init__(self):
            self.retry_calls = 0
            self.initial_learning_ids = []
            self.retry_context = None

        def decide(self, *args):
            return BrowserAction(
                action="click",
                target="button[aria-label='Add']",
                reason="Try the accessible name.",
                expected_result="The item is added.",
                confidence=0.8,
            )

        def decide_with_learning_context(self, *args):
            self.initial_learning_ids = args[4]["retrieved_experience_ids"]
            return self.decide(*args[:4])

        def decide_retry(self, *args):
            self.retry_calls += 1
            self.retry_context = args[2]
            if self.retry_calls == 1:
                return _proposal(
                    proposed_actions=[
                        BrowserAction(
                            action="click",
                            target="button[aria-label='Add']",
                            reason="Repeat the failed locator.",
                            expected_result="The cart confirms the addition.",
                            confidence=0.8,
                        )
                    ],
                    supporting_evidence=[],
                    differences_from_previous_strategy="",
                )
            return _proposal(
                proposed_actions=[
                    BrowserAction(
                        action="click",
                        target="#add-to-cart",
                        reason="Use the unique observed DOM selector.",
                        expected_result="The cart confirms the addition.",
                        confidence=0.9,
                    ),
                    BrowserAction(
                        action="wait",
                        reason="Allow the confirmation to render.",
                        expected_result="The confirmation remains visible.",
                        confidence=0.9,
                    ),
                ]
            )

    learning_manager = JSONLearningManager(tmp_path / "learning")
    learning_manager.store_experience(
        _experience(
            experience_id="EXP-RELEVANT",
            run_id="previous-run",
            application_context={
                "application_id": case.test_case_id,
                "website": str(case.website),
            },
            journey_objective=case.goal,
            page_context={"url": str(case.website), "title": "Product"},
        )
    )
    monkeypatch.setattr(test_runner, "PlaywrightController", FakeController)
    monkeypatch.setattr(
        test_runner,
        "AdaptiveRetryEngine",
        lambda max_retries=3: AdaptiveRetryEngine(learning_manager, max_retries),
    )
    monkeypatch.setattr(
        test_runner,
        "settings",
        type(
            "TestSettings",
            (),
            {
                "artifacts_dir": tmp_path,
                "browser_timeout": 1000,
                "max_agent_steps": 10,
                "max_retries": 1,
                "azure_ai_configured": False,
            },
        )(),
    )
    agent = FakeAgent()

    result = test_runner.run_test_case(
        case,
        agent=agent,
        artifacts_directory=tmp_path / "artifacts",
    )

    assert result["completed"] is True
    assert result["status"] == "completed"
    assert agent.retry_calls == 2
    assert agent.initial_learning_ids == ["EXP-RELEVANT"]
    assert len(agent.retry_context["attempt_history"]) == 1
    assert agent.retry_context["retrieved_experience_ids"] == ["EXP-RELEVANT"]
    assert agent.retry_context["strategy_validation_feedback"]
    run = json.loads((learning_manager.runs_directory / f"run_{result['run_id']}.json").read_text(encoding="utf-8"))
    assert [attempt["outcome"] for attempt in run["attempts"]] == ["FAILURE", "SUCCESS"]
    assert run["retry_decisions"][0]["validation"]["status"] == "REJECTED"
    assert run["retry_decisions"][1]["executed"] is True
    assert len(run["attempts"][1]["actions"]) == 2
    assert run["attempts"][1]["objective_verified"] is True
