"""Plain-language reasons for assignments (Assignment.reason) and the Assignment builder.

Every number in a reason is computed here from the plan data; nothing is invented by an LLM.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.contracts.models import Assignment, ObjectiveWeights
from app.solver.candidates import Candidate, SolveContext
from app.solver.common import at_min, fmt
from app.solver.plan_metrics import EARLINESS_PER_H


@dataclass(frozen=True)
class Placement:
    cand: Candidate
    start: int

    @property
    def end(self) -> int:
        return self.start + self.cand.dur


def slot_cost(c: Candidate, start: int, w: ObjectiveWeights, earliness_per_h: float) -> float:
    """Stand-alone cost of one candidate at a given start (ignores contention with other jobs)."""
    ext = c.ext_min(start)
    return (w.downtime * ext / 60.0 + (w.disruption if ext > 0 else 0.0) + w.logistics * c.opt.cost_usd
            + earliness_per_h * (start + c.dur) / 60.0)


def _slot_text(ctx: SolveContext, c: Candidate, start: int) -> str:
    ext = c.ext_min(start)
    bits = [c.opt.label if c.opt.pool else "no part needed", f"{ext / 60:.1f} h extension"]
    return f"{c.station} {fmt(at_min(ctx.now, start))} ({'; '.join(bits)})"


def build_reason(ctx: SolveContext, pl: Placement, mode: str, weights: ObjectiveWeights,
                 earliness_per_h: float = EARLINESS_PER_H) -> str:
    c = pl.cand
    task = ctx.task_by_id[c.task_id]
    ext_min = c.ext_min(pl.start)
    slack_h = (ctx.deadline_min(task) - pl.end) / 60.0
    if mode == "static_baseline":
        lead = f"Baseline policy: last ground window at home base {ctx.home.get(task.tail, '?')} that fits before the deadline"
    elif mode == "greedy":
        lead = "Greedy: earliest feasible completion without delaying a flight"
    else:
        lead = "Lowest-cost feasible slot"
    window = f"Ground window {c.win.hours:.1f} h for a {task.duration_h:g} h job: " + (
        "0 h extension." if ext_min == 0 else f"{ext_min / 60:.1f} h extension (delays the next departure).")
    parts = [
        f"{lead}: {c.station}, {fmt(at_min(ctx.now, pl.start))} to {fmt(at_min(ctx.now, pl.end))}.",
        f"{c.opt.label if c.opt.pool else 'No part needed'}; technician {c.tech_id} ({task.required_skill}) on shift.",
        window,
        f"Finishes {slack_h:.1f} h before the {fmt(task.deadline)} deadline.",
    ]
    if mode != "static_baseline":
        others: dict[tuple[str, int], tuple[float, Candidate]] = {}
        for o in ctx.candidates(task):
            if o.slot_key == c.slot_key:
                continue
            cost = slot_cost(o, o.lo, weights, earliness_per_h)
            if o.slot_key not in others or cost < others[o.slot_key][0]:
                others[o.slot_key] = (cost, o)
        if not others:
            parts.append("Only feasible slot.")
        else:
            best_cost, best = min(others.values(), key=lambda x: (x[0], x[1].lo, x[1].station))
            mine = slot_cost(c, pl.start, weights, earliness_per_h)
            note = f"{len(others)} other feasible slot(s); best alternative: {_slot_text(ctx, best, best.lo)}"
            if best_cost < mine - 1e-9:
                note += " would cost less on its own but conflicts with other jobs or the stability penalty"
            parts.append(note + ".")
    return " ".join(parts)


def make_assignment(ctx: SolveContext, pl: Placement, mode: str, weights: ObjectiveWeights) -> Assignment:
    c = pl.cand
    return Assignment(
        task_id=c.task_id, tail=c.tail, station=c.station,
        start=at_min(ctx.now, pl.start), end=at_min(ctx.now, pl.end),
        technician_id=c.tech_id, part_source=c.opt.source,
        window_hours=round(c.win.hours, 4), extension_h=round(c.ext_min(pl.start) / 60.0, 4),
        reason=build_reason(ctx, pl, mode, weights),
    )
