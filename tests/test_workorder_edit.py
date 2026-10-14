"""Editing a maintenance request before it is decided: who may, what, and that every change is audited."""
from datetime import datetime, timezone

import pytest

from app.contracts.models import EvidenceRef, WorkOrder
from app.copilot import audit, persist, workorders

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def memory_backend():
    persist.use_memory() if hasattr(persist, "use_memory") else None
    persist._OVERRIDE = False
    workorders.reset()
    audit.reset() if hasattr(audit, "reset") else None
    yield
    persist._OVERRIDE = None


def make(status="pending_approval") -> WorkOrder:
    wo = WorkOrder(id="WO-1", tail="SIM-001", task_id="T-SIM-001-engine_1-001", station="AAA", start=NOW, end=NOW.replace(hour=14),
                   description="d", status=status, plan_id="P-1", evidence=[EvidenceRef(kind="plan", ref="P-1", note="")],
                   title="Engine borescope inspection", part="BORESCOPE-KIT", part_qty=1)
    workorders._save(wo)
    return wo


def test_name_work_part_and_order_flag_can_be_edited_and_are_audited():
    make()
    new = workorders.edit("WO-1", "Maya", title="Borescope and oil check", work="Inspect HPT blades, check chip detector", part="HYD-PUMP", part_qty=2, order_part=True)
    assert (new.title, new.part, new.part_qty, new.order_part, new.edited_by) == ("Borescope and oil check", "HYD-PUMP", 2, True, "Maya")
    assert new.work.startswith("Inspect HPT")
    entry = [e for e in audit.list_entries() if e.action == "work_order_edited"][-1]
    assert entry.actor == "Maya" and "part: BORESCOPE-KIT -> HYD-PUMP" in entry.detail


def test_only_a_named_person_may_edit_and_only_while_pending():
    make()
    with pytest.raises(workorders.WorkOrderError) as e:
        workorders.edit("WO-1", "agent:coordinator", title="x")
    assert e.value.status == 422
    workorders.decide("WO-1", "approve", "Maya")
    with pytest.raises(workorders.WorkOrderError) as e:
        workorders.edit("WO-1", "Maya", title="too late")
    assert e.value.status == 409


def test_an_edit_that_changes_nothing_is_not_recorded():
    make()
    before = len(audit.list_entries())
    workorders.edit("WO-1", "Maya", title="Engine borescope inspection", order_part=False)
    assert len(audit.list_entries()) == before
