from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.browser_action import BrowserAction


class FailureCategory(StrEnum):
    AGENT_RELATED = "AGENT_RELATED"
    APPLICATION_RELATED = "APPLICATION_RELATED"
    SERVER_RELATED = "SERVER_RELATED"
    NETWORK_RELATED = "NETWORK_RELATED"
    INFRASTRUCTURE_RELATED = "INFRASTRUCTURE_RELATED"
    UNKNOWN = "UNKNOWN"


class StrategyProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failure_assessment: str
    failure_category: FailureCategory
    root_cause: str
    learning_eligibility: bool
    retrieved_experience_ids: list[str] = Field(default_factory=list)
    strategy_id: str
    strategy_description: str
    reasoning: str
    supporting_evidence: list[str] = Field(default_factory=list)
    differences_from_previous_strategy: str
    proposed_actions: list[BrowserAction] = Field(default_factory=list)
    expected_result: str
    verification_conditions: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    retry_recommendation: str

    @model_validator(mode="after")
    def validate_strategy(self) -> "StrategyProposal":
        if not self.strategy_id.strip():
            raise ValueError("strategy_id is required")
        recommendation = self.retry_recommendation.strip().casefold()
        stop_recommended = recommendation.startswith(("stop", "do not retry", "no retry"))
        if not self.proposed_actions and not stop_recommended:
            raise ValueError("at least one proposed action is required unless the retry is stopped")
        return self
