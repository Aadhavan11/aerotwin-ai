"""Work orders: drafted from a plan, decided by a person, audited (feature 3.6 backend).

Rules (SCOPE.md section 8, INTEGRATION.md section 5):

* Drafts are always ``pending_approval``. Only a person can approve or reject:
  the decider must be a named user, never ``system`` or ``agent:*``.
* Every draft, decision, supersession and completion writes an audit entry.
* A pending order is superseded when a newer plan changes that task. An
  already **approved** order is never replaced by a later slot: a new draft is
  made only when the new slot is earlier.
* Completion (``complete_due``) is what resets the twin: when simulated time
  passes the end of an approved window, the component is marked maintained.
"""
from __future__ import annotations

import re
from datetime import datetime
from threading import RLock
from typing import Iterable, Optional

from app.contracts.models import EvidenceRef, Plan, WorkOrder
from app.copilot import audit, kb
from app.ml.sensors import kind_from_component_id

_LOCK = RLock()
_ORDERS: dict[str, WorkOrder] = {}
_COMPLETED: set[str] = set()
_SEQ = 0
_TASK_RE = re.compile(r"^T-(SIM-\d+)-(.+)-\d+$")


class WorkOrderError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def reset() -> None:
    global _SEQ
    with _LOCK:
        _ORDERS.clear()
        _COMPLETED.clear()
        _SEQ = 0


def component_of(task_id: str) -> Optional[tuple[str, str]]:
    """``T-SIM-017-engine_2-001`` -> (``SIM-017``, ``SIM-017/engine_2``)."""
    m = _TASK_RE.match(task_id)
    if not m:
        return None
    tail, comp = m.group(1), m.group(2)
    return tail, f"{tail}/{comp}"


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%MZ")


def _next_id() -> str:
    global _SEQ
    _SEQ += 1
    return f"WO-{_SEQ}"


def list_orders(status: Optional[str] = None) -> list[WorkOrder]:
    with _LOCK:
        out = list(_ORDERS.values())
    return [w for w in out if status is None or w.status == status]


def get_order(work_order_id: str) -> Optional[WorkOrder]:
    with _LOCK:
        return _ORDERS.get(work_order_id)


def draft_from_plan(plan: Plan, *, actor: str = "agent:coordinator", tails: Optional[Iterable[str]] = None
                    ) -> tuple[list[WorkOrder], list[str]]:
    """Draft one pending order per assignment. Returns (orders for this plan, notes)."""
    wanted = set(tails) if tails else None
    out: list[WorkOrder] = []
    notes: list[str] = []
    with _LOCK:
        for a in plan.assignments:
            if wanted is not None and a.tail not in wanted:
                continue
            same_task = [w for w in _ORDERS.values() if w.task_id == a.task_id]
            approved = [w for w in same_task if w.status == "approved"]
            if approved and not any(a.start < w.start for w in approved):
                out.append(approved[0])
                notes.append(f"{approved[0].id} for {a.task_id} is already approved and not later than plan {plan.id}; kept")
                continue
            for w in same_task:
                if w.status == "pending_approval" and w.plan_id != plan.id:
                    _ORDERS[w.id] = w.model_copy(update={
                        "status": "rejected", "decided_by": "system", "note": f"superseded by plan {plan.id}"})
                    audit.record("system", "work_order_superseded", w.id, f"replaced by plan {plan.id}")
            dup = [w for w in same_task if w.status == "pending_approval" and w.plan_id == plan.id]
            if dup:
                out.append(dup[0])
                continue
            comp = component_of(a.task_id)
            kind = kind_from_component_id(comp[1]) if comp else None
            card = kb.default_card_for(kind) if kind else None
            title = card["title"] if card else "Maintenance task"
            tech = a.technician_id or "technician to be assigned"
            evidence = [EvidenceRef(kind="plan", ref=plan.id, note=a.reason)]
            if comp:
                evidence.append(EvidenceRef(kind="health", ref=comp[1], note="component state when the plan was made"))
            if card:
                evidence.append(EvidenceRef(kind="kb", ref=card["id"], note="synthetic task card"))
            wo = WorkOrder(
                id=_next_id(), tail=a.tail, task_id=a.task_id, station=a.station, start=a.start, end=a.end,
                description=f"{title} on {a.tail} at {a.station}, {_iso(a.start)} to {_iso(a.end)}, {tech}.",
                status="pending_approval", plan_id=plan.id, evidence=evidence,
            )
            _ORDERS[wo.id] = wo
            audit.record(actor, "work_order_drafted", wo.id, f"{a.task_id} at {a.station} from plan {plan.id}")
            out.append(wo)
    return out, notes


def decide(work_order_id: str, decision: str, user: str, note: str = "", at: Optional[datetime] = None) -> WorkOrder:
    """Approve or reject. Raises ``WorkOrderError`` for unknown id, bad actor, bad decision or a repeat decision."""
    who = (user or "").strip()
    if not who:
        raise WorkOrderError("invalid_actor", "a name is required to approve or reject", 422)
    if who.lower() == "system" or who.lower().startswith("agent"):
        raise WorkOrderError("invalid_actor", "only a person can approve or reject a work order", 422)
    if decision not in ("approve", "reject"):
        raise WorkOrderError("invalid_decision", "decision must be 'approve' or 'reject'", 422)
    with _LOCK:
        wo = _ORDERS.get(work_order_id)
        if wo is None:
            raise WorkOrderError("not_found", f"no work order {work_order_id!r}", 404)
        if wo.status != "pending_approval":
            raise WorkOrderError("already_decided", f"{work_order_id} is already {wo.status}", 409)
        when = at or audit._now()
        new = wo.model_copy(update={
            "status": "approved" if decision == "approve" else "rejected",
            "decided_by": who, "decided_at": when, "note": note or "",
        })
        _ORDERS[work_order_id] = new
    audit.record(who, f"work_order_{'approved' if decision == 'approve' else 'rejected'}", work_order_id,
                 note or "", when)
    from app.contracts.models import TwinEvent
    from app.twin import state as twin

    twin.add_timeline_event(new.tail, TwinEvent(
        ts=when, kind="plan_change",
        summary=f"Work order {new.id} {new.status} by {who}: {new.station} {_iso(new.start)}",
        ref=new.id))
    return new


def complete_due(now: datetime) -> list[WorkOrder]:
    """Mark approved orders whose window has ended as done and reset the twin component.

    Call this after advancing the simulation clock (``POST /sim/step``).
    """
    from app.twin import state as twin

    done: list[WorkOrder] = []
    with _LOCK:
        due = [w for w in _ORDERS.values() if w.status == "approved" and w.end <= now and w.id not in _COMPLETED]
        for w in due:
            _COMPLETED.add(w.id)
            done.append(w)
    for w in done:
        comp = component_of(w.task_id)
        if comp:
            twin.apply_maintenance(comp[0], comp[1], w.end)
        audit.record("system", "work_order_completed", w.id, f"{w.task_id} finished at {w.station}", w.end)
    return done
