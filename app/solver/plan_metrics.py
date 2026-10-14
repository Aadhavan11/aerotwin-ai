"""Plan metrics, the objective value and the hard-constraint validator (shared by every solver mode).

Definitions (documented because the demo numbers come from here):

* ``downtime_h``          sum of ``extension_h``: hours a job runs past its planned ground window (the aircraft's next
                          departure is delayed by that much). Cascades onto later legs are ignored (simplification).
* ``flights_affected``    1 per assignment with ``extension_h > 0`` (same simplification).
* ``logistics_cost_usd``  sum of ``ship_cost_usd`` of the parts that are shipped in (not on hand).
* ``total_cost_usd``      estimated cost from a simulated price list: parts + labor + bay time + a fixed overhead per job + shipping + delay + a deferral allowance per late or
                          unscheduled task (see ``app/solver/costs.py``).
* ``tech_utilization``    technician busy hours / available technician hours over the first ``horizon_h`` hours.
* ``hangar_utilization``  hangar busy hours / (hangar slots x horizon hours), over stations that are open.
* ``schedule_changes``    assignments moved (different station or start), added or dropped versus ``previous``.
* ``late_or_unscheduled`` unscheduled tasks + any assignment that ends after its deadline (must be 0 for a feasible plan).

``plan_objective`` is what every mode reports as ``Plan.objective_value``:
    alpha*downtime_h + beta*flights_affected + gamma*logistics_usd + delta*schedule_changes
    + earliness_per_h * (sum over jobs of hours from now until the job ends)      <- soft preference for sooner, safer fixes
    + UNSCHEDULED_PENALTY * unscheduled tasks
Deadlines are NOT in the objective; they are hard constraints.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Iterable, Optional

from app.contracts.models import Assignment, FleetState, ObjectiveWeights, Plan, PlanMetrics, Task
from app.solver import costs
from app.solver.common import UNSCHEDULED_PENALTY, aware, minutes_between
from app.solver.skills import has_skill

EARLINESS_PER_H = 0.05   # cost units per hour a job finishes later; a gentle tie-breaker, never trades against a flight or a dollar bill


def _ship_cost(state: FleetState, a: Assignment, task: Optional[Task]) -> float:
    if not a.part_source or task is None or not task.required_part:
        return 0.0
    for st in state.stations:
        if st.iata == a.station:
            for p in st.parts:
                if p.part == task.required_part:
                    return float(p.ship_cost_usd)
    return 0.0


def diff_counts(previous: Optional[Plan], assignments: Iterable[Assignment]) -> tuple[list[tuple[Assignment, Assignment]], list[Assignment], list[Assignment]]:
    """(moved pairs (old, new), added, dropped) of ``assignments`` versus ``previous``. Moved = station or start differs."""
    new = {a.task_id: a for a in assignments}
    if previous is None:
        return [], [], []
    old = {a.task_id: a for a in previous.assignments}
    moved = [(old[t], new[t]) for t in sorted(new) if t in old
             and (old[t].station != new[t].station or aware(old[t].start) != aware(new[t].start))]
    added = [new[t] for t in sorted(new) if t not in old]
    dropped = [old[t] for t in sorted(old) if t not in new]
    return moved, added, dropped


def compute_metrics(state: FleetState, tasks: list[Task], assignments: list[Assignment],
                    previous: Optional[Plan] = None, horizon_h: float = 120.0) -> PlanMetrics:
    now = aware(state.now)
    task_by_id = {t.id: t for t in tasks}
    downtime = sum(a.extension_h for a in assignments)
    flights = sum(1 for a in assignments if a.extension_h > 1e-9)
    logistics = sum(_ship_cost(state, a, task_by_id.get(a.task_id)) for a in assignments)
    late = sum(1 for a in assignments if a.task_id in task_by_id and aware(a.end) > aware(task_by_id[a.task_id].deadline))
    unscheduled = len([t for t in tasks if t.id not in {a.task_id for a in assignments}])

    horizon_min = horizon_h * 60.0

    def inside(start: datetime, end: datetime) -> float:
        s = max(0.0, minutes_between(start, now))
        e = min(horizon_min, minutes_between(end, now))
        return max(0.0, e - s)

    tech_busy: dict[str, float] = defaultdict(float)
    hangar_busy: dict[str, float] = defaultdict(float)
    for a in assignments:
        m = inside(a.start, a.end)
        if a.technician_id:
            tech_busy[a.technician_id] += m
        hangar_busy[a.station] += m
    tech_avail = sum(inside(t.shift_start, t.shift_end) for s in state.stations for t in s.technicians if t.available)
    hangar_avail = sum(s.hangar_slots * horizon_min for s in state.stations if s.hangar_slots > 0)
    moved, added, dropped = diff_counts(previous, assignments)
    jobs = [costs.job_cost(task_by_id.get(a.task_id), a) for a in assignments]
    c_parts, c_labor, c_bay = (round(sum(j[k] for j in jobs), 2) for k in ("parts", "labor", "bay"))
    c_delay = round(sum(j["delay"] for j in jobs), 2)
    c_overhead = round(sum(j["overhead"] for j in jobs), 2)
    c_exposure = round((late + unscheduled) * costs.DEFERRAL_ALLOWANCE_USD, 2)

    return PlanMetrics(
        downtime_h=round(downtime, 4),
        flights_affected=flights,
        logistics_cost_usd=round(logistics, 2),
        tech_utilization=round(sum(tech_busy.values()) / tech_avail, 4) if tech_avail > 0 else 0.0,
        hangar_utilization=round(sum(hangar_busy.values()) / hangar_avail, 4) if hangar_avail > 0 else 0.0,
        schedule_changes=len(moved) + len(added) + len(dropped),
        tasks_total=len(tasks),
        tasks_scheduled=len(assignments),
        late_or_unscheduled=late + unscheduled,
        cost_parts_usd=c_parts, cost_labor_usd=c_labor, cost_bay_usd=c_bay, cost_delay_usd=c_delay, cost_exposure_usd=c_exposure, cost_overhead_usd=c_overhead,
        total_cost_usd=costs.total_cost(c_parts, c_labor, c_bay, round(logistics, 2), c_delay, c_exposure, c_overhead),
    )


def plan_objective(state: FleetState, metrics: PlanMetrics, assignments: list[Assignment], weights: ObjectiveWeights,
                   earliness_per_h: float = EARLINESS_PER_H) -> float:
    now = aware(state.now)
    wait_h = sum(max(0.0, minutes_between(a.end, now)) / 60.0 for a in assignments)
    unscheduled = metrics.tasks_total - metrics.tasks_scheduled
    return round(
        weights.downtime * metrics.downtime_h + weights.disruption * metrics.flights_affected
        + weights.logistics * metrics.logistics_cost_usd + weights.changes * metrics.schedule_changes
        + earliness_per_h * wait_h + UNSCHEDULED_PENALTY * unscheduled, 4)


def validate_plan(state: FleetState, tasks: list[Task], assignments: list[Assignment], max_ext_h: float,
                  force_station: Optional[str] = None, force_tails: Optional[set[str]] = None) -> list[str]:
    """Re-check every hard constraint on a finished plan. Empty list = valid. Used as a guard before a plan is returned,
    so a bug in any solver can never ship a plan that breaks a deadline, a capacity or a part limit."""
    from app.solver.candidates import SolveContext  # local import: candidates imports nothing from here

    ctx = SolveContext(state, tasks, max_ext_h=max_ext_h)
    now = aware(state.now)
    bad: list[str] = []
    task_by_id = {t.id: t for t in tasks}
    stations = ctx.stations
    techs = {t.id: (t, s.iata) for s in state.stations for t in s.technicians}
    seen: set[str] = set()
    for a in assignments:
        t = task_by_id.get(a.task_id)
        if t is None:
            bad.append(f"{a.task_id}: assignment for an unknown task")
            continue
        if a.task_id in seen:
            bad.append(f"{a.task_id}: assigned twice")
        seen.add(a.task_id)
        s, e = aware(a.start), aware(a.end)
        if e > aware(t.deadline):
            bad.append(f"{a.task_id}: ends {e.isoformat()} after its deadline {aware(t.deadline).isoformat()}")
        if s < aware(t.earliest) or s < now:
            bad.append(f"{a.task_id}: starts before its earliest time")
        if minutes_between(e, s) < t.duration_h * 60.0 - 1e-6:
            bad.append(f"{a.task_id}: shorter than its {t.duration_h} h duration")
        if a.tail != t.tail:
            bad.append(f"{a.task_id}: assigned to {a.tail}, task belongs to {t.tail}")
        if force_station and a.station != force_station and (force_tails is None or a.tail in force_tails):
            bad.append(f"{a.task_id}: not at the forced station {force_station}")
        st = stations.get(a.station)
        if st is None or st.hangar_slots <= 0:
            bad.append(f"{a.task_id}: station {a.station} has no hangar capacity")

        # inside a real ground window of this aircraft at this station, with an honest, capped extension
        wins = [w for w in ctx.windows.get(t.tail, []) if w.station == a.station
                and aware(w.start) <= s + _EPS and s < aware(w.end) + timedelta(hours=max_ext_h)]
        if not wins:
            bad.append(f"{a.task_id}: not inside any ground window of {t.tail} at {a.station}")
        else:
            w = min(wins, key=lambda w: abs(minutes_between(w.start, s)))
            ext_h = max(0.0, minutes_between(e, w.end)) / 60.0
            if ext_h > max_ext_h + 1e-6:
                bad.append(f"{a.task_id}: extension {ext_h:.2f} h exceeds the {max_ext_h} h limit")
            if abs(ext_h - a.extension_h) > 0.02:
                bad.append(f"{a.task_id}: reported extension {a.extension_h} h but the job overruns its window by {ext_h:.2f} h")

        tech = techs.get(a.technician_id or "")
        if tech is None:
            bad.append(f"{a.task_id}: unknown technician {a.technician_id}")
        else:
            tc, tstation = tech
            if not has_skill(tc.skills, t.required_skill):
                bad.append(f"{a.task_id}: technician {tc.id} lacks skill {t.required_skill}")
            if not tc.available:
                bad.append(f"{a.task_id}: technician {tc.id} is unavailable")
            if tstation != a.station:
                bad.append(f"{a.task_id}: technician {tc.id} is based at {tstation}, not {a.station}")
            if s < aware(tc.shift_start) or e > aware(tc.shift_end):
                bad.append(f"{a.task_id}: outside the shift of {tc.id}")

        # the part must exist and be at the station when the job starts
        if t.required_part and t.part_qty > 0:
            opts = [o for o in ctx.part_options(a.station, t) if o.source == a.part_source]
            if not opts:
                bad.append(f"{a.task_id}: no valid source for {t.required_part} at {a.station} (part_source={a.part_source})")
            elif minutes_between(s, now) + 1e-6 < min(o.ready_min for o in opts):
                bad.append(f"{a.task_id}: {t.required_part} arrives after the job starts")

    def overlap_pairs(items: list[tuple[str, datetime, datetime]]) -> Iterable[tuple[str, str]]:
        items = sorted(items, key=lambda x: x[1])
        for i, (ia, sa, ea) in enumerate(items):
            for ib, sb, eb in items[i + 1:]:
                if sb >= ea:
                    break
                yield ia, ib

    by_tech: dict[str, list] = defaultdict(list)
    by_tail: dict[str, list] = defaultdict(list)
    by_station: dict[str, list] = defaultdict(list)
    for a in assignments:
        item = (a.task_id, aware(a.start), aware(a.end))
        if a.technician_id:
            by_tech[a.technician_id].append(item)
        by_tail[a.tail].append(item)
        by_station[a.station].append(item)
    for k, items in by_tech.items():
        bad += [f"technician {k} double-booked: {x} and {y}" for x, y in overlap_pairs(items)]
    for k, items in by_tail.items():
        bad += [f"aircraft {k} in two jobs at once: {x} and {y}" for x, y in overlap_pairs(items)]
    for k, items in by_station.items():
        slots = stations[k].hangar_slots if k in stations else 0
        for p in sorted({s for _, s, _ in items}):
            if sum(1 for _, s, e in items if s <= p < e) > slots:
                bad.append(f"station {k} exceeds {slots} hangar slot(s) at {p.isoformat()}")
                break

    used: dict[tuple[str, str], int] = defaultdict(int)
    for a in assignments:
        t = task_by_id.get(a.task_id)
        if t and t.required_part and t.part_qty > 0:
            used[(a.part_source or a.station, t.required_part)] += t.part_qty
    for (stn, part), n in used.items():
        have = next((p.qty for p in (stations[stn].parts if stn in stations else []) if p.part == part), 0)
        if n > have:
            bad.append(f"part {part} at {stn}: {n} needed but only {have} in stock")
    return bad


_EPS = timedelta(seconds=1)
