"""The agent tool registry: the 16 deterministic tools of CONTRACTS.md section 6.

Every tool returns ``{"data": <json>, "evidence": <EvidenceRef>, "missing_info": [str]}``. The LLM (later) only chooses tools and
explains; every number comes from here. Tools read the same state as the planner (``app.solver.service.get_state``: the twin snapshot,
or the golden stand-in until it exists, plus any disruption already applied through POST /plan/replan).

``call_tool(name, args)`` never raises for expected failures: it returns ``{"data": None, "error": "..."}`` so a run can report it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from app.agents import kb, workorders
from app.contracts.models import (
    EvidenceRef, FleetState, ObjectiveWeights, Plan, ScenarioEvent, Task, TwinEvent,
)
from app.solver import service
from app.solver.candidates import SolveContext
from app.solver.common import aware
from app.solver.engine import solve
from app.solver.store import STORE
from app.solver.tasks import derive_tasks_ex
from app.solver.windows import ground_windows
from app.whatif.compare import compare
from app.whatif.events import EventError
from app.whatif.runner import replan_and_store
from app.whatif.simulate import affected_legs, simulate


class ToolError(ValueError):
    """Expected failure of a tool call (unknown plan id, bad argument...)."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    agent: str
    description: str
    input_schema: dict
    fn: Callable[..., dict]


def _out(data: Any, evidence: EvidenceRef, missing: Optional[list[str]] = None) -> dict:
    return {"data": to_jsonable_python(data), "evidence": evidence.model_dump(mode="json"), "missing_info": list(missing or [])}


def _state() -> tuple[FleetState, str]:
    return service.get_state()


def _plan(plan_id: Optional[str]) -> Plan:
    if plan_id in (None, "", "current", "optimized"):
        state, source = _state()
        return service.ensure_plans(state, source)[0]
    if plan_id == "baseline":
        state, source = _state()
        return service.ensure_plans(state, source)[1]
    for p in reversed(STORE.history):
        if p.id == plan_id:
            return p
    raise ToolError(f"unknown plan id {plan_id!r}")


def _tasks(state: FleetState) -> list[Task]:
    return derive_tasks_ex(state).tasks


