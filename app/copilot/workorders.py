"""Work orders: drafted from a plan, decided by a person, audited (feature 3.6 backend).

One store for every source (the copilot chat and the agent runs both draft
here), so ``GET /workorders`` and the Approve button see the same orders.

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

import json
import re
from datetime import datetime
from threading import RLock
from typing import Any, Iterable, Optional

from app.contracts.models import EvidenceRef, Plan, TwinEvent, WorkOrder
from app.copilot import audit, kb, persist
from app.ml.sensors import kind_from_component_id

_LOCK = RLock()
_ORDERS: dict[str, WorkOrder] = {}          # memory backend
_COMPLETED: set[str] = set()                # memory backend
_SEQ = 0                                    # memory backend
_TASK_RE = re.compile(r"^T-(SIM-\d+)-(.+)-\d+$")
_COMPLETED_KEY = "work_orders_completed"


class WorkOrderError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# --------------------------------------------------------------------------
# Storage (memory or shared DB)
# --------------------------------------------------------------------------
def _num(work_order_id: str) -> int:
    m = re.match(r"WO-(\d+)$", work_order_id)
    return int(m.group(1)) if m else 10 ** 9


def _load_all() -> list[WorkOrder]:
    if persist.enabled():
        with persist.conn() as c:
            rows = c.execute("SELECT json FROM work_orders").fetchall()
        orders = [WorkOrder.model_validate_json(r["json"]) for r in rows]
        # INSERT OR REPLACE moves an updated row to the end of the table: order by number, not by storage.
        return sorted(orders, key=lambda w: (_num(w.id), w.id))
    return list(_ORDERS.values())


def _save(wo: WorkOrder) -> None:
    if persist.enabled():
        with persist.conn() as c:
            c.execute("INSERT OR REPLACE INTO work_orders(id,status,json) VALUES(?,?,?)",
                      (wo.id, wo.status, wo.model_dump_json()))
    else:
        _ORDERS[wo.id] = wo


def _new_id() -> str:
    global _SEQ
    if persist.enabled():
        nums = [int(m.group(1)) for w in _load_all() if (m := re.match(r"WO-(\d+)$", w.id))]
        return f"WO-{max(nums, default=0) + 1}"
    _SEQ += 1
    return f"WO-{_SEQ}"


def _completed() -> set[str]:
    if persist.enabled():
        with persist.conn() as c:
            r = c.execute("SELECT value FROM kv WHERE key=?", (_COMPLETED_KEY,)).fetchone()
        return set(json.loads(r["value"])) if r else set()
    return set(_COMPLETED)


def _mark_completed(ids: Iterable[str]) -> None:
    ids = list(ids)
    if persist.enabled():
        done = _completed() | set(ids)
        with persist.conn() as c:
            c.execute("INSERT OR REPLACE INTO kv(key,value) VALUES(?,?)", (_COMPLETED_KEY, json.dumps(sorted(done))))
    else:
        _COMPLETED.update(ids)


def reset() -> None:
    """Clear the in-memory store. Database rows belong to the owner of the scenario reset."""
    global _SEQ
    with _LOCK:
        _ORDERS.clear()
        _COMPLETED.clear()
        _SEQ = 0


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------
def component_of(task_id: str) -> Optional[tuple[str, str]]:
    """``T-SIM-017-engine_2-001`` -> (``SIM-017``, ``SIM-017/engine_2``)."""
    m = _TASK_RE.match(task_id)
    if not m:
        return None
    tail, comp = m.group(1), m.group(2)
    return tail, f"{tail}/{comp}"


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%MZ")


def list_orders(status: Optional[str] = None) -> list[WorkOrder]:
    with _LOCK:
        out = _load_all()
    return [w for w in out if status is None or w.status == status]


def get_order(work_order_id: str) -> Optional[WorkOrder]:
    return next((w for w in list_orders() if w.id == work_order_id), None)


# --------------------------------------------------------------------------
# Drafting
# --------------------------------------------------------------------------
def _describe(a: Any, task: Any) -> str:
    when = f"{_iso(a.start)} to {_iso(a.end)}"
    tech = a.technician_id or "technician to be assigned"
    if task is not None:
        text = f"{task.title} on {a.tail} at {a.station}, {when}. Why: {task.basis}. Technician {tech}"
    else:
        comp = component_of(a.task_id)
        card = kb.default_card_for(kind_from_component_id(comp[1])) if comp else None
        title = card["title"] if card else "Maintenance task"
        text = f"{title} on {a.tail} at {a.station}, {when}. Technician {tech}"
    if a.part_source:
        text += f"; part shipped from {a.part_source}"
    return text + ". Advisory only; needs human approval."


def draft_from_plan(plan: Plan, *, actor: str = "agent:coordinator", tails: Optional[Iterable[str]] = None,
                    tasks: Optional[Iterable[Any]] = None) -> tuple[list[WorkOrder], list[str]]:
    """Draft one pending order per assignment. Returns (orders for this plan, notes).

    ``tasks`` (optional ``Task`` objects) enrich the description and evidence
    with the task title and the stated basis (the RUL reasoning).
    """
    wanted = set(tails) if tails else None
    by_id = {t.id: t for t in (tasks or [])}
    out: list[WorkOrder] = []
    notes: list[str] = []
    with _LOCK:
        existing = _load_all()
        for a in sorted(plan.assignments, key=lambda x: (x.start, x.task_id)):
            if wanted is not None and a.tail not in wanted:
                continue
            same_task = [w for w in existing if w.task_id == a.task_id]
            approved = [w for w in same_task if w.status == "approved"]
            if approved and not any(a.start < w.start for w in approved):
                keep = approved[0]
                out.append(keep)
                if a.start > keep.start or a.station != keep.station:
                    # The plan has moved this task away from what a person approved. Never move an approval
                    # silently: keep it, say so, and leave the decision to a person.
                    notes.append(
                        f"{keep.id} for {a.task_id} is already approved for {keep.station} {_iso(keep.start)}, but plan {plan.id} "
                        f"now places the task at {a.station} {_iso(a.start)}. The approved slot may no longer be feasible; "
                        f"a person should review it. Kept as approved.")
                    marker = f"plan {plan.id}"
                    if not any(e.action == "work_order_review_needed" and e.ref == keep.id and marker in e.detail
                               for e in audit.list_entries()):
                        audit.record("system", "work_order_review_needed", keep.id,
                                     f"{marker} places {a.task_id} at {a.station} {_iso(a.start)}; approved for {keep.station} {_iso(keep.start)}")
                else:
                    notes.append(f"{keep.id} for {a.task_id} is already approved and matches plan {plan.id}; kept")
                continue
            for w in same_task:
                if w.status == "pending_approval" and w.plan_id != plan.id:
                    sup = w.model_copy(update={"status": "rejected", "decided_by": "system",
                                               "note": f"superseded by plan {plan.id}"})
                    _save(sup)
                    existing = [sup if x.id == w.id else x for x in existing]
                    audit.record("system", "work_order_superseded", w.id, f"replaced by plan {plan.id}")
            dup = [w for w in existing if w.task_id == a.task_id and w.status == "pending_approval" and w.plan_id == plan.id]
            if dup:
                out.append(dup[0])
                continue
            task = by_id.get(a.task_id)
            comp = component_of(a.task_id)
            evidence = [EvidenceRef(kind="plan", ref=plan.id, note=a.reason)]
            if task is not None:
                evidence.append(EvidenceRef(kind="health", ref=task.component_id, note=task.basis))
                card = kb.card_for_title(task.title)
            else:
                if comp:
                    evidence.append(EvidenceRef(kind="health", ref=comp[1], note="component state when the plan was made"))
                card = kb.default_card_for(kind_from_component_id(comp[1])) if comp else None
            if card:
                evidence.append(EvidenceRef(kind="kb", ref=card["id"], note="synthetic task card"))
            wo = WorkOrder(
                id=_new_id(), tail=a.tail, task_id=a.task_id, station=a.station, start=a.start, end=a.end,
                description=_describe(a, task), status="pending_approval", plan_id=plan.id, evidence=evidence,
            )
            _save(wo)
            existing.append(wo)
            audit.record(actor, "work_order_drafted", wo.id, f"{a.task_id} at {a.station} from plan {plan.id}")
            out.append(wo)
    return out, notes


def draft(plan: Plan, state: Any = None, tasks: Optional[Iterable[Any]] = None) -> list[WorkOrder]:
    """Same call shape as the agent registry's ``workorders.draft(plan, state, tasks)``."""
    orders, _ = draft_from_plan(plan, tasks=tasks)
    return orders


