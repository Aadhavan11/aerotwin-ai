"""Everything the Maintenance screen shows for one aircraft, joined in one place.

``GET /aircraft/{tail}/maintenance`` is read-only and additive (CONTRACTS-CHANGELOG.md). Nothing here is
invented: tasks come from the scheduler (``derive_tasks``), slots from the current plan, orders from the
shared work-order store, stock and shifts from the station records, history from the twin timeline.
The suggestions are advice drawn from the same health numbers the twin shows; they are not tasks and
nothing is scheduled or approved by them.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.contracts.models import Assignment, Plan, Station, Task, WorkOrder
from app.copilot import kb, workorders

LABEL = {"engine": "Engine", "brakes": "Brakes", "landing_gear": "Landing gear", "hydraulics": "Hydraulics", "avionics": "Avionics"}
HISTORY_KINDS_HIDDEN = {"flight"}


def _aware(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _iso(t: Optional[datetime]) -> Optional[str]:
    return _aware(t).strftime("%Y-%m-%dT%H:%M:%SZ") if t else None


def _component_name(component_id: str) -> str:
    short = component_id.split("/")[-1]
    if short.startswith("engine_"):
        return f"Engine {short.split('_')[1]}"
    return LABEL.get(short, short.replace("_", " ").title())


def _card(title: str) -> Optional[dict]:
    c = kb.card_for_title(title)
    if not c:
        return None
    return {"id": c.get("id"), "ata": c.get("ata"), "note": c.get("note"), "trigger": c.get("trigger"),
            "interval_days": c.get("interval_days"), "synthetic": True}


def _station(stations: dict[str, Station], iata: str) -> Optional[Station]:
    return stations.get(iata)


def _logistics(task: Task, a: Optional[Assignment], stations: dict[str, Station]) -> Optional[dict]:
    """Part and technician picture at the station where the job is planned."""
    if a is None:
        return None
    st = _station(stations, a.station)
    if st is None:
        return None
    start, end = _aware(a.start), _aware(a.end)
    skill = task.required_skill
    qualified = [t for t in st.technicians if skill in t.skills and t.available]
    covering = [t for t in qualified if _aware(t.shift_start) <= start and end <= _aware(t.shift_end)]
    part = next((p for p in st.parts if p.part == task.required_part), None) if task.required_part else None
    out: dict[str, Any] = {
        "station": a.station,
        "hangar_slots": st.hangar_slots,
        "skill": skill,
        "technician_assigned": a.technician_id,
        "technicians_covering": len(covering),
        "technicians_qualified": len({t.id for t in qualified}),
    }
    if part is not None:
        out["part"] = {
            "name": part.part, "needed": task.part_qty, "on_hand": part.qty, "eta_hours": part.eta_hours,
            "from_station": a.part_source, "ship_from": part.ship_from, "ship_hours": part.ship_hours,
            "ship_cost_usd": part.ship_cost_usd,
        }
    elif task.required_part:
        out["part"] = {"name": task.required_part, "needed": task.part_qty, "on_hand": 0, "eta_hours": 0.0,
                       "from_station": a.part_source, "ship_from": None, "ship_hours": 0.0, "ship_cost_usd": 0.0}
    return out


def _cost(task: Task, a: Optional[Assignment], stations: dict[str, Station]) -> Optional[dict]:
    """Estimated cost of the planned job from the simulated price list (app/solver/costs.py)."""
    if a is None:
        return None
    from app.solver import costs

    ship = 0.0
    st = stations.get(a.station)
    if a.part_source and st is not None and task.required_part:
        ship = next((float(p.ship_cost_usd) for p in st.parts if p.part == task.required_part), 0.0)
    return {**costs.job_cost(task, a, ship), "hours": round(costs.job_hours(a), 2), "crew": costs.CREW_SIZE,
            "labor_rate_usd_h": costs.LABOR_RATE_USD_H, "bay_rate_usd_h": costs.BAY_RATE_USD_H,
            "part_unit_usd": costs.PART_PRICE_USD.get(task.required_part) if task.required_part else None}


def _order_for(task_id: str, orders: list[WorkOrder]) -> Optional[WorkOrder]:
    mine = [w for w in orders if w.task_id == task_id]
    for status in ("approved", "pending_approval"):
        found = [w for w in mine if w.status == status]
        if found:
            return found[-1]
    return mine[-1] if mine else None


def _flights_per_day(legs: list, tail: str, now: datetime) -> float:
    future = [l for l in legs if l.tail == tail and _aware(l.sched_dep) >= now]
    if len(future) < 2:
        return 4.0
    span = (_aware(future[-1].sched_dep) - _aware(future[0].sched_dep)).total_seconds() / 86400
    return max(len(future) / span, 1.0) if span > 0 else 4.0


def build(tail: str) -> Optional[dict]:
    from app.solver import service
    from app.solver.exposure import exposure
    from app.solver.tasks import derive_tasks
    from app.solver.windows import ground_windows
    from app.twin import state as twin

    state, source = service.get_state()
    if not any(a.tail == tail for a in state.aircraft):
        return None
    current, baseline = service.ensure_plans(state, source)
    now = _aware(state.now)
    all_tasks = derive_tasks(state)
    tasks = sorted((t for t in all_tasks if t.tail == tail), key=lambda t: (_aware(t.deadline), t.id))
    stations = {s.iata: s for s in state.stations}
    cur = {a.task_id: a for a in current.assignments}
    base = {a.task_id: a for a in baseline.assignments}
    expo = {r["task_id"]: r for r in exposure(state, all_tasks, current)}
    orders = [w for w in workorders.list_orders() if w.tail == tail]

    items: list[dict] = []
    for t in tasks:
        a = cur.get(t.id)
        b = base.get(t.id)
        wo = _order_for(t.id, orders)
        moved = bool(wo and wo.status == "approved" and a and (a.station != wo.station or abs((_aware(a.start) - _aware(wo.start)).total_seconds()) > 60))
        items.append({
            "task_id": t.id, "title": t.title, "component_id": t.component_id, "component": _component_name(t.component_id),
            "source": t.source, "basis": t.basis, "deadline": _iso(t.deadline), "earliest": _iso(t.earliest),
            "duration_h": t.duration_h, "skill": t.required_skill, "part": t.required_part, "part_qty": t.part_qty,
            "card": _card(t.title),
            "assignment": a.model_dump(mode="json") if a else None,
            "baseline": ({"station": b.station, "start": _iso(b.start), "end": _iso(b.end)} if b else None),
            "exposure": expo.get(t.id),
            "work_order": wo.model_dump(mode="json") if wo else None,
            "approved_slot_moved": moved,
            "logistics": _logistics(t, a, stations),
            "cost": _cost(t, a, stations),
        })

    # Suggestions: components that have no open task yet but are worn or trending toward their limit.
    twin_view = twin.get_twin(tail)
    flights_per_day = _flights_per_day(state.legs, tail, now)
    open_components = {t.component_id for t in tasks}
    suggestions: list[dict] = []
    for s in twin_view.components if twin_view else []:
        if s.component_id in open_components or s.rul_cycles is None:
            continue
        kind = s.kind
        # Conservative life left in flights; the engine model is capped at 125 so a lower bar applies to engines.
        bar = 60.0 if kind == "engine" else 400.0
        if s.rul_cycles.p10 >= bar and s.health >= 0.5:
            continue
        card = kb.default_card_for(kind)
        due = now + timedelta(days=s.rul_cycles.p10 / flights_per_day)
        cap = 125.0 if kind == "engine" else 500.0
        left = f"at least {cap:g}" if s.rul_cycles.p50 >= cap - 0.05 else f"about {s.rul_cycles.p50:g}"
        suggestions.append({
            "component_id": s.component_id, "component": _component_name(s.component_id), "kind": kind, "status": s.status,
            "health": s.health, "rul_p10": s.rul_cycles.p10, "rul_p50": s.rul_cycles.p50,
            "action": card["title"] if card else "Inspect", "card_id": card["id"] if card else None,
            "duration_h": card.get("duration_h") if card else None, "skill": card.get("skill") if card else None,
            "part": card.get("part") if card else None,
            "act_before": _iso(due),
            "text": (f"{_component_name(s.component_id)}: {round(s.health * 100)}% health, "
                     f"{left} flights left (conservative {s.rul_cycles.p10:g}). At about "
                     f"{flights_per_day:.1f} flights a day, plan the work before {_iso(due)[:10]}."),
        })
    suggestions.sort(key=lambda x: x["rul_p10"])
    suggestions = suggestions[:4]

    windows = [w for w in ground_windows(state.legs, now, state.aircraft) if w.tail == tail][:12]
    history = []
    if twin_view is not None:
        history = [e.model_dump(mode="json") for e in sorted(twin_view.timeline, key=lambda e: _aware(e.ts), reverse=True)
                   if e.kind not in HISTORY_KINDS_HIDDEN][:40]
    return {
        "tail": tail, "now": _iso(now), "source": source, "plan_id": current.id,
        "flights_per_day": round(flights_per_day, 1),
        "items": items, "suggestions": suggestions, "history": history,
        "ground_windows": [{"station": w.station, "start": _iso(w.start), "end": _iso(w.end), "hours": round(w.hours, 1),
                            "before_leg": w.before_leg} for w in windows],
    }


def queue() -> dict:
    """One row per open task across the fleet, most urgent deadline first (read only)."""
    from app.solver import service
    from app.solver.exposure import exposure
    from app.solver.tasks import derive_tasks

    state, source = service.get_state()
    current, _ = service.ensure_plans(state, source)
    tasks = sorted(derive_tasks(state), key=lambda t: (_aware(t.deadline), t.id))
    cur = {a.task_id: a for a in current.assignments}
    expo = {r["task_id"]: r for r in exposure(state, tasks, current)}
    orders = workorders.list_orders()
    rows = []
    for t in tasks:
        a = cur.get(t.id)
        wo = _order_for(t.id, [w for w in orders if w.tail == t.tail])
        e = expo.get(t.id) or {}
        rows.append({
            "task_id": t.id, "tail": t.tail, "title": t.title, "component_id": t.component_id,
            "component": _component_name(t.component_id), "source": t.source, "deadline": _iso(t.deadline),
            "duration_h": t.duration_h, "skill": t.required_skill, "part": t.required_part,
            "station": a.station if a else None, "start": _iso(a.start) if a else None, "end": _iso(a.end) if a else None,
            "technician_id": a.technician_id if a else None, "part_source": a.part_source if a else None,
            "scheduled": a is not None, "slack_to_deadline_h": e.get("slack_to_deadline_h"),
            "work_order_id": wo.id if wo else None, "work_order_status": wo.status if wo else None,
            "approved_slot_moved": bool(wo and wo.status == "approved" and a and (a.station != wo.station
                                      or abs((_aware(a.start) - _aware(wo.start)).total_seconds()) > 60)),
        })
    return {"now": _iso(state.now), "plan_id": current.id, "rows": rows}
