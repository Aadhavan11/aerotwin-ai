"""replan(state, prior, event) -> ReplanResult: the "self-adjusting plan" loop (CONTRACTS.md 4.3).

    apply the event (pure)  ->  re-derive tasks  ->  solve(previous=prior)  ->  diff (moved / added / dropped, with why)

``previous=prior`` adds a small penalty for every changed assignment, so the new plan changes only what the event forces
(or what is clearly cheaper). If nothing is feasible, ``after.feasible`` is False with ``infeasible_reasons`` and the plan
is NEVER silently relaxed: deadlines stay hard. Whatever the least-bad option is must be proposed to a human, not applied.

Extra optional arguments (the three-argument call from CONTRACTS.md still works):
  ``already_applied=True``  the event is already inside ``state`` (the platform wrote it to the DB): do not apply it twice.
"""
from __future__ import annotations

import time
from typing import Optional

from app.contracts.models import (
    EvidenceRef, FleetState, ObjectiveWeights, Plan, PlanDiff, PlanMetrics, ReplanResult, ScenarioEvent, SolveMode,
)
from app.solver.common import aware, fmt
from app.solver.engine import solve
from app.solver.plan_metrics import diff_counts, validate_plan
from app.solver.tasks import derive_tasks
from app.whatif.events import EventError, apply_event, describe_event


def make_diff(state_after: FleetState, tasks_after: list, prior: Plan, after: Plan, event: ScenarioEvent,
              max_ext_h: float = 3.0) -> PlanDiff:
    moved_pairs, added, dropped = diff_counts(prior, after.assignments)
    what = describe_event(event)
    task_by_id = {t.id: t for t in tasks_after}

    moved = []
    for old, new in moved_pairs:
        why = f"{what}. "
        task = task_by_id.get(old.task_id)
        problems = validate_plan(state_after, [task], [old], max_ext_h) if task else ["task no longer exists"]
        if problems:
            why += f"The previous slot ({old.station} {fmt(old.start)}) is no longer feasible: {problems[0]}. "
        else:
            why += (f"The previous slot ({old.station} {fmt(old.start)}) is still feasible, but the new slot is cheaper "
                    f"overall once the event is taken into account. ")
        why += f"New slot: {new.reason}"
        moved.append({
            "task_id": old.task_id,
            "from": {"station": old.station, "start": aware(old.start).isoformat()},
            "to": {"station": new.station, "start": aware(new.start).isoformat()},
            "why": why,
        })
    return PlanDiff(
        moved=moved,
        added=[a.task_id for a in added],
        dropped=[a.task_id for a in dropped],   # in the old plan, not in the new one: no longer needed OR no longer feasible
    )


def replan(
    state: FleetState,
    prior: Plan,
    event: ScenarioEvent,
    weights: Optional[ObjectiveWeights] = None,
    time_limit_s: float = 5.0,
    already_applied: bool = False,
    mode: SolveMode = "cpsat",
) -> ReplanResult:
    t0 = time.perf_counter()
    try:
        if already_applied:
            state2, effects = state, ["event already applied to the snapshot; not applied again"]
        else:
            state2, effects = apply_event(state, event)
    except EventError as exc:
        return _invalid(state, prior, event, str(exc), t0)

    tasks2 = derive_tasks(state2)
    after = solve(state2, tasks2, weights, mode, time_limit_s, previous=prior)
    after = after.model_copy(update={"evidence": list(after.evidence) + [
        EvidenceRef(kind="event", ref=event.id or event.type, note=e) for e in effects]})
    diff = make_diff(state2, tasks2, prior, after, event)
    return ReplanResult(event=event, before=prior, after=after, diff=diff,
                        solve_seconds=round(time.perf_counter() - t0, 4))


def _invalid(state: FleetState, prior: Plan, event: ScenarioEvent, message: str, t0: float) -> ReplanResult:
    """A malformed event: report it as an infeasible result instead of raising (CONTRACTS.md: errors are results)."""
    metrics: PlanMetrics = prior.metrics
    after = Plan(id=f"{prior.id}-invalid", created=aware(state.now), mode=prior.mode, feasible=False,
                 infeasible_reasons=[f"event rejected: {message}; the current plan is unchanged"],
                 assignments=list(prior.assignments), metrics=metrics, objective_value=prior.objective_value,
                 solve_seconds=0.0)
    return ReplanResult(event=event, before=prior, after=after, diff=PlanDiff(moved=[], added=[], dropped=[]),
                        solve_seconds=round(time.perf_counter() - t0, 4))
