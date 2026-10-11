"""Test helpers: an INDEPENDENT hard-constraint checker (it does not call app.solver.plan_metrics.validate_plan,
so a bug in the production guard cannot hide a bug in a solver) and a tiny hand-built world for targeted cases."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from app.contracts.models import (
    Aircraft, ComponentState, DataQuality, FleetState, Leg, PartStock, Plan, RulEstimate, Station, Task, Technician,
)
from app.solver.common import UTC

T0 = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def hrs(h: float) -> timedelta:
    return timedelta(hours=h)


# ---------------------------------------------------------------- independent checker
def hard_violations(state: FleetState, tasks: list[Task], plan: Plan, max_ext_h: float = 3.0) -> list[str]:
    """Every hard rule, re-derived from scratch with plain loops."""
    out: list[str] = []
    by_id = {t.id: t for t in tasks}
    tech = {t.id: (t, s.iata) for s in state.stations for t in s.technicians}
    slots = {s.iata: s.hangar_slots for s in state.stations}
    legs = defaultdict(list)
    for l in state.legs:
        legs[l.tail].append(l)
    stock = {(s.iata, p.part): p.qty for s in state.stations for p in s.parts}

    for a in plan.assignments:
        t = by_id[a.task_id]
        if a.end > t.deadline:
            out.append(f"{a.task_id} ends after its deadline")
        if a.end - a.start < hrs(t.duration_h) - timedelta(seconds=1):
            out.append(f"{a.task_id} shorter than its duration")
        tc, home = tech[a.technician_id]
        if t.required_skill not in tc.skills or not tc.available or home != a.station:
            out.append(f"{a.task_id} has an unqualified / unavailable / remote technician")
        if a.start < tc.shift_start or a.end > tc.shift_end:
            out.append(f"{a.task_id} outside the technician's shift")
        if slots[a.station] <= 0:
            out.append(f"{a.task_id} in a closed hangar")
        seq = sorted(legs[a.tail], key=lambda l: l.sched_dep)
        ok = False
        for prev, nxt in zip(seq, seq[1:]):
            if prev.arr == nxt.dep == a.station and prev.sched_arr <= a.start < nxt.sched_dep + hrs(max_ext_h):
                ok = a.end <= nxt.sched_dep + hrs(max_ext_h) + timedelta(seconds=1)
        if seq and seq[0].dep == a.station and a.start < seq[0].sched_dep + hrs(max_ext_h) and a.end <= seq[0].sched_dep + hrs(max_ext_h) + timedelta(seconds=1):
            ok = True
        if not ok:
            out.append(f"{a.task_id} is not inside a ground window (+extension) of {a.tail} at {a.station}")
        if t.required_part:
            stock[(a.part_source or a.station, t.required_part)] -= t.part_qty
    for k, v in stock.items():
        if v < 0:
            out.append(f"stock of {k} went negative")

    def clash(items):
        items = sorted(items)
        return any(items[i + 1][0] < items[i][1] for i in range(len(items) - 1))

    tech_iv, tail_iv = defaultdict(list), defaultdict(list)
    for a in plan.assignments:
        tech_iv[a.technician_id].append((a.start, a.end))
        tail_iv[a.tail].append((a.start, a.end))
    if any(clash(v) for v in tech_iv.values()):
        out.append("a technician is double-booked")
    if any(clash(v) for v in tail_iv.values()):
        out.append("an aircraft is in two jobs at once")
    for st, cap in slots.items():
        iv = [(a.start, a.end) for a in plan.assignments if a.station == st]
        for s0, _ in iv:
            if sum(1 for s1, e1 in iv if s1 <= s0 < e1) > cap:
                out.append(f"{st} over capacity")
                break
    return out


def total_cost(plan: Plan) -> float:
    return plan.objective_value if plan.objective_value is not None else float("inf")


# ---------------------------------------------------------------- tiny hand-built world
def mini_state(
    *,
    aircraft: Optional[list[tuple[str, str]]] = None,                       # (tail, home)
    windows: Optional[dict[str, list[tuple[str, float, float]]]] = None,    # tail -> [(station, start_h, end_h)] in time order
    stations: Optional[list[Station]] = None,
    rul_p10: Optional[dict[str, float]] = None,                             # component_id -> p10
) -> FleetState:
    """A small world where every requested ground window exists exactly: window i is "sitting at station from hour s to hour e"
    (hours after T0). Legs are chained so leg i arrives at window i's station at s and leg i+1 leaves it at e.
    Aircraft are 'AIR' at T0 so no extra initial window is added."""
    aircraft = aircraft or [("SIM-001", "AAA")]
    windows = windows or {}
    legs: list[Leg] = []
    comps: list[ComponentState] = []
    for tail, home in aircraft:
        wins = windows.get(tail, [])
        prev_station = home
        for i, (stn, s, e) in enumerate(wins):
            dep_h = wins[i - 1][2] if i else s - 1.0
            legs.append(Leg(id=f"L-{tail}-{i}", tail=tail, dep=prev_station, arr=stn,
                            sched_dep=T0 + hrs(dep_h), sched_arr=T0 + hrs(s)))
            prev_station = stn
        if wins:
            stn, _, e = wins[-1]
            legs.append(Leg(id=f"L-{tail}-out", tail=tail, dep=stn, arr="OUT",
                            sched_dep=T0 + hrs(e), sched_arr=T0 + hrs(e + 1.0)))
        comps.append(ComponentState(
            tail=tail, component_id=f"{tail}/engine_1", kind="engine", cycles=100, hours=180.0, health=0.9, anomaly_score=0.1,
            rul_cycles=RulEstimate(p10=(rul_p10 or {}).get(f"{tail}/engine_1", 400.0), p50=420.0, p90=450.0),
            data_quality=DataQuality(status="ok"), status="ok", updated=T0))
    ac = [Aircraft(tail=t, type="A320-class", home=h, location="AIR", status="ok", cycles=1, hours=1.0) for t, h in aircraft]
    return FleetState(now=T0, aircraft=ac, components=comps, legs=legs, stations=stations or [simple_station("AAA")])


def simple_station(iata: str, slots: int = 1, skills: tuple[str, ...] = ("engine",), n_tech: int = 1,
                   kits: int = 1, eta: float = 0.0) -> Station:
    techs = [Technician(id=f"{iata}-T{i + 1}", skills=list(skills), shift_start=T0 - hrs(12), shift_end=T0 + hrs(24 * 6))
             for i in range(n_tech)]
    return Station(iata=iata, hangar_slots=slots, technicians=techs,
                   parts=[PartStock(part="BORESCOPE-KIT", qty=kits, eta_hours=eta)], lat=0.0, lon=0.0)


def engine_task(tid: str, tail: str, deadline_h: float, duration_h: float = 4.0, part: Optional[str] = "BORESCOPE-KIT") -> Task:
    return Task(id=tid, tail=tail, component_id=f"{tail}/engine_1", title="Engine borescope inspection", duration_h=duration_h,
                earliest=T0, deadline=T0 + hrs(deadline_h), required_skill="engine", required_part=part, part_qty=1 if part else 0,
                source="rul", basis="test")
