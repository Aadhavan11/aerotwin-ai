"""Greedy solver (L1 and the fallback if CP-SAT returns nothing).

Tasks in deadline order; for each, take the candidate (any station with skill + part + hangar) that finishes EARLIEST,
preferring placements that do not overrun the ground window. Deadlines are hard: a task that fits nowhere is reported
unscheduled with a reason, never pushed past its deadline.
"""
from __future__ import annotations

from app.solver.candidates import SolveContext
from app.solver.ledger import Ledger
from app.solver.reasons import Placement


def run(ctx: SolveContext) -> tuple[list[Placement], dict[str, str]]:
    ledger = Ledger(ctx)
    placed: list[Placement] = []
    unscheduled: dict[str, str] = {}
    for task in ctx.tasks:
        best = None
        best_key = None
        for c in ctx.candidates(task):
            s = ledger.earliest_start(c)
            if s is None:
                continue
            key = (c.ext_min(s) > 0, s + c.dur, c.opt.cost_usd, c.tech_rank, c.station, c.tech_id)
            if best_key is None or key < best_key:
                best, best_key = Placement(c, s), key
        if best is None:
            unscheduled[task.id] = ctx.diagnose(task, ledger=ledger)
        else:
            ledger.commit(best.cand, best.start)
            placed.append(best)
    return placed, unscheduled
