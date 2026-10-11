"""Static baseline: the comparison policy for the demo.

DEFINITION (the demo states it out loud): "do each task at the aircraft's HOME BASE, in the LAST ground window that fits
before the task's deadline". It is a fixed-schedule policy: no looking at other stations, no early action. It still uses
real resources (a technician with the skill, a hangar slot, the part: on hand or shipped at its cost), so its plan is
executable; it simply does not optimize. If the last window needs a ground-time extension it takes the latest window
where the job fits with the smallest extension. A task that fits at no home-base window is reported unscheduled.
"""
from __future__ import annotations

from app.solver.candidates import Candidate, SolveContext
from app.solver.ledger import Ledger
from app.solver.reasons import Placement


def run(ctx: SolveContext) -> tuple[list[Placement], dict[str, str]]:
    ledger = Ledger(ctx)
    placed: list[Placement] = []
    unscheduled: dict[str, str] = {}
    for task in ctx.tasks:
        home = ctx.home.get(task.tail)
        by_window: dict[int, list[tuple[Candidate, int]]] = {}
        for c in ctx.candidates(task):
            if c.station != home:
                continue
            s = ledger.earliest_start(c)
            if s is not None:
                by_window.setdefault(c.win_s, []).append((c, s))
        best = None
        if by_window:
            no_ext = [w for w, items in by_window.items() if any(c.ext_min(s) == 0 for c, s in items)]
            if no_ext:
                w = max(no_ext)   # the LAST window that fits without extension
                items = [(c, s) for c, s in by_window[w] if c.ext_min(s) == 0]
            else:
                w = max(by_window)  # latest window that fits at all, with the smallest extension
                items = by_window[w]
            c, s = min(items, key=lambda cs: (cs[0].ext_min(cs[1]), cs[1], cs[0].opt.cost_usd, cs[0].tech_rank, cs[0].tech_id))
            best = Placement(c, s)
        if best is None:
            msg = ctx.diagnose(task, station=home, ledger=ledger)
            elsewhere = len({c.slot_key for c in ctx.candidates(task) if c.station != home})
            msg += f". Baseline policy only considers the home base {home}"
            if elsewhere:
                msg += f" ({elsewhere} slot(s) at other stations would work)"
            unscheduled[task.id] = msg
        else:
            ledger.commit(best.cand, best.start)
            placed.append(best)
    return placed, unscheduled