# --------------------------------------------------------------------------
# Deciding and completing
# --------------------------------------------------------------------------
def decide(work_order_id: str, decision: str, user: str, note: str = "", at: Optional[datetime] = None) -> WorkOrder:
    """Approve or reject. Raises ``WorkOrderError`` for unknown id, bad actor, bad decision or a repeat decision."""
    who = (user or "").strip()
    if not who or who.lower() in ("system", "unknown") or who.lower().startswith("agent"):
        raise WorkOrderError("invalid_actor", "a person's name is required to approve or reject", 422)
    if decision not in ("approve", "reject"):
        raise WorkOrderError("invalid_decision", "decision must be 'approve' or 'reject'", 422)
    with _LOCK:
        wo = get_order(work_order_id)
        if wo is None:
            raise WorkOrderError("not_found", f"no work order {work_order_id!r}", 404)
        if wo.status != "pending_approval":
            raise WorkOrderError("already_decided", f"{work_order_id} is already {wo.status}", 409)
        when = at or persist.sim_now()
        new = wo.model_copy(update={
            "status": "approved" if decision == "approve" else "rejected",
            "decided_by": who, "decided_at": when, "note": note or "",
        })
        _save(new)
    audit.record(who, f"work_order_{new.status}", work_order_id, note or "", when)
    from app.twin import state as twin

    twin.add_timeline_event(new.tail, TwinEvent(
        ts=when, kind="plan_change",
        summary=f"Work order {new.id} {new.status} by {who}: {new.station} {_iso(new.start)}", ref=new.id))
    return new


def complete_due(now: datetime) -> list[WorkOrder]:
    """Mark approved orders whose window has ended as done and reset the twin component.

    Call this after advancing the simulation clock (``POST /sim/step``).
    """
    from app.twin import state as twin

    with _LOCK:
        finished = _completed()
        due = [w for w in _load_all() if w.status == "approved" and w.end <= now and w.id not in finished]
        _mark_completed(w.id for w in due)
    for w in due:
        comp = component_of(w.task_id)
        if comp:
            twin.apply_maintenance(comp[0], comp[1], w.end)
        audit.record("system", "work_order_completed", w.id, f"{w.task_id} finished at {w.station}", w.end)
    return due
