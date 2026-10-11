"""HTTP for what-if (CONTRACTS.md section 4): POST /plan/replan and POST /whatif/compare. Owner: Pardhu."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.contracts.models import Comparison, ObjectiveWeights, ReplanResult, Scenario, ScenarioEvent
from app.solver import service
from app.solver.tasks import derive_tasks
from app.whatif import compare as cmp
from app.whatif.events import EventError
from app.whatif.metrics import plan_block
from app.whatif.runner import replan_and_store

router = APIRouter(tags=["whatif"])


class ReplanRequest(BaseModel):
    event: ScenarioEvent
    already_applied: bool = False     # True if the platform already wrote the event into the snapshot
    weights: Optional[ObjectiveWeights] = None


class CompareRequest(BaseModel):
    scenarios: list[Scenario] = []    # empty -> the five default what-if questions from the concept doc
    weights: Optional[ObjectiveWeights] = None


@router.post("/plan/replan", response_model=ReplanResult)
def post_plan_replan(req: ReplanRequest):
    """Apply the event to the current snapshot, re-plan with CP-SAT (stable versus the current plan), return the diff.
    The new plan becomes the current plan. A malformed event is a 422 and changes nothing."""
    try:
        return replan_and_store(req.event, req.already_applied, req.weights)
    except EventError as exc:
        return JSONResponse(status_code=422, content={"error": "invalid event", "detail": str(exc)})


@router.post("/whatif/compare", response_model=Comparison)
def post_whatif_compare(req: CompareRequest) -> Comparison:
    """One metrics row per named scenario, all from the same snapshot. Each scenario is solved independently."""
    state, source = service.get_state()
    with service.SOLVE_LOCK:
        base, _ = service.ensure_plans(state, source)
        scenarios = req.scenarios or cmp.default_scenarios(state, base, derive_tasks(state))
        plans = cmp.build_scenario_plans(state, scenarios, req.weights, base_plan=base)
    return cmp.compare(state, plans)


@router.get("/metrics/plan")
def get_metrics_plan() -> dict:
    """The plan block of /metrics (extra endpoint; Shamit's /metrics can call app.whatif.metrics.plan_block() instead)."""
    return plan_block()
