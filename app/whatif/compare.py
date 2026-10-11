"""compare(state, plans) -> Comparison, plus the scenario builder behind POST /whatif/compare (feature 3.5).

Every scenario starts from the SAME snapshot: apply its events (pure), re-derive tasks, honour its constraints, solve:
  constraints.force_station  only that station's ground windows are used ("service at another airport")
  constraints.earliest       ISO time; no task may start before it ("maintenance now vs after several more flights")
  constraints.tail           optional: limit both constraints to this aircraft's tasks (default: every task)
Each scenario is solved independently (no stability penalty toward the current plan, so the comparison is not biased);
``schedule_changes`` is then measured against the current plan.

The doc's five questions are available as ``default_scenarios`` (used when the request has no scenarios).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from app.contracts.models import (
    Comparison, FleetState, ObjectiveWeights, Plan, Scenario, ScenarioEvent, Task,
)
from app.solver.common import aware
from app.solver.engine import solve
from app.solver.exposure import exposure
from app.solver.plan_metrics import compute_metrics, plan_objective
from app.solver.tasks import derive_tasks
from app.whatif.events import EventError, apply_event


def compare(state: FleetState, plans: dict[str, Plan]) -> Comparison:
    """One row per named plan. Metrics are the ones ``solve`` computed on that scenario's own snapshot
    (re-deriving them against ``state`` would be wrong for scenarios that changed the tasks)."""
    rows = []
    tasks = derive_tasks(state)
    for name, p in plans.items():
        known = {t.id for t in tasks}
        rows.append({
            "name": name, "feasible": p.feasible, "mode": p.mode, "plan_id": p.id,
            "metrics": p.metrics.model_dump(), "objective_value": p.objective_value,
            "solve_seconds": p.solve_seconds, "as_of": aware(state.now).isoformat(),
            "infeasible_reasons": list(p.infeasible_reasons),
            # risk, not just cost: how long the fault stays open and the margin to the hard deadline (only for tasks known in this state)
            "exposure": exposure(state, tasks, p.model_copy(update={"assignments": [a for a in p.assignments if a.task_id in known]})),
        })
    return Comparison(rows=rows)


def _parse_time(value) -> datetime:
    if isinstance(value, datetime):
        return aware(value)
    return aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))


def build_scenario_plans(state: FleetState, scenarios: list[Scenario], weights: Optional[ObjectiveWeights] = None,
                         time_limit_s: float = 3.0, base_plan: Optional[Plan] = None) -> dict[str, Plan]:
    plans: dict[str, Plan] = {}
    for sc in scenarios:
        name, n = sc.name, 2
        while name in plans:
            name, n = f"{sc.name} ({n})", n + 1
        plans[name] = _solve_scenario(state, sc, weights, time_limit_s, base_plan)
    return plans


def _solve_scenario(state: FleetState, sc: Scenario, weights: Optional[ObjectiveWeights], time_limit_s: float,
                    base_plan: Optional[Plan]) -> Plan:
    st = state
    try:
        for ev in sc.events:
            st, _ = apply_event(st, ev)
        tasks = derive_tasks(st)
        tail = sc.constraints.get("tail")
        earliest = sc.constraints.get("earliest")
        if earliest:
            e = _parse_time(earliest)
            tasks = [t.model_copy(update={"earliest": max(aware(t.earliest), e)}) if (not tail or t.tail == tail) else t
                     for t in tasks]
        force = sc.constraints.get("force_station")
        force_tails = {tail} if tail else None
    except (EventError, ValueError) as exc:
        return _rejected(state, sc, str(exc))
    plan = solve(st, tasks, weights, "cpsat", time_limit_s, previous=None, force_station=force, force_tails=force_tails)
    if base_plan is not None:
        metrics = compute_metrics(st, tasks, plan.assignments, previous=base_plan)
        plan = plan.model_copy(update={
            "metrics": metrics,
            "objective_value": plan_objective(st, metrics, plan.assignments, weights or ObjectiveWeights())})
    return plan


def _rejected(state: FleetState, sc: Scenario, message: str) -> Plan:
    from app.contracts.models import PlanMetrics
    from app.solver.store import STORE
    zero = PlanMetrics(downtime_h=0, flights_affected=0, logistics_cost_usd=0, tech_utilization=0, hangar_utilization=0,
                       schedule_changes=0, tasks_total=0, tasks_scheduled=0, late_or_unscheduled=0)
    return Plan(id=STORE.next_plan_id(), created=aware(state.now), mode="cpsat", feasible=False,
                infeasible_reasons=[f"scenario '{sc.name}' rejected: {message}"], assignments=[], metrics=zero, solve_seconds=0.0)


def default_scenarios(state: FleetState, base_plan: Plan, tasks: Optional[list[Task]] = None) -> list[Scenario]:
    """The five what-if questions from the concept doc, built around the most urgent task of the current plan."""
    tasks = tasks if tasks is not None else derive_tasks(state)
    now = aware(state.now)
    scenarios = [Scenario(name="Current plan (optimized)")]
    if not tasks or not base_plan.assignments:
        return scenarios
    urgent = next((t for t in tasks if t.source == "rul"), tasks[0])   # most urgent condition-driven task
    a = next((x for x in base_plan.assignments if x.task_id == urgent.id), None)
    if a is None:   # the urgent task is not in the plan (infeasible): nothing to vary around
        return scenarios
    home = next((ac.home for ac in state.aircraft if ac.tail == urgent.tail), None)
    others = [s.iata for s in state.stations if s.iata != a.station and s.hangar_slots > 0]
    other = home if home and home != a.station else (others[0] if others else None)

    scenarios.append(Scenario(name=f"{urgent.tail}: maintenance later, not before +24 h", constraints={"earliest": (now + timedelta(hours=24)).isoformat(), "tail": urgent.tail}))
    if other:
        scenarios.append(Scenario(name=f"{urgent.tail}: service at another airport ({other})", constraints={"force_station": other, "tail": urgent.tail}))
    if urgent.required_part:
        scenarios.append(Scenario(name=f"{urgent.required_part} at {a.station} delayed 12 h", events=[ScenarioEvent(
            id="E-W1", type="part_delay", at=now, station=a.station, part=urgent.required_part, delay_hours=12.0)]))
    if a.technician_id:
        scenarios.append(Scenario(name=f"Technician {a.technician_id} needed elsewhere (unavailable)", events=[ScenarioEvent(
            id="E-W2", type="tech_unavailable", at=now, technician_id=a.technician_id)]))
    scenarios.append(Scenario(name=f"{a.station} hangar unavailable", events=[ScenarioEvent(
        id="E-W3", type="facility_closed", at=now, station=a.station)]))
    return scenarios
