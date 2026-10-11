"""simulate(plan, state, runs=1, seed=0) -> PlanMetrics (feature 3.5).

L1 (runs <= 1)  analytic: the plan's own numbers, recomputed against ``state`` (same definitions as app/solver/plan_metrics.py).
L2 (runs >= 2)  SimPy discrete-event run, repeated ``runs`` times with a seeded RNG:
                * one process per aircraft steps through its future legs;
                * every maintenance assignment runs in the ground window before its leg, queueing for a Resource per
                  technician (capacity 1) and per hangar (capacity = slots);
                * job durations are jittered uniformly by +-JITTER (15%);
                * if a job is not finished at the (possibly already delayed) departure time, the departure slips and
                  the delay CASCADES onto the aircraft's later legs, absorbed only by ground-time slack
                  (a turnaround never needs more than MIN_TURN_H, a synthetic assumption).
                The returned PlanMetrics holds MEANS over the runs; ``simulate_detailed`` also gives p90 and the per-run values.
                In L2, ``flights_affected`` counts every delayed departure including cascaded ones, so it can exceed the
                analytic count (which ignores cascades).

Everything here is SIMULATED and uses synthetic durations; the numbers are not verified airline results.
"""
from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from app.contracts.models import Assignment, FleetState, Leg, Plan, PlanMetrics, Task
from app.solver.common import DEFAULT_HORIZON_H, aware, minutes_between
from app.solver import costs
from app.solver.plan_metrics import compute_metrics
from app.solver.tasks import derive_tasks

log = logging.getLogger(__name__)

JITTER = 0.15
MIN_TURN_H = 0.5


@dataclass
class SimSummary:
    runs: int
    metrics: PlanMetrics                       # means over the runs
    downtime_mean_h: float
    downtime_p90_h: float
    flights_mean: float
    flights_p90: float
    deadline_overrun_runs: int                 # runs in which some job finished after its (hard) deadline because of jitter
    per_run_downtime_h: list[float] = field(default_factory=list)


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    return v[min(len(v) - 1, max(0, math.ceil(q * len(v)) - 1))]


def _analytic(plan: Plan, state: FleetState, tasks: list[Task]) -> PlanMetrics:
    m = compute_metrics(state, tasks, plan.assignments, previous=None, horizon_h=DEFAULT_HORIZON_H)
    return m.model_copy(update={"schedule_changes": plan.metrics.schedule_changes})


def simulate(plan: Plan, state: FleetState, runs: int = 1, seed: int = 0, tasks: Optional[list[Task]] = None) -> PlanMetrics:
    """Contract entry point. ``tasks`` defaults to ``derive_tasks(state)`` (same state the plan was made from)."""
    tasks = tasks if tasks is not None else derive_tasks(state)
    if runs <= 1:
        return _analytic(plan, state, tasks)
    try:
        return simulate_detailed(plan, state, runs=runs, seed=seed, tasks=tasks).metrics
    except ImportError:
        log.warning("simpy is not installed: returning the analytic metrics instead of a %d-run simulation", runs)
        return _analytic(plan, state, tasks)


def affected_legs(plan: Plan, state: FleetState) -> list[Leg]:
    """Legs whose departure the plan delays directly (assignments with an extension). For the Operations agent."""
    out: dict[str, Leg] = {}
    for a in plan.assignments:
        if a.extension_h > 1e-9:
            leg = _leg_for(a, [l for l in state.legs if l.tail == a.tail])
            if leg is not None:
                out[leg.id] = leg
    return sorted(out.values(), key=lambda l: (aware(l.sched_dep), l.id))


def _leg_for(a: Assignment, legs: list[Leg]) -> Optional[Leg]:
    """The leg that departs from the assignment's station right after the job's ground window."""
    s = aware(a.start)
    at_station = sorted((l for l in legs if l.dep == a.station), key=lambda l: aware(l.sched_dep))
    later = [l for l in at_station if aware(l.sched_dep) >= s - timedelta(seconds=1)]
    if later:
        return later[0]
    near = [l for l in at_station if aware(l.sched_dep) >= s - timedelta(hours=6)]   # job started after the planned departure
    return near[-1] if near else None


