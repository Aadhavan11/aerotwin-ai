"""HTTP for the agent workflow (CONTRACTS.md section 4): POST /agents/run, GET /agents/runs/{id}."""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.agents.runner import run_workflow
from app.agents.runs import RUNS
from app.contracts.models import AgentRun

router = APIRouter(tags=["agents"])


class RunScope(BaseModel):
    tails: Optional[list[str]] = None
    horizon_h: Optional[float] = None


class RunRequest(BaseModel):
    goal: str
    scope: RunScope = RunScope()
    mode: Literal["llm", "scripted"] = "scripted"


@router.post("/agents/run", response_model=AgentRun)
def post_agents_run(req: RunRequest) -> AgentRun:
    """Starts a run and returns immediately with status "running"; poll GET /agents/runs/{id} every second."""
    return run_workflow(req.goal, req.scope.model_dump(exclude_none=True), req.mode, background=True)


@router.get("/agents/runs/{run_id}", response_model=AgentRun)
def get_agents_run(run_id: str):
    run = RUNS.get(run_id)
    if run is None:
        return JSONResponse(status_code=404, content={"error": "unknown run", "detail": run_id})
    return run
