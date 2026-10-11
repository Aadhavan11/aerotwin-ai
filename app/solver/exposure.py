"""Exposure: how long a condition-driven fault stays open, and how much margin is left before its hard deadline.

Cost metrics (downtime, flights delayed, dollars) can tie between two plans while the plans differ a lot in RISK: one fixes the engine
tomorrow, the other at the last legal moment. These numbers make that visible. They are plain functions of a plan and the legs
(no model change, nothing invented):

* ``hours_to_fix``          hours from now until the job ends;
* ``flights_before_fix``    scheduled departures of that aircraft between now and the start of the job (flights flown with the fault open);
* ``slack_to_deadline_h``   hours between the end of the job and the task's hard deadline (the safety margin).
"""
from __future__ import annotations

from typing import Optional

from app.contracts.models import FleetState, Plan, Task
from app.solver.common import aware, minutes_between


def exposure(state: FleetState, tasks: list[Task], plan: Plan) -> list[dict]:
    """One row per condition-driven (RUL) task, most urgent deadline first. Unscheduled tasks get ``scheduled: False``."""
    now = aware(state.now)
    by_task = {a.task_id: a for a in plan.assignments}
    rows: list[dict] = []
    for t in sorted((t for t in tasks if t.source == "rul"), key=lambda t: (aware(t.deadline), t.id)):
        a = by_task.get(t.id)
        row = {"task_id": t.id, "tail": t.tail, "component_id": t.component_id, "scheduled": a is not None}
        if a is not None:
            start = aware(a.start)
            row.update({
                "station": a.station,
                "hours_to_fix": round(minutes_between(a.end, now) / 60.0, 1),
                "flights_before_fix": sum(1 for l in state.legs if l.tail == t.tail and now <= aware(l.sched_dep) < start),
                "slack_to_deadline_h": round(minutes_between(t.deadline, a.end) / 60.0, 1),
            })
        rows.append(row)
    return rows


def headline(rows: list[dict]) -> Optional[dict]:
    """The most urgent condition-driven task."""
    return rows[0] if rows else None
