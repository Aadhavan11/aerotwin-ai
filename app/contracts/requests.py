"""HTTP request bodies (the response models are in models.py). Real module routers MUST use these too, so the API stays identical in MOCK and real mode."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from .models import ObjectiveWeights, ScreenContext, Scenario, ScenarioEvent, SolveMode


class SolveRequest(BaseModel):
    mode: SolveMode = "cpsat"
    weights: Optional[ObjectiveWeights] = None


class ReplanRequest(BaseModel):
    event: ScenarioEvent


class CompareRequest(BaseModel):
    scenarios: list[Scenario]


class StepRequest(BaseModel):
    hours: float = Field(default=6.0, gt=0, le=240)


class ResetRequest(BaseModel):
    name: str = "golden"


class AgentRunRequest(BaseModel):
    goal: str = "Plan maintenance for the aircraft that need attention"
    scope: dict = {}
    mode: Literal["llm", "scripted"] = "scripted"
    screen: Optional[ScreenContext] = None      # what the person is looking at; reaches the LLM agents through scope["screen"]


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    user: str = "unknown"
    note: str = ""
