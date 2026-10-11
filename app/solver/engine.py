"""solve(): one entry point for the three solver modes (CONTRACTS.md section 5).

    solve(state, tasks, weights, mode, time_limit_s=5.0, previous=None) -> Plan

Extra optional keywords (backwards compatible): ``force_station`` (what-if "service at another airport": only that station's
windows are used; ``force_tails`` limits it to those aircraft, default all), ``max_extension_h``, ``earliness_per_h``.

Never raises for expected failures: an infeasible problem returns ``Plan(feasible=False, infeasible_reasons=[...])``.
If CP-SAT cannot return a solution, greedy is used and the plan says so (``mode='greedy'`` and an evidence note).
Every plan is re-validated against the hard constraints before it is returned.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

from app.contracts.models import (
    Assignment, EvidenceRef, FleetState, ObjectiveWeights, Plan, SolveMode, Task,
)
from app.solver import baseline, greedy
from app.solver.candidates import SolveContext
from app.solver.common import DEFAULT_HORIZON_H, DEFAULT_MAX_EXT_H
from app.solver.plan_metrics import EARLINESS_PER_H, compute_metrics, plan_objective, validate_plan
from app.solver.reasons import Placement, make_assignment
from app.solver.store import STORE

log = logging.getLogger(__name__)


def solve(
    state: FleetState,
    tasks: list[Task],
    weights: Optional[ObjectiveWeights] = None,
    mode: SolveMode = "cpsat",
    time_limit_s: float = 5.0,
    previous: Optional[Plan] = None,
    force_station: Optional[str] = None,
    force_tails: Optional[set[str]] = None,
    max_extension_h: float = DEFAULT_MAX_EXT_H,
    earliness_per_h: float = EARLINESS_PER_H,
) -> Plan:
    t0 = time.perf_counter()
    weights = weights or ObjectiveWeights()
    ctx = SolveContext(state, tasks, force_station=force_station, max_ext_h=max_extension_h, force_tails=force_tails)
    notes: list[str] = []
    for issue in ctx.window_issues:
        log.warning("rotation data issue: %s", issue)
    if ctx.window_issues:
        notes.append(f"{len(ctx.window_issues)} rotation data issue(s): aircraft with a broken rotation have fewer ground windows")

    if mode == "cpsat" and os.getenv("MOCK_SOLVER") == "1":
        notes.append("MOCK_SOLVER=1: greedy stub used instead of CP-SAT")
        mode_run: SolveMode = "greedy"
    else:
        mode_run = mode

    placements: list[Placement]
    unscheduled: dict[str, str]
    if mode_run == "static_baseline":
        placements, unscheduled = baseline.run(ctx)
    elif mode_run == "greedy":
        placements, unscheduled = greedy.run(ctx)
    else:
        placements, unscheduled, mode_run = _run_cpsat(ctx, weights, time_limit_s, previous, earliness_per_h, notes)

    assignments = _assignments(ctx, placements, mode_run, weights)
    problems = validate_plan(state, ctx.tasks, assignments, ctx.max_ext_h, force_station, force_tails)
    if problems and mode_run == "cpsat":
        log.error("CP-SAT plan failed validation, using greedy: %s", problems[:3])
        notes.append(f"CP-SAT result failed the hard-constraint check ({problems[0]}); greedy used instead")
        mode_run = "greedy"
        placements, unscheduled = greedy.run(ctx)
        assignments = _assignments(ctx, placements, mode_run, weights)
        problems = validate_plan(state, ctx.tasks, assignments, ctx.max_ext_h, force_station, force_tails)
    reasons = list(unscheduled.values())
    if problems:  # an internal bug: say so loudly instead of returning a plan that breaks a hard rule
        log.error("plan failed validation: %s", problems)
        reasons = [f"internal check failed: {p}" for p in problems] + reasons

    metrics = compute_metrics(state, ctx.tasks, assignments, previous, DEFAULT_HORIZON_H)
    plan_id = STORE.next_plan_id()
    plan = Plan(
        id=plan_id, created=ctx.now, mode=mode_run, feasible=not unscheduled and not problems,
        infeasible_reasons=reasons, assignments=assignments, metrics=metrics,
        objective_value=plan_objective(state, metrics, assignments, weights, earliness_per_h),
        solve_seconds=round(time.perf_counter() - t0, 4),
        evidence=[EvidenceRef(kind="plan", ref=plan_id, note=n) for n in notes],
    )
    return plan


def _assignments(ctx: SolveContext, placements: list[Placement], mode: str, weights: ObjectiveWeights) -> list[Assignment]:
    ordered = sorted(placements, key=lambda p: (p.start, p.cand.task_id))
    return [make_assignment(ctx, p, mode, weights) for p in ordered]


def _run_cpsat(ctx: SolveContext, weights: ObjectiveWeights, time_limit_s: float, previous: Optional[Plan],
               earliness_per_h: float, notes: list[str]):
    try:
        from app.solver import cpsat
        hint, _ = greedy.run(ctx)
        res = cpsat.run(ctx, weights, time_limit_s, previous, earliness_per_h, hint)
    except Exception as exc:  # noqa: BLE001 (any solver/import failure must degrade to greedy, loudly)
        log.exception("CP-SAT failed")
        res, err = None, f"{type(exc).__name__}: {exc}"
        notes.append(f"CP-SAT failed ({err}); greedy used instead")
    else:
        if res is None:
            notes.append("CP-SAT returned no solution; greedy used instead")
    if res is None:
        placements, unscheduled = greedy.run(ctx)
        return placements, unscheduled, "greedy"
    notes.append(f"CP-SAT: {res.gap_note}")
    return res.placements, res.unscheduled, "cpsat"
