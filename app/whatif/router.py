"""HTTP for what-if (CONTRACTS.md section 4): POST /plan/replan and POST /whatif/compare. Owner: Pardhu."""
from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.contracts.models import Comparison, ReplanResult
from app.contracts.requests import CompareRequest, ReplanRequest
from app.solver import service
from app.solver.tasks import derive_tasks
from app.whatif import compare as cmp
from app.whatif.events import EventError
from app.whatif.metrics import plan_block
from app.whatif.runner import replan_and_store

router = APIRouter(tags=["whatif"])


@router.post("/plan/replan", response_model=ReplanResult)
def post_plan_replan(req: ReplanRequest, already_applied: bool = Query(False)):
    """Apply the event to the current snapshot, re-plan with CP-SAT (stable versus the current plan), return the diff.
    The new plan becomes the current plan. A malformed event is a 422 and changes nothing."""
    try:
        return replan_and_store(req.event, already_applied)
    except EventError as exc:
        return JSONResponse(status_code=422, content={"error": "invalid event", "detail": str(exc)})


@router.post("/whatif/compare", response_model=Comparison)
def post_whatif_compare(req: CompareRequest) -> Comparison:
    """One metrics row per named scenario, all from the same snapshot. Each scenario is solved independently."""
    state, source = service.get_state()
    with service.SOLVE_LOCK:
        base, _ = service.ensure_plans(state, source)
        scenarios = req.scenarios or cmp.default_scenarios(state, base, derive_tasks(state))
        plans = cmp.build_scenario_plans(state, scenarios, None, base_plan=base)
    return cmp.compare(state, plans)


@router.get("/metrics/plan")
def get_metrics_plan() -> dict:
    """The plan block of /metrics (extra endpoint; Shamit's /metrics can call app.whatif.metrics.plan_block() instead)."""
    return plan_block()
