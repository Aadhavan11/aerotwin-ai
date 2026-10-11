"""Draft work orders from a plan. Always ``pending_approval``: nothing here approves anything (a human clicks Approve elsewhere)."""
from __future__ import annotations

from app.contracts.models import FleetState, Plan, Task, WorkOrder
from app.copilot import workorders as _store


def draft(plan: Plan, state: FleetState, tasks: list[Task]) -> list[WorkOrder]:
    """Draft in the shared work-order store (``app.copilot.workorders``), so ``GET /workorders`` and the Approve button
    see the same orders and ids never collide. Always ``pending_approval``; a newer plan supersedes an older pending draft."""
    return _store.draft(plan, state, tasks)
