"""least_bad: when a task cannot meet its HARD deadline, what is the earliest slot that exists at all?

This is a SUGGESTION FOR A HUMAN, never a plan: the scheduler does not schedule past a deadline (CONTRACTS.md section 1). It tells an
authorized person how late the least-bad option would be, so they can decide (e.g. ground the aircraft, or approve an exception).
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from app.contracts.models import FleetState, Task
from app.solver.candidates import SolveContext
from app.solver.common import aware, at_min


def least_bad(state: FleetState, task: Task, max_ext_h: float = 3.0) -> Optional[dict]:
    now = aware(state.now)
    last_arrival = max([aware(l.sched_arr) for l in state.legs if l.tail == task.tail] + [now])
    relaxed = task.model_copy(update={"deadline": max(last_arrival, aware(task.deadline)) + timedelta(days=1)})
    ctx = SolveContext(state, [relaxed], max_ext_h=max_ext_h)
    cands = ctx.candidates(relaxed)
    if not cands:
        return None
    best = min(cands, key=lambda c: (c.lo + c.dur, c.opt.cost_usd, c.station, c.tech_id))
    start, end = best.lo, best.lo + best.dur
    late_h = (end - ctx.deadline_min(task)) / 60.0
    return {
        "station": best.station, "start": at_min(now, start), "end": at_min(now, end), "technician_id": best.tech_id,
        "part_source": best.opt.source, "extension_h": round(best.ext_min(start) / 60.0, 2),
        "hours_after_deadline": round(max(late_h, 0.0), 1),
        "note": "NOT a plan: this slot is after the hard deadline and needs an authorized human decision; the system will not schedule it",
    }
