"""HTTP for the scheduler (CONTRACTS.md section 4): GET /plan and POST /plan/solve. Owner: Pardhu."""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.contracts.models import Comparison, Plan
from app.contracts.requests import SolveRequest
from app.solver import service
from app.solver.engine import solve
from app.solver.exposure import exposure
from app.solver.store import STORE
from app.solver.tasks import derive_tasks

router = APIRouter(tags=["plan"])


class PlanBundle(BaseModel):
    current: Plan
    baseline: Plan
    compare: Comparison
    # risk view that cost metrics can hide: hours to fix, flights flown with the fault open, slack before the hard deadline
    exposure: dict[str, list[dict]] = {}


@router.get("/plan", response_model=PlanBundle)
def get_plan() -> PlanBundle:
    """The current optimized plan, the static baseline, and a comparison row each for baseline / greedy / optimized."""
    from app.whatif.compare import compare
    state, source = service.get_state()
    current, baseline = service.ensure_plans(state, source)
    greedy = STORE.greedy_cache.get(current.id)
    if greedy is None:
        greedy = STORE.greedy_cache[current.id] = solve(state, derive_tasks(state), None, "greedy")
    tasks = derive_tasks(state)
    return PlanBundle(current=current, baseline=baseline,
                      compare=compare(state, {"baseline": baseline, "greedy": greedy, "optimized": current}),
                      exposure={"baseline": exposure(state, tasks, baseline), "optimized": exposure(state, tasks, current)})


@router.post("/plan/solve", response_model=Plan)
def post_plan_solve(req: SolveRequest, time_limit_s: float = Query(5.0, gt=0, le=60)) -> Plan:
    """Solve now with the given mode and weights. static_baseline becomes the baseline; cpsat/greedy become the current plan."""
    state, source = service.get_state()
    return service.solve_and_save(state, source, req.mode, req.weights, time_limit_s)
