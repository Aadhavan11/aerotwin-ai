"""The copilot proposes changes to maintenance requests; it never applies them."""
from datetime import datetime, timezone

import pytest

from app.contracts.models import EvidenceRef, WorkOrder
from app.copilot import actions, audit, persist, workorders

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def memory_backend():
    persist._OVERRIDE = False
    workorders.reset()
    yield
    persist._OVERRIDE = None


def make(status="pending_approval") -> WorkOrder:
    wo = WorkOrder(id="WO-1", tail="SIM-001", task_id="T-SIM-001-engine_1-001", station="AAA", start=NOW, end=NOW.replace(hour=14), description="d", status=status,
                   plan_id="P-1", evidence=[EvidenceRef(kind="plan", ref="P-1", note="")], title="Engine borescope inspection", part="BORESCOPE-KIT", part_qty=1)
    workorders._save(wo)
    return wo


def test_a_rename_is_proposed_not_applied():
    make()
    text, props = actions.parse_scripted("rename WO-1 to Engine check and oil sample")
    assert props and props[0]["ok"] and props[0]["request"] == {"method": "PATCH", "path": "/workorders/WO-1", "body": {"title": "Engine check and oil sample"}}
    assert "Nothing changes until you allow it" in text
    assert workorders.get_order("WO-1").title == "Engine borescope inspection"  # untouched
    assert not [e for e in audit.list_entries() if e.action == "work_order_edited"]


def test_ordering_and_part_changes_are_understood():
    make()
    _, props = actions.parse_scripted("please order the part for WO-1")
    assert props[0]["request"]["body"] == {"order_part": True}
    _, props = actions.parse_scripted("change the part of WO-1 to HYD-PUMP")
    assert props[0]["request"]["body"]["part"] == "HYD-PUMP"


def test_this_request_uses_the_one_on_screen():
    make()
    _, props = actions.parse_scripted("rename this request to Cabin check", "Maintenance request · WO-1 · Waiting for a decision")
    assert props and props[0]["request"]["path"] == "/workorders/WO-1"


def test_an_approved_or_missing_request_cannot_be_changed():
    make("approved")
    text, props = actions.parse_scripted("rename WO-1 to x")
    assert props == [] or not props[0]["ok"]
    assert "already approved" in text or "already approved" in " ".join(props[0]["problems"])
    text, props = actions.parse_scripted("rename WO-9 to x")
    assert "no work order WO-9" in text


def test_ordinary_questions_are_not_actions():
    assert actions.parse_scripted("Which aircraft need attention?") is None
    assert actions.parse_scripted("Why is SIM-017 prioritized over SIM-012?") is None
