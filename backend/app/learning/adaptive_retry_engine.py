from __future__ import annotations

import copy
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from app.learning.learning_manager import JSONLearningManager
from app.models.adaptive_retry import FailureCategory, StrategyProposal
from app.models.browser_action import BrowserAction

logger = logging.getLogger(__name__)


class StrategyValidationError(ValueError):
    pass


class AdaptiveRetryEngine:
    MAX_RETRIES = 3

    def __init__(
        self,
        learning_manager: JSONLearningManager | None = None,
        max_retries: int = 3,
        positive_reward: float = 1.0,
        negative_reward: float = -1.0,
    ) -> None:
        self.learning_manager = learning_manager or JSONLearningManager()
        self.max_retries = min(self.MAX_RETRIES, max(0, int(max_retries)))
        self.max_attempts = self.max_retries + 1
        self.positive_reward = positive_reward
        self.negative_reward = negative_reward
        self.run_id: str | None = None
        self.attempts: list[dict[str, Any]] = []
        self.retrieved_experiences: list[dict[str, Any]] = []
        self.retrieval_history: list[dict[str, Any]] = []
        self.retry_decisions: list[dict[str, Any]] = []

    def start_run(self, run_id: str) -> None:
        self.run_id = run_id
        self.attempts = []
        self.retrieved_experiences = []
        self.retrieval_history = []
        self.retry_decisions = []
        logger.info("Adaptive journey started run=%s max_attempts=%d", run_id, self.max_attempts)

    @staticmethod
    def triage_failure(evidence: dict[str, Any]) -> tuple[FailureCategory, str, bool]:
        error = str(evidence.get("error", "")).casefold()
        events = evidence.get("network_events", [])
        network_errors = evidence.get("network_errors", [])
        site_host = str(evidence.get("website_hostname", "")).casefold().strip()

        def is_application_request(item: dict[str, Any]) -> bool:
            if not site_host:
                return True
            host = (urlsplit(str(item.get("url", ""))).hostname or "").casefold()
            return host == site_host or host.endswith("." + site_host)

        statuses = [
            item.get("status")
            for item in events
            if isinstance(item, dict)
            and isinstance(item.get("status"), int)
            and is_application_request(item)
        ]
        server_status = evidence.get("http_status") or next(
            (status for status in statuses if status >= 500), None
        )
        if isinstance(server_status, int) and server_status >= 500:
            return FailureCategory.SERVER_RELATED, f"HTTP {server_status} server failure", False
        if isinstance(server_status, int) and 400 <= server_status < 500:
            return FailureCategory.APPLICATION_RELATED, f"HTTP {server_status} application response", False
        network_error_tokens = (
            "net::err_name_not_resolved", "net::err_connection",
            "dns_probe", "connection reset", "connection refused",
            "network is unreachable", "temporary failure in name resolution",
        )
        relevant_network_errors = [
            item for item in network_errors
            if isinstance(item, dict)
            and is_application_request(item)
            and any(token in str(item.get("failure", "")).casefold() for token in network_error_tokens)
        ]
        if any(token in error for token in network_error_tokens) or relevant_network_errors:
            return FailureCategory.NETWORK_RELATED, "Browser request failed due to network connectivity", False
        if evidence.get("browser_crash") or any(
            token in error for token in ("browser has been closed", "browser process", "browser crash", "azure ai request", "llm request")
        ):
            return FailureCategory.INFRASTRUCTURE_RELATED, "Browser or infrastructure failure", False
        console_errors = evidence.get("console_errors", [])
        page_errors = evidence.get("page_errors", [])
        if evidence.get("application_crash") or page_errors:
            return FailureCategory.APPLICATION_RELATED, "Application runtime error observed in the browser", False
        if any(isinstance(item, str) and ("uncaught" in item.casefold() or "exception" in item.casefold()) for item in console_errors):
            return FailureCategory.APPLICATION_RELATED, "Application console exception observed", False
        if evidence.get("unsafe_action"):
            return FailureCategory.AGENT_RELATED, "Proposed action was blocked by the browser safety policy", False
        if evidence.get("action_error") or evidence.get("journey_incomplete"):
            return FailureCategory.AGENT_RELATED, str(
                evidence.get("failure_reason") or "Execution strategy did not verify the journey objective"
            ), True
        return FailureCategory.UNKNOWN, "Insufficient evidence to establish the failure cause", False

    def validate_strategy(
        self,
        proposal: StrategyProposal,
        previous_attempts: list[dict[str, Any]] | None = None,
        current_page_state: dict[str, Any] | None = None,
        current_evidence: dict[str, Any] | None = None,
    ) -> None:
        attempted = previous_attempts if previous_attempts is not None else self.attempts
        prior_ids = {item.get("strategy_id") for item in attempted if item.get("strategy_id")}
        if proposal.strategy_id in prior_ids:
            raise StrategyValidationError("Every attempt must use a unique strategy_id.")
        proposed = [self._action_signature(action) for action in proposal.proposed_actions]
        for previous in attempted:
            if previous.get("outcome") != "FAILURE":
                continue
            previous_actions = previous.get("actions", [])
            previous_signatures = [
                self._action_signature(action)
                for action in previous_actions
                if isinstance(action, dict) and action.get("action")
            ]
            repeated_actions = set(proposed).intersection(previous_signatures)
            if repeated_actions:
                previous_state = previous.get("observed_state", {})
                page_changed = (
                    bool(current_page_state)
                    and current_page_state != previous_state
                )
                evidence_changed = (
                    bool(current_evidence)
                    and current_evidence != previous.get("evidence", {})
                )
                if (
                    not proposal.supporting_evidence
                    or not proposal.differences_from_previous_strategy.strip()
                    or not (page_changed or evidence_changed)
                ):
                    raise StrategyValidationError(
                        "The proposal repeats a failed action without new evidence or changed page state and justification."
                    )
        if not proposal.strategy_description.strip() or not proposal.reasoning.strip():
            raise StrategyValidationError("A strategy description and reasoning are required.")
        if not proposal.failure_assessment.strip() or not proposal.root_cause.strip():
            raise StrategyValidationError("A failure assessment and root cause are required.")
        if not proposal.expected_result.strip() or not any(
            condition.strip() for condition in proposal.verification_conditions
        ):
            raise StrategyValidationError("Expected result and verification conditions are required.")
        stop_recommended = proposal.retry_recommendation.strip().casefold().startswith(
            ("stop", "do not retry", "no retry")
        )
        if not stop_recommended and not proposal.proposed_actions:
            raise StrategyValidationError("A retry strategy must include at least one action.")
        if not stop_recommended and (
            not proposal.supporting_evidence
            or not proposal.differences_from_previous_strategy.strip()
        ):
            raise StrategyValidationError(
                "A retry strategy must cite evidence and explain its difference from prior attempts."
            )
        if proposal.failure_category in {
            FailureCategory.SERVER_RELATED,
            FailureCategory.NETWORK_RELATED,
            FailureCategory.INFRASTRUCTURE_RELATED,
            FailureCategory.APPLICATION_RELATED,
            FailureCategory.UNKNOWN,
        } and proposal.learning_eligibility:
            raise StrategyValidationError(
                "External or uncertain failures cannot be learning-eligible."
            )
        if any(not isinstance(action, BrowserAction) for action in proposal.proposed_actions):
            raise StrategyValidationError("Only schema-validated browser actions are allowed.")
        logger.info("Validated retry strategy run=%s strategy=%s", self.run_id, proposal.strategy_id)

    @staticmethod
    def _action_signature(action: BrowserAction | dict[str, Any]) -> str:
        payload = action.model_dump(mode="json") if isinstance(action, BrowserAction) else action
        return json.dumps(
            {key: payload.get(key) for key in ("action", "target", "value")},
            sort_keys=True,
        )

    def retrieve_context(
        self,
        test_case_id: str,
        journey_objective: str,
        page_context: dict[str, Any],
        action: dict[str, Any] | None = None,
        failure_category: str | None = None,
    ) -> list[dict[str, Any]]:
        retrieved = self.learning_manager.get_similar_experiences(
            {
                "application_id": test_case_id,
                "journey_objective": journey_objective,
                "failure_category": failure_category,
            },
            page_context,
            action,
            limit=10,
        )
        retrieved = [item for item in retrieved if item.get("run_id") != self.run_id]
        known_ids = {
            item.get("experience_id") for item in self.retrieved_experiences
        }
        self.retrieved_experiences.extend(
            item for item in retrieved if item.get("experience_id") not in known_ids
        )
        self.retrieval_history.append(
            {
                "query": {
                    "application_id": test_case_id,
                    "journey_objective": journey_objective,
                    "failure_category": failure_category,
                    "page_context": page_context,
                    "action": action,
                },
                "experience_ids": [item.get("experience_id") for item in retrieved],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        logger.info(
            "Queried JSON experience index run=%s result_count=%d experience_ids=%s",
            self.run_id,
            len(retrieved),
            [item.get("experience_id") for item in retrieved],
        )
        return retrieved

    def build_learning_context(
        self,
        persona: dict[str, Any],
        journey_objective: str,
        browser_state: dict[str, Any],
        evidence: dict[str, Any],
        attempted_strategies: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "persona": persona,
            "journey_objective": journey_objective,
            "run_id": self.run_id,
            "attempt_number": len(self.attempts) + 1,
            "maximum_attempts": self.max_attempts,
            "attempt_history": copy.deepcopy(self.attempts),
            "browser_state": copy.deepcopy(browser_state),
            "retrieved_experiences": copy.deepcopy(self.retrieved_experiences),
            "retrieved_experience_ids": [item.get("experience_id") for item in self.retrieved_experiences],
            "retrieval_history": copy.deepcopy(self.retrieval_history),
            "attempted_strategies": copy.deepcopy(attempted_strategies or [
                {"strategy_id": item.get("strategy_id"), "strategy_description": item.get("strategy_description")}
                for item in self.attempts
            ]),
            "strategies_to_avoid": copy.deepcopy([
                {
                    "strategy_id": item.get("strategy_id"),
                    "actions": item.get("actions", []),
                    "reason": item.get("root_cause"),
                }
                for item in self.attempts
                if item.get("outcome") == "FAILURE"
            ]),
            "current_evidence": copy.deepcopy(evidence),
        }

    def record_attempt(self, attempt: dict[str, Any]) -> dict[str, Any]:
        attempt = dict(attempt)
        attempt.setdefault("attempt_number", len(self.attempts) + 1)
        attempt.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        category = FailureCategory(attempt.get("failure_category", FailureCategory.UNKNOWN))
        eligible = bool(attempt.get("learning_eligible")) and category == FailureCategory.AGENT_RELATED
        verified = bool(attempt.get("objective_verified"))
        validation_status = attempt.get("validation_status", "UNVERIFIED")
        reward = 0.0
        if eligible and validation_status == "VALIDATED":
            if attempt.get("outcome") == "SUCCESS" and verified:
                reward = self.positive_reward
            elif attempt.get("outcome") == "FAILURE" and attempt.get("confidence", 0) >= 0.7:
                reward = self.negative_reward
        attempt["learning_eligible"] = eligible
        attempt["reward"] = reward
        self.attempts.append(attempt)
        if not self.run_id:
            raise RuntimeError("start_run must be called before recording an attempt")
        experience_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.run_id}:{attempt['attempt_number']}"))
        persona = attempt.get("persona", {})
        experience = {
            "experience_id": experience_id,
            "run_id": self.run_id,
            "persona": persona,
            "journey_objective": attempt.get("journey_objective", ""),
            "attempt_number": attempt["attempt_number"],
            "strategy_id": attempt.get("strategy_id", ""),
            "strategy_description": attempt.get("strategy_description", ""),
            "page_context": attempt.get("page_context", {}),
            "application_context": attempt.get("application_context", {}),
            "observed_state": attempt.get("observed_state", {}),
            "evidence_references": attempt.get("evidence_references", []),
            "previous_failure_analysis": attempt.get("previous_failure_analysis", ""),
            "action_details": attempt.get("actions", []),
            "execution_result": attempt.get("execution_result", {}),
            "failure_category": category.value,
            "root_cause": attempt.get("root_cause", ""),
            "learning_eligible": eligible,
            "outcome": attempt.get("outcome", "FAILURE"),
            "reward": reward,
            "confidence": attempt.get("confidence", 0.0),
            "validation_status": validation_status,
            "timestamp": attempt["timestamp"],
        }
        self.learning_manager.store_experience(experience)
        self._persist_run(attempt.get("final_status"))
        logger.info(
            "Persisted attempt run=%s attempt=%s outcome=%s category=%s eligible=%s reward=%s",
            self.run_id, attempt["attempt_number"], attempt.get("outcome"),
            category.value, eligible, reward,
        )
        return attempt

    def record_retry_decision(self, decision: dict[str, Any]) -> None:
        self.retry_decisions.append(copy.deepcopy(decision))
        self._persist_run()

    def _persist_run(self, final_status: str | None = None) -> None:
        assert self.run_id is not None
        self.learning_manager.save_run(
            self.run_id,
            {
                "run_id": self.run_id,
                "attempts": self.attempts,
                "retry_decisions": self.retry_decisions,
                "retrieved_experiences": self.retrieved_experiences,
                "retrieval_history": self.retrieval_history,
                "final_status": final_status,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def finish_run(self, final_status: str, final_result: dict[str, Any]) -> None:
        self._persist_run(final_status)
        self.learning_manager.save_run(
            self.run_id or "unknown",
            {
                "run_id": self.run_id or "unknown",
                "attempts": self.attempts,
                "retry_decisions": self.retry_decisions,
                "retrieved_experiences": self.retrieved_experiences,
                "retrieval_history": self.retrieval_history,
                "final_status": final_status,
                "final_execution_result": final_result,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    def can_retry(self, attempt_number: int) -> bool:
        return attempt_number < self.max_attempts