def _time(value: Any) -> datetime:
    try:
        return aware(value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError as exc:
        raise ToolError(f"bad time {value!r}") from exc


def _schema(props: dict, required: Optional[list[str]] = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


# ---------------------------------------------------------------- Health agent
def list_at_risk(horizon_cycles: int = 60, tails: Optional[list[str]] = None) -> dict:
    """Components whose conservative RUL (p10) is below the horizon, or that are in alert, riskiest first."""
    state, _ = _state()
    rows, missing = [], []
    for c in state.components:
        if tails and c.tail not in tails:
            continue
        p10 = c.rul_cycles.p10 if c.rul_cycles else None
        if (p10 is not None and p10 < horizon_cycles) or c.status == "alert":
            rows.append({"tail": c.tail, "component_id": c.component_id, "health": c.health, "rul_p10": p10,
                         "is_anomaly": c.anomaly_score >= 0.5, "status": c.status})
            if p10 is None:
                missing.append(f"{c.component_id}: status {c.status} but no RUL estimate")
    rows.sort(key=lambda r: (r["rul_p10"] if r["rul_p10"] is not None else -1.0, r["health"], r["component_id"]))
    return _out(rows, EvidenceRef(kind="tool_call", ref="list_at_risk", note=f"RUL p10 < {horizon_cycles} cycles or alert"), missing)


def get_component_health(component_id: str) -> dict:
    state, _ = _state()
    comp = next((c for c in state.components if c.component_id == component_id), None)
    if comp is None:
        raise ToolError(f"unknown component {component_id!r}")
    return _out(comp, EvidenceRef(kind="health", ref=component_id, note=f"health {comp.health}, status {comp.status}"))


def get_sensor_trend(component_id: str, last: int = 30) -> dict:
    """Recent telemetry from the twin. If the twin is not available the tool says so instead of inventing a trend."""
    try:
        from app.twin.state import get_twin  # type: ignore[import-not-found]
    except ImportError:
        return _out([], EvidenceRef(kind="tool_call", ref="get_sensor_trend", note="twin not available"),
                    [f"sensor trend for {component_id}: the digital twin is not available yet"])
    tail = component_id.split("/", 1)[0]
    comp = next((c for c in get_twin(tail).components if c.component_id == component_id), None)
    if comp is None:
        raise ToolError(f"unknown component {component_id!r}")
    rows = [h.model_dump(mode="json") for h in comp.history[-last:]]
    return _out({"history": rows, "sensors_last": comp.sensors_last},
                EvidenceRef(kind="health", ref=component_id, note=f"last {len(rows)} history points"))


def get_maintenance_history(tail: str) -> dict:
    state, _ = _state()
    events = [TwinEvent(ts=c.last_maintenance, kind="maintenance", summary=f"{c.component_id} last maintained", ref=c.component_id)
              for c in state.components if c.tail == tail and c.last_maintenance is not None]
    events.sort(key=lambda e: e.ts)
    missing = [] if events else [f"no maintenance history recorded for {tail}"]
    return _out(events, EvidenceRef(kind="tool_call", ref="get_maintenance_history", note=tail), missing)


# ---------------------------------------------------------------- Scheduling agent
def find_ground_windows(tail: str, horizon_h: float = 120.0) -> dict:
    state, _ = _state()
    end = aware(state.now).timestamp() + horizon_h * 3600
    wins = [w for w in ground_windows(state.legs, state.now, state.aircraft) if w.tail == tail and aware(w.start).timestamp() < end]
    return _out([{**w.model_dump(mode="json"), "hours": round(w.hours, 3)} for w in wins],
                EvidenceRef(kind="tool_call", ref="find_ground_windows", note=f"{tail}, {len(wins)} windows"),
                [] if wins else [f"{tail}: no ground windows in the known rotation"])


def derive_tasks() -> dict:
    state, _ = _state()
    res = derive_tasks_ex(state)
    return _out(res.tasks, EvidenceRef(kind="tool_call", ref="derive_tasks", note=f"{len(res.tasks)} tasks from RUL p10 and routine checks"),
                res.notes)


def solve_schedule(mode: str = "cpsat", weights: Optional[dict] = None) -> dict:
    if mode not in ("cpsat", "greedy", "static_baseline"):
        raise ToolError(f"unknown mode {mode!r}")
    state, source = _state()
    w = ObjectiveWeights(**weights) if weights else None
    plan = service.solve_and_save(state, source, mode, w)
    return _out(plan, EvidenceRef(kind="plan", ref=plan.id, note=f"{plan.mode}, feasible={plan.feasible}"),
                list(plan.infeasible_reasons))


def compare_plans(names: Optional[list[str]] = None) -> dict:
    state, source = _state()
    current, baseline = service.ensure_plans(state, source)
    available = {"baseline": baseline, "optimized": current}
    names = names or ["baseline", "greedy", "optimized"]
    plans: dict[str, Plan] = {}
    for n in names:
        if n == "greedy":
            plans[n] = STORE.greedy_cache.get(current.id) or STORE.greedy_cache.setdefault(
                current.id, solve(state, _tasks(state), None, "greedy"))
        elif n in available:
            plans[n] = available[n]
        else:
            raise ToolError(f"unknown plan name {n!r}; use baseline, greedy or optimized")
    return _out(compare(state, plans), EvidenceRef(kind="plan", ref=current.id, note="comparison: " + ", ".join(names)))


# ---------------------------------------------------------------- Logistics agent
def check_parts(station: str, part: str) -> dict:
    state, _ = _state()
    st = next((s for s in state.stations if s.iata == station), None)
    if st is None:
        raise ToolError(f"unknown station {station!r}")
    ps = next((p for p in st.parts if p.part == part), None)
    if ps is None:
        return _out({"on_hand": 0, "eta_hours": None, "ship_from": None, "ship_cost": None},
                    EvidenceRef(kind="tool_call", ref="check_parts", note=f"{station} {part}"),
                    [f"{station} has no stock record for {part}"])
    return _out({"on_hand": ps.qty if ps.eta_hours <= 0 else 0, "in_transit": ps.qty if ps.eta_hours > 0 else 0, "eta_hours": ps.eta_hours,
                 "ship_from": ps.ship_from, "ship_hours": ps.ship_hours, "ship_cost": ps.ship_cost_usd},
                EvidenceRef(kind="tool_call", ref="check_parts", note=f"{station} {part}"))


def match_technicians(station: str, skill: str, start: str, end: str) -> dict:
    state, _ = _state()
    st = next((s for s in state.stations if s.iata == station), None)
    if st is None:
        raise ToolError(f"unknown station {station!r}")
    s, e = _time(start), _time(end)
    cur = STORE.current
    busy = {a.technician_id for a in (cur.assignments if cur else []) if aware(a.start) < e and s < aware(a.end)}
    techs = [{**t.model_dump(mode="json"), "busy_in_current_plan": t.id in busy} for t in st.technicians
             if skill in t.skills and t.available and aware(t.shift_start) <= s and e <= aware(t.shift_end)]
    return _out(techs, EvidenceRef(kind="tool_call", ref="match_technicians", note=f"{station} {skill}"),
                [] if techs else [f"no available {skill} technician at {station} for that window"])


def shortage_report() -> dict:
    """What is missing or has to be moved for the current plan: tasks the plan could not place, and parts that must be shipped."""
    state, source = _state()
    plan = service.ensure_plans(state, source)[0]
    tasks = _tasks(state)
    ctx = SolveContext(state, tasks)
    kind = {"hangar_closed": "hangar", "no_skill": "technician", "tech_unavailable": "technician", "shift": "technician",
            "no_part": "part", "part_late": "part", "too_short": "window"}
    rows = []
    scheduled = {a.task_id for a in plan.assignments}
    for t in tasks:
        if t.id not in scheduled:
            blockers = ctx.blockers(t)
            top = next((k for k, _ in blockers.most_common() if k in kind), None)
            rows.append({"task_id": t.id, "missing": kind.get(top, "capacity"), "detail": ctx.diagnose(t)})
    for a in plan.assignments:
        t = next((x for x in tasks if x.id == a.task_id), None)
        if a.part_source and t and t.required_part:
            ps = next((p for s in state.stations if s.iata == a.station for p in s.parts if p.part == t.required_part), None)
            rows.append({"task_id": a.task_id, "missing": "part",
                         "detail": f"{t.required_part} not on hand at {a.station}; shipped from {a.part_source}"
                                   + (f" (+${ps.ship_cost_usd:,.0f}, {ps.ship_hours:g} h)" if ps else "")})
        if a.extension_h > 0:
            rows.append({"task_id": a.task_id, "missing": "window",
                         "detail": f"ground window too short; the job overruns it by {a.extension_h:g} h and delays the next departure"})
    return _out(rows, EvidenceRef(kind="plan", ref=plan.id, note=f"{len(rows)} shortage(s)"))


# ---------------------------------------------------------------- Operations agent
def simulate_plan(plan_id: Optional[str] = None, runs: int = 1) -> dict:
    plan = _plan(plan_id)
    state, _ = _state()
    m = simulate(plan, state, runs=runs, seed=0, tasks=_tasks(state))
    return _out(m, EvidenceRef(kind="plan", ref=plan.id, note="analytic metrics" if runs <= 1 else f"{runs} simulated runs"))


def flights_affected(plan_id: Optional[str] = None) -> dict:
    plan = _plan(plan_id)
    state, _ = _state()
    legs = affected_legs(plan, state)
    return _out(legs, EvidenceRef(kind="plan", ref=plan.id, note=f"{len(legs)} flight(s) delayed by an extension"))


def replan_with_event(event: dict) -> dict:
    try:
        ev = ScenarioEvent(**event)
        res = replan_and_store(ev)
    except (ValidationError, TypeError) as exc:
        raise ToolError(f"bad event: {exc}") from exc
    except EventError as exc:
        raise ToolError(f"event rejected: {exc}") from exc
    return _out(res, EvidenceRef(kind="event", ref=ev.id or ev.type, note=f"replan in {res.solve_seconds} s, feasible={res.after.feasible}"),
                list(res.after.infeasible_reasons))


# ---------------------------------------------------------------- Coordinator agent
def draft_work_orders(plan_id: Optional[str] = None) -> dict:
    plan = _plan(plan_id)
    state, _ = _state()
    orders = workorders.draft(plan, state, _tasks(state))
    return _out(orders, EvidenceRef(kind="plan", ref=plan.id, note=f"{len(orders)} work order(s), pending approval"))


def search_kb(query: str) -> dict:
    hits = kb.search(query)
    return _out(hits, EvidenceRef(kind="kb", ref=hits[0]["doc_id"] if hits else "kb", note=f"query: {query}"),
                [] if hits else [f"no knowledge-base entry matches {query!r}"])


# ---------------------------------------------------------------- registry
TOOLS: dict[str, ToolSpec] = {t.name: t for t in [
    ToolSpec("list_at_risk", "health", "Components with a low conservative RUL (p10) or in alert, riskiest first.",
             _schema({"horizon_cycles": {"type": "integer"}, "tails": {"type": "array", "items": {"type": "string"}}}), list_at_risk),
    ToolSpec("get_component_health", "health", "Health, anomaly score, RUL quantiles and data quality of one component.",
             _schema({"component_id": {"type": "string"}}, ["component_id"]), get_component_health),
    ToolSpec("get_sensor_trend", "health", "Recent sensor history of one component (needs the digital twin).",
             _schema({"component_id": {"type": "string"}, "last": {"type": "integer"}}, ["component_id"]), get_sensor_trend),
    ToolSpec("get_maintenance_history", "health", "Last maintenance of each component of an aircraft.",
             _schema({"tail": {"type": "string"}}, ["tail"]), get_maintenance_history),
    ToolSpec("find_ground_windows", "scheduling", "Ground windows (arrival to next departure) of one aircraft.",
             _schema({"tail": {"type": "string"}, "horizon_h": {"type": "number"}}, ["tail"]), find_ground_windows),
    ToolSpec("derive_tasks", "scheduling", "Maintenance tasks with hard deadlines from RUL p10 and routine checks.", _schema({}), derive_tasks),
    ToolSpec("solve_schedule", "scheduling", "Solve the maintenance schedule (cpsat, greedy or static_baseline).",
             _schema({"mode": {"type": "string", "enum": ["cpsat", "greedy", "static_baseline"]}, "weights": {"type": "object"}}), solve_schedule),
    ToolSpec("compare_plans", "scheduling", "Compare baseline, greedy and optimized plans on the same snapshot.",
             _schema({"names": {"type": "array", "items": {"type": "string"}}}), compare_plans),
    ToolSpec("check_parts", "logistics", "Stock, delivery time and shipping option of a part at a station.",
             _schema({"station": {"type": "string"}, "part": {"type": "string"}}, ["station", "part"]), check_parts),
    ToolSpec("match_technicians", "logistics", "Available technicians with a skill whose shift covers a time window.",
             _schema({"station": {"type": "string"}, "skill": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}},
                     ["station", "skill", "start", "end"]), match_technicians),
    ToolSpec("shortage_report", "logistics", "Tasks the plan could not place and parts that must be shipped.", _schema({}), shortage_report),
    ToolSpec("simulate_plan", "operations", "Metrics of a plan (runs=1 analytic; runs>=2 discrete-event simulation).",
             _schema({"plan_id": {"type": "string"}, "runs": {"type": "integer"}}), simulate_plan),
    ToolSpec("flights_affected", "operations", "Flights whose departure the plan delays.",
             _schema({"plan_id": {"type": "string"}}), flights_affected),
    ToolSpec("replan_with_event", "operations", "Apply a disruption (part delay, closed hangar, technician out, new fault) and replan.",
             _schema({"event": {"type": "object"}}, ["event"]), replan_with_event),
    ToolSpec("draft_work_orders", "coordinator", "Draft work orders (pending human approval) from a plan.",
             _schema({"plan_id": {"type": "string"}}), draft_work_orders),
    ToolSpec("search_kb", "coordinator", "Search the synthetic task-card knowledge base.",
             _schema({"query": {"type": "string"}}, ["query"]), search_kb),
]}


def call_tool(name: str, args: Optional[dict] = None) -> dict:
    """Run a tool; expected failures come back as ``{"data": None, "error": ..., "evidence": ..., "missing_info": [...]}``."""
    spec = TOOLS.get(name)
    if spec is None:
        return {"data": None, "error": f"unknown tool {name!r}", "evidence": EvidenceRef(kind="tool_call", ref=name).model_dump(mode="json"),
                "missing_info": []}
    try:
        return spec.fn(**(args or {}))
    except (ToolError, EventError, ValidationError, TypeError) as exc:
        return {"data": None, "error": str(exc), "evidence": EvidenceRef(kind="tool_call", ref=name, note="failed").model_dump(mode="json"),
                "missing_info": [f"{name}: {exc}"]}
