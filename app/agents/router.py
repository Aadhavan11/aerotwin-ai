"""HTTP for the agent workflow (CONTRACTS.md section 4): POST /agents/run, GET /agents/runs/{id}."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.agents.runner import run_workflow
from app.agents.runs import RUNS
from app.contracts.models import AgentRun
from app.contracts.requests import AgentRunRequest

router = APIRouter(tags=["agents"])


@router.post("/agents/run", response_model=AgentRun)
def post_agents_run(req: AgentRunRequest) -> AgentRun:
    """Starts a run and returns immediately with status "running"; poll GET /agents/runs/{id} every second."""
    scope = {**req.scope, "screen": req.screen.model_dump(exclude={"text"})} if req.screen else req.scope  # the agents see the same screen as the person
    return run_workflow(req.goal, scope, req.mode, background=True)


@router.get("/agents/runs/{run_id}", response_model=AgentRun)
def get_agents_run(run_id: str):
    run = RUNS.get(run_id)
    if run is None:
        return JSONResponse(status_code=404, content={"error": "unknown run", "detail": run_id})
    return run
