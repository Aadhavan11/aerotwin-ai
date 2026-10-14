"""Monte-Carlo risk evaluation API (SIMULATED; advisory). Mounted by app/insights/router.py when this module exists.

    POST /ops/montecarlo        {plan_id?: str, n?: int, seed?: int, params?: {...MCConfig overrides}}
    GET  /ops/montecarlo/last   the most recent result (404 if none yet)

JSON keys are documented in docs/MONTECARLO.md.
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.whatif import montecarlo as mc

router = APIRouter(tags=["ops"])


class MonteCarloRequest(BaseModel):
    plan_id: Optional[str] = None
    n: int = Field(default=mc.DEFAULT_N, ge=1, le=mc.MAX_N)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    params: Optional[dict] = None


def _err(status: int, error: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "detail": detail})


@router.post("/ops/montecarlo")
def post_montecarlo(req: MonteCarloRequest):
    from app.solver.service import SOLVE_LOCK, ensure_plans, get_state
    from app.solver.store import STORE
    from app.solver.tasks import derive_tasks

    try:
        cfg = mc.MCConfig.from_overrides(req.params)
    except ValueError as exc:
        return _err(422, "invalid_params", str(exc))
    t0 = time.perf_counter()
    state, source = get_state()
    with SOLVE_LOCK:
        if req.plan_id:
            plan = next((p for p in reversed(STORE.history) if p.id == req.plan_id), None)
            if plan is None:
                return _err(404, "not_found", f"no plan {req.plan_id!r}")
            cur, base = STORE.current, STORE.baseline
        else:
            cur, base = ensure_plans(state, source)
            plan = cur
    tasks = derive_tasks(state)
    res = dict(mc.run(state, plan, tasks, cfg, req.n, req.seed))
    other = base if plan is not base else cur
    if cur is not None and base is not None and other is not None:
        optimised, baseline = (cur, base)
        res["comparison"] = mc.compare_runs(state, optimised, baseline, tasks, cfg, req.n, req.seed)
    else:
        res["comparison"] = None
    res["simulated"] = True
    res["provenance"] = {"source": "app.whatif.montecarlo", "state_source": source, "as_of": state.now.isoformat(), "seed": req.seed, "n": res["n"],
                         "model": "seeded vectorised Monte-Carlo over part delay, technician no-show, job overrun, RUL failure and weather knock-on",
                         "config": mc.asdict(cfg), "prior_constants": {"cancellation_cost_usd": cfg.cancellation_cost_usd, "aog_cost_usd_h": cfg.aog_cost_usd_h},
                         "synthetic": True}
    res["elapsed_s"] = round(time.perf_counter() - t0, 3)
    mc.set_last(res)
    return res


@router.get("/ops/montecarlo/last")
def get_last():
    last = mc.get_last()
    if last is None:
        return _err(404, "not_found", "no Monte-Carlo run yet; POST /ops/montecarlo first")
    return last
