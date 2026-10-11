"""replan_and_store: the one place that replans the CURRENT plan and keeps the world consistent.

Used by POST /plan/replan and by the agents' ``replan_with_event`` tool, so both see the same disruptions.
Raises EventError for a malformed event (the caller turns it into a 422 or a tool error); nothing is changed in that case.
"""
from __future__ import annotations

from typing import Optional

from app.contracts.models import ObjectiveWeights, ReplanResult, ScenarioEvent
from app.solver import service
from app.solver.store import STORE
from app.whatif.events import apply_event
from app.whatif.replan import replan


def replan_and_store(event: ScenarioEvent, already_applied: bool = False,
                     weights: Optional[ObjectiveWeights] = None) -> ReplanResult:
    state, source = service.get_state()
    if not already_applied:
        apply_event(state, event)          # raises EventError before anything is touched
    with service.SOLVE_LOCK:
        prior, _ = service.ensure_plans(state, source)
        result = replan(state, prior, event, weights, already_applied=already_applied)
        STORE.save(result.after)
        STORE.last_replan_seconds = result.solve_seconds
        if not already_applied:
            STORE.world_events.append(event)    # the snapshot is not written back, so remember the disruption
        # recompute the baseline in the SAME changed world so "baseline vs optimized" stays an honest comparison
        changed, _ = service.get_state()
        service.solve_and_save(changed, source, "static_baseline")
    return result
