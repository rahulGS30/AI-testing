from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BrowserActionType(StrEnum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    FILL = "fill"
    SCROLL = "scroll"
    SELECT = "select"
    PRESS_KEY = "press_key"
    WAIT = "wait"
    FINISH = "finish"


class BrowserAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: BrowserActionType
    target: str | None = None
    value: str | None = None
    reason: str
    expected_result: str
    confidence: float = Field(ge=0, le=1)

    @field_validator("target", "value", "reason", "expected_result")
    @classmethod
    def bound_string_fields(cls, value):
        if value is not None and len(value) > 2000:
            raise ValueError("Browser action fields must not exceed 2000 characters.")
        return value

    @model_validator(mode="after")
    def validate_action_fields(self) -> "BrowserAction":
        if self.action in {
            BrowserActionType.CLICK,
            BrowserActionType.FILL,
            BrowserActionType.TYPE,
            BrowserActionType.SELECT,
            BrowserActionType.PRESS_KEY,
        } and not self.target:
            raise ValueError(f"{self.action.value} requires a target")
        if self.action in {
            BrowserActionType.FILL,
            BrowserActionType.TYPE,
            BrowserActionType.SELECT,
        } and self.value is None:
            raise ValueError(f"{self.action.value} requires a value")
        if self.action == BrowserActionType.NAVIGATE and not self.value:
            raise ValueError("navigate requires a URL in value")
        return self
