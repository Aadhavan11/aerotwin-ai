"""Spare-part repositioning: advise moving stock BEFORE a shortage hits the critical path (doc stretch goal; advisory only).

A plan made on a snapshot already ships a part the moment it is needed. The forward-looking question is different: where will parts be
needed soon, and does the stock sit there? We forecast demand per home station from every component whose conservative RUL (p10) is
below ``horizon_cycles`` (a wider net than the planning threshold), compare it with what is on hand there, and suggest moving surplus
from the donor named in the station's own stock record (``ship_from``, with its ``ship_hours`` and ``ship_cost_usd``).

Nothing here moves a part or books a cost; a human decides. Numbers are copied from the stock records and the task list.
"""
from __future__ import annotations

from collections import defaultdict

from app.contracts.models import FleetState
from app.solver.tasks import derive_tasks_ex


def suggest_repositioning(state: FleetState, horizon_cycles: float = 120.0) -> list[dict]:
    home = {a.tail: a.home for a in state.aircraft}
    tasks = derive_tasks_ex(state, rul_threshold=horizon_cycles, max_scheduled=0).tasks
    demand: dict[tuple[str, str], int] = defaultdict(int)
    who: dict[tuple[str, str], list[str]] = defaultdict(list)
    for t in tasks:
        if t.required_part and t.part_qty > 0 and t.tail in home:
            key = (home[t.tail], t.required_part)
            demand[key] += t.part_qty
            who[key].append(t.id)
    stock = {(s.iata, p.part): p for s in state.stations for p in s.parts if p.eta_hours <= 0}   # on hand now
    on_hand = {k: p.qty for k, p in stock.items()}
    demand_total: dict[str, int] = defaultdict(int)
    for (stn, part), n in demand.items():
        demand_total[part] += n

    # surplus per donor station = stock minus the demand of aircraft homed there
    surplus = {k: on_hand.get(k, 0) - demand.get(k, 0) for k in on_hand}
    out: list[dict] = []
    for (stn, part), need in sorted(demand.items()):
        have = on_hand.get((stn, part), 0)
        short = need - have
        if short <= 0:
            continue
        rec = next((s for s in state.stations if s.iata == stn), None)
        ps = next((p for p in rec.parts if p.part == part), None) if rec else None
        donor = ps.ship_from if ps else None
        if not ps or not donor or surplus.get((donor, part), 0) <= 0:
            out.append({"kind": "shortage_no_donor", "part": part, "to_station": stn, "from_station": donor, "qty": short,
                        "demand": need, "on_hand": have, "task_ids": who[(stn, part)],
                        "benefit": f"{stn} may need {need} {part} for aircraft homed there but holds {have}, and no station has a surplus to send",
                        "note": "Advisory: nothing was moved"})
            continue
        qty = min(short, surplus[(donor, part)])
        surplus[(donor, part)] -= qty
        out.append({"kind": "reposition", "part": part, "from_station": donor, "to_station": stn, "qty": qty,
                    "ship_hours": ps.ship_hours, "cost_usd": round(ps.ship_cost_usd * qty, 2), "demand": need, "on_hand": have,
                    "task_ids": who[(stn, part)],
                    "benefit": (f"{stn} may need {need} {part} for aircraft homed there but holds {have}; moving {qty} from {donor} now "
                                f"takes {ps.ship_hours:g} h and costs ${ps.ship_cost_usd * qty:,.0f}, instead of waiting that long when the job is due"),
                    "note": "Advisory: nothing was moved or booked; a human decides"})
    return out
