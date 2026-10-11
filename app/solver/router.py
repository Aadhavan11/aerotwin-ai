"""HTTP for the scheduler (CONTRACTS.md section 4): GET /plan and POST /plan/solve. Owner: Pardhu."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.contracts.models import Comparison, ObjectiveWeights, Plan, SolveMode
from app.solver import service
from app.solver.engine import solve
from app.solver.store import STORE
from app.solver.tasks import derive_tasks

router = APIRouter(tags=["plan"])


class SolveRequest(BaseModel):
    mode: SolveMode = "cpsat"
    weights: Optional[ObjectiveWeights] = None
    time_limit_s: float = Field(5.0, gt=0, le=60)


class PlanBundle(BaseModel):
    current: Plan
    baseline: Plan
    compare: Comparison


@router.get("/plan", response_model=PlanBundle)
def get_plan() -> PlanBundle:
    """The current optimized plan, the static baseline, and a comparison row each for baseline / greedy / optimized."""
    from app.whatif.compare import compare
    state, source = service.get_state()
    current, baseline = service.ensure_plans(state, source)
    greedy = STORE.greedy_cache.get(current.id)
    if greedy is None:
        greedy = STORE.greedy_cache[current.id] = solve(state, derive_tasks(state), None, "greedy")
    return PlanBundle(current=current, baseline=baseline,
                      compare=compare(state, {"baseline": baseline, "greedy": greedy, "optimized": current}))


@router.post("/plan/solve", response_model=Plan)
def post_plan_solve(req: SolveRequest) -> Plan:
    """Solve now with the given mode and weights. static_baseline becomes the baseline; cpsat/greedy become the current plan."""
    state, source = service.get_state()
    return service.solve_and_save(state, source, req.mode, req.weights, req.time_limit_s)
