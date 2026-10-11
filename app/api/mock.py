"""MOCK routers: one per module group, same paths and models as the real modules (CONTRACTS.md section 4).
They are used when MOCK=1, MOCK_<GROUP>=1, or when the real module (app/<group>/router.py) is missing or fails to import.
Responses carry the header `X-Data-Source: mock`.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Response

from .. import fixtures
from ..contracts.models import *  # noqa: F401,F403
from ..contracts.requests import AgentRunRequest, CompareRequest, DecisionRequest, ReplanRequest, ResetRequest, SolveRequest, StepRequest
from .mockstate import STATE


def _mark(resp: Response) -> None:
    resp.headers["X-Data-Source"] = "mock"


# ---------------------------------------------------------------- twin (Aashirth)
twin = APIRouter(tags=["twin (mock)"])


@twin.get("/aircraft/{tail}", response_model=TwinView)
def get_aircraft(tail: str, response: Response):
    _mark(response)
    t = STATE.twin(tail)
    if t is None:
        raise HTTPException(404, f"unknown tail {tail}")
    return t


@twin.get("/aircraft/{tail}/telemetry", response_model=list[TelemetryRow])
def get_telemetry(tail: str, response: Response, component: Optional[str] = None, last: int = Query(60, ge=1, le=500)):
    _mark(response)
    t = STATE.twin(tail)
    if t is None:
        raise HTTPException(404, f"unknown tail {tail}")
    comp = next((c for c in t.components if c.component_id == (component or c.component_id) and c.sensors_last), None)
    if comp is None:
        return []
    rows = []
    n = min(last, len(comp.history))
    for h in comp.history[-n:]:
        f = (h.cycle - comp.history[0].cycle) / max(1, comp.history[-1].cycle - comp.history[0].cycle)
        vals = {k: round(v * (1 - 0.01 * (1 - f)) if k in ("T24", "T30", "T50", "Nc") else v, 3) for k, v in comp.sensors_last.items()}
        rows.append(TelemetryRow(tail=tail, component_id=comp.component_id, ts=h.ts, cycle=h.cycle, values=vals))
    return rows


# ---------------------------------------------------------------- solver (Pardhu)
solver = APIRouter(tags=["solver (mock)"])


@solver.get("/plan")
def get_plan(response: Response):
    _mark(response)
    cur, base = STATE.plan_current, STATE.plan_baseline
    cmp = Comparison(rows=[{"name": "optimized", "feasible": cur.feasible, "metrics": cur.metrics.model_dump(mode="json")},
                           {"name": "static_baseline", "feasible": base.feasible, "metrics": base.metrics.model_dump(mode="json")}])
    return {"current": cur, "baseline": base, "compare": cmp}


@solver.post("/plan/solve", response_model=Plan)
def solve_plan(req: SolveRequest, response: Response):
    _mark(response)
    if req.mode == "static_baseline":
        return STATE.plan_baseline
    p = STATE.plan_current.model_copy(deep=True)
    p.mode = req.mode
    return p


# ---------------------------------------------------------------- what-if (Pardhu)
whatif = APIRouter(tags=["whatif (mock)"])


@whatif.post("/plan/replan", response_model=ReplanResult)
def replan(req: ReplanRequest, response: Response):
    _mark(response)
    return STATE.replan(req.event)


@whatif.post("/whatif/compare", response_model=Comparison)
def compare(req: CompareRequest, response: Response):
    _mark(response)
    return STATE.compare(req.scenarios)


# ---------------------------------------------------------------- simulator (Aadhavan)
sim = APIRouter(tags=["sim (mock)"])


@sim.post("/sim/step")
def sim_step(req: StepRequest, response: Response):
    _mark(response)
    with STATE.lock:
        STATE.now = STATE.now + timedelta(hours=req.hours)
        return {"sim_now": STATE.now, "ingested": int(req.hours * 12)}


@sim.post("/scenario/inject")
def scenario_inject(ev: ScenarioEvent, response: Response):
    _mark(response)
    return STATE.inject(ev)


@sim.post("/scenario/reset")
def scenario_reset(req: ResetRequest, response: Response):
    _mark(response)
    STATE.reset()
    return {"ok": True, "scenario": req.name}


# ---------------------------------------------------------------- agents (Pardhu; LLM mode Aashirth)
agents = APIRouter(tags=["agents (mock)"])


@agents.post("/agents/run", response_model=AgentRun)
def agents_run(req: AgentRunRequest, response: Response):
    _mark(response)
    return STATE.start_run(req.goal, req.mode)


@agents.get("/agents/runs/{run_id}", response_model=AgentRun)
def agents_get(run_id: str, response: Response):
    _mark(response)
    r = STATE.get_run(run_id)
    if r is None:
        raise HTTPException(404, f"unknown run {run_id}")
    return r


# ---------------------------------------------------------------- copilot + work orders (Aashirth)
copilot = APIRouter(tags=["copilot (mock)"])


@copilot.post("/copilot/chat", response_model=ChatResponse)
def copilot_chat(req: ChatRequest, response: Response):
    _mark(response)
    return STATE.chat(req.message)


@copilot.get("/workorders", response_model=list[WorkOrder])
def list_workorders(response: Response):
    _mark(response)
    return STATE.workorders


@copilot.post("/workorders/{wid}/decision", response_model=WorkOrder)
def decide(wid: str, req: DecisionRequest, response: Response):
    _mark(response)
    w = STATE.decide(wid, req.decision, req.user, req.note)
    if w is None:
        raise HTTPException(404, f"unknown work order {wid}")
    return w


@copilot.get("/audit", response_model=list[AuditEntry])
def list_audit(response: Response):
    _mark(response)
    return list(reversed(STATE.audit))


ROUTERS = {"twin": twin, "solver": solver, "whatif": whatif, "sim": sim, "agents": agents, "copilot": copilot}
