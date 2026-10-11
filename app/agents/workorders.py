"""Draft work orders from a plan. Always ``pending_approval``: nothing here approves anything (a human clicks Approve elsewhere)."""
from __future__ import annotations

from app.agents.kb import card_for_title
from app.agents.runs import RUNS
from app.contracts.models import EvidenceRef, FleetState, Plan, Task, WorkOrder
from app.solver.common import fmt


def draft(plan: Plan, state: FleetState, tasks: list[Task]) -> list[WorkOrder]:
    by_id = {t.id: t for t in tasks}
    orders: list[WorkOrder] = []
    for a in sorted(plan.assignments, key=lambda x: (x.start, x.task_id)):
        t = by_id.get(a.task_id)
        evidence = [EvidenceRef(kind="plan", ref=plan.id, note=f"{plan.mode} plan, objective {plan.objective_value}")]
        desc = f"{t.title if t else a.task_id} on {a.tail} at {a.station}, {fmt(a.start)} to {fmt(a.end)}"
        if t is not None:
            card = card_for_title(t.title)
            if card is not None:
                evidence.append(EvidenceRef(kind="kb", ref=card.id, note="synthetic task card"))
            evidence.append(EvidenceRef(kind="health", ref=t.component_id, note=t.basis))
            desc += f". Why: {t.basis}"
        desc += f". Technician {a.technician_id}" + (f"; part shipped from {a.part_source}" if a.part_source else "") + ". Advisory only; needs human approval."
        orders.append(WorkOrder(id=RUNS.next_work_order_id(), tail=a.tail, task_id=a.task_id, station=a.station, start=a.start,
                                end=a.end, description=desc, status="pending_approval", plan_id=plan.id, evidence=evidence))
    return orders
