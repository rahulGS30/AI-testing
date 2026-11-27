from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AnalysisEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: int = Field(ge=0)
    stage: Literal["initial", "before", "after"]
    source: Literal["page_text", "control", "action", "run_result"]
    quote: str = Field(min_length=1, max_length=500)


class UXFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    observation: str = Field(min_length=1, max_length=500)
    persona_impact: str = Field(min_length=1, max_length=500)
    recommendation: str = Field(min_length=1, max_length=500)
    priority: Literal["high", "medium", "low"]
    evidence: list[AnalysisEvidence] = Field(min_length=1, max_length=3)


class UXAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["completed"] = "completed"
    summary: str = Field(min_length=1, max_length=1000)
    strengths: list[UXFinding] = Field(max_length=10)
    improvements: list[UXFinding] = Field(max_length=10)
    not_assessed: list[str] = Field(max_length=10)
    evidence_scope: str = Field(min_length=1, max_length=500)