def simulate_detailed(plan: Plan, state: FleetState, runs: int = 20, seed: int = 0,
                      tasks: Optional[list[Task]] = None, jitter: float = JITTER) -> SimSummary:
    import simpy  # imported lazily so the rest of the module works without it

    tasks = tasks if tasks is not None else derive_tasks(state)
    task_by_id = {t.id: t for t in tasks}
    base = _analytic(plan, state, tasks)
    now = aware(state.now)
    hrs = lambda dt: minutes_between(dt, now) / 60.0  # noqa: E731

    legs_by_tail: dict[str, list[Leg]] = {}
    for l in sorted(state.legs, key=lambda l: (aware(l.sched_dep), l.id)):
        if aware(l.sched_arr) > now:
            legs_by_tail.setdefault(l.tail, []).append(l)
    jobs_by_leg: dict[str, list[Assignment]] = {}
    unmapped = 0
    for a in plan.assignments:
        leg = _leg_for(a, legs_by_tail.get(a.tail, []))
        if leg is None:
            unmapped += 1
            continue
        jobs_by_leg.setdefault(leg.id, []).append(a)
    if unmapped:
        log.warning("%d assignment(s) could not be tied to a departure; they are ignored by the simulation", unmapped)

    horizon_h = DEFAULT_HORIZON_H
    tech_avail_h = sum(max(0.0, min(horizon_h, hrs(t.shift_end)) - max(0.0, hrs(t.shift_start)))
                       for s in state.stations for t in s.technicians if t.available)
    hangar_avail_h = sum(s.hangar_slots * horizon_h for s in state.stations if s.hangar_slots > 0)
    unscheduled = base.tasks_total - base.tasks_scheduled

    def one_run(run: int) -> tuple[float, int, float, float, int]:
        rng = random.Random(seed * 1_000_003 + run)
        env = simpy.Environment()
        tech = {t.id: simpy.Resource(env, 1) for s in state.stations for t in s.technicians}
        hangar = {s.iata: simpy.Resource(env, s.hangar_slots) for s in state.stations if s.hangar_slots > 0}
        acc = {"downtime": 0.0, "flights": 0, "tech_busy": 0.0, "hangar_busy": 0.0, "overrun": 0}

        def job(a: Assignment, arrive_h: float):
            planned = (aware(a.end) - aware(a.start)).total_seconds() / 3600.0
            yield env.timeout(max(0.0, hrs(a.start) - env.now, arrive_h - env.now))
            dur = planned * rng.uniform(1.0 - jitter, 1.0 + jitter)
            with tech[a.technician_id].request() as rt:           # type: ignore[index]
                yield rt
                with hangar[a.station].request() as rh:
                    yield rh
                    yield env.timeout(dur)
                    used = dur
            end = env.now
            acc["tech_busy"] += used
            acc["hangar_busy"] += used
            t = task_by_id.get(a.task_id)
            if t is not None and end > hrs(t.deadline) + 1e-9:
                acc["overrun"] += 1
            return end

        def aircraft(tail: str):
            legs = legs_by_tail.get(tail, [])
            prev_delay = 0.0
            prev: Optional[Leg] = None
            for leg in legs:
                sd = hrs(leg.sched_dep)
                if prev is None:
                    arrive_h, window_len = 0.0, max(sd, 0.0)   # on the ground since "now"
                else:
                    arrive_h = hrs(prev.sched_arr) + prev_delay
                    window_len = hrs(leg.sched_dep) - hrs(prev.sched_arr)
                if prev is None and sd <= 0.0:       # already airborne at "now"
                    prev, prev_delay = leg, 0.0
                    continue
                base_dep = max(sd, arrive_h + min(MIN_TURN_H, max(window_len, 0.0)))
                job_end = arrive_h
                for a in sorted(jobs_by_leg.get(leg.id, []), key=lambda a: (aware(a.start), a.task_id)):
                    procs_end = yield env.process(job(a, arrive_h))
                    job_end = max(job_end, procs_end)
                dep_actual = max(base_dep, job_end)
                acc["downtime"] += dep_actual - base_dep
                delay = dep_actual - sd
                if delay > 1.0 / 60.0:
                    acc["flights"] += 1
                yield env.timeout(max(0.0, dep_actual - env.now))
                prev, prev_delay = leg, max(0.0, delay)

        for tail in sorted(legs_by_tail):
            env.process(aircraft(tail))
        env.run()
        return (acc["downtime"], acc["flights"],
                acc["tech_busy"] / tech_avail_h if tech_avail_h else 0.0,
                acc["hangar_busy"] / hangar_avail_h if hangar_avail_h else 0.0, acc["overrun"])

    results = [one_run(r) for r in range(max(1, runs))]
    n = len(results)
    down = [r[0] for r in results]
    flights = [float(r[1]) for r in results]
    overrun_runs = sum(1 for r in results if r[4] > 0)
    mean = lambda xs: sum(xs) / len(xs)  # noqa: E731
    metrics = PlanMetrics(
        downtime_h=round(mean(down), 4), flights_affected=int(round(mean(flights))),
        logistics_cost_usd=base.logistics_cost_usd,
        tech_utilization=round(mean([r[2] for r in results]), 4), hangar_utilization=round(mean([r[3] for r in results]), 4),
        schedule_changes=plan.metrics.schedule_changes, tasks_total=base.tasks_total, tasks_scheduled=base.tasks_scheduled,
        late_or_unscheduled=unscheduled + int(round(mean([float(r[4]) for r in results]))),
        cost_parts_usd=base.cost_parts_usd, cost_labor_usd=base.cost_labor_usd, cost_bay_usd=base.cost_bay_usd,
        cost_delay_usd=round(mean(down) * costs.DELAY_COST_USD_H, 2), cost_exposure_usd=base.cost_exposure_usd,
        total_cost_usd=costs.total_cost(base.cost_parts_usd, base.cost_labor_usd, base.cost_bay_usd, base.logistics_cost_usd,
                                        mean(down) * costs.DELAY_COST_USD_H, base.cost_exposure_usd),
    )
    return SimSummary(runs=n, metrics=metrics, downtime_mean_h=round(mean(down), 4), downtime_p90_h=round(_percentile(down, 0.9), 4),
                      flights_mean=round(mean(flights), 3), flights_p90=_percentile(flights, 0.9),
                      deadline_overrun_runs=overrun_runs, per_run_downtime_h=[round(d, 4) for d in down])
