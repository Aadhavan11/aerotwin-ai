"""OR-Tools CP-SAT model for fleet-aware maintenance scheduling (feature 3.3, level 2).

Time is integer minutes since ``state.now``. One *optional interval* per candidate (task, ground window, technician, part source).

Hard constraints (never relaxed, never in the objective):
  * every task: exactly one candidate OR the ``unscheduled`` literal (which is penalised by BIG and reported with reasons);
  * ``end <= task.deadline``, the job inside the technician's shift, ``start >= earliest / part ready / window start``
    (all baked into each start variable's domain by ``SolveContext``);
  * technician: NoOverlap over his intervals; aircraft: NoOverlap over its intervals;
  * hangar: Cumulative(demand 1) <= hangar_slots per station (a closed hangar has no candidates);
  * parts: units consumed from each (station, part) pool <= stock; shipped parts consume the SOURCE station's stock;
  * ground-time extension: ``end <= window_end + ext`` with ``ext <= max_ext`` (hard cap); ext > 0 is costed.

Objective (all integers; SCALE turns the float weights into ints):
  alpha*downtime_h + beta*flights_affected + gamma*logistics_usd + delta*changed_vs_previous
  + earliness*hours_until_job_ends (soft preference for sooner, safer fixes) + tiny technician-rank tie-break + BIG*unscheduled.

Method names are the snake_case ones (OR-Tools >= 9.10; the CamelCase aliases exist but are deprecated).
"""
from __future__ import annotations

import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from ortools.sat.python import cp_model

from app.contracts.models import ObjectiveWeights, Plan
from app.solver.candidates import Candidate, SolveContext
from app.solver.ledger import Ledger
from app.solver.common import SEED, minutes_between
from app.solver.plan_metrics import EARLINESS_PER_H
from app.solver.reasons import Placement

log = logging.getLogger(__name__)

SCALE = 10_000_000          # float cost unit -> integer objective units
BIG = 10**13                # per unscheduled task: dominates any feasible cost


@dataclass
class CpSatResult:
    placements: list[Placement]
    unscheduled: dict[str, str]
    status: str                    # "optimal" | "feasible"
    gap_note: str
    wall_seconds: float


def _workers() -> int:
    return max(1, int(os.getenv("SOLVER_WORKERS", "1")))


def run(ctx: SolveContext, weights: ObjectiveWeights, time_limit_s: float = 5.0, previous: Optional[Plan] = None,
        earliness_per_h: float = EARLINESS_PER_H, hint: Optional[list[Placement]] = None) -> Optional[CpSatResult]:
    m = cp_model.CpModel()
    cands: list[Candidate] = []
    xs: list = []
    sv: list = []
    ivs: list = []
    obj: list = []
    per_task_lits: dict[str, list[int]] = {}
    unsched = {}

    c_ext = round(weights.downtime * SCALE / 60.0)       # per minute of extension
    c_late = round(weights.disruption * SCALE)           # per affected flight
    c_early = round(earliness_per_h * SCALE / 60.0)      # per minute until the job ends
    c_change = round(weights.changes * SCALE)

    by_tech: dict[str, list[int]] = defaultdict(list)
    by_tail: dict[str, list[int]] = defaultdict(list)
    by_station: dict[str, list[int]] = defaultdict(list)
    by_pool: dict[tuple[str, str], list[int]] = defaultdict(list)

    for task in ctx.tasks:
        un = m.new_bool_var(f"un_{task.id}")
        unsched[task.id] = un
        obj.append(BIG * un)
        lits = [un]
        for c in ctx.candidates(task):
            i = len(cands)
            cands.append(c)
            x = m.new_bool_var(f"x{i}")
            s = m.new_int_var(c.lo, c.hi, f"s{i}")
            iv = m.new_optional_fixed_size_interval_var(s, c.dur, x, f"iv{i}")
            xs.append(x), sv.append(s), ivs.append(iv)
            lits.append(x)
            by_tech[c.tech_id].append(i)
            by_tail[c.tail].append(i)
            by_station[c.station].append(i)
            if c.opt.pool:
                by_pool[c.opt.pool].append(i)

            # end time if present (for the earliness term)
            end = m.new_int_var(0, c.hi + c.dur, f"e{i}")
            m.add(end == s + c.dur).only_enforce_if(x)
            m.add(end == 0).only_enforce_if(x.negated())
            terms = [c_early * end, c.tech_rank * x, round(weights.logistics * c.opt.cost_usd * SCALE) * x]

            # ground-time extension: end <= window_end + ext, ext <= max_ext (hard); ext > 0 flags an affected flight
            max_over = c.hi + c.dur - c.win_e
            if max_over > 0:
                ext = m.new_int_var(0, min(ctx.max_ext_min, max_over), f"ext{i}")
                late = m.new_bool_var(f"late{i}")
                m.add(s + c.dur - c.win_e <= ext).only_enforce_if(x)
                m.add(ext == 0).only_enforce_if(x.negated())
                m.add(ext <= ctx.max_ext_min * late)
                terms += [c_ext * ext, c_late * late]
            obj.extend(terms)
        m.add_exactly_one(lits)
        per_task_lits[task.id] = lits

    # resources
    for ids in by_tech.values():
        if len(ids) > 1:
            m.add_no_overlap([ivs[i] for i in ids])
    for ids in by_tail.values():
        if len(ids) > 1:
            m.add_no_overlap([ivs[i] for i in ids])
    for station, ids in by_station.items():
        slots = ctx.stations[station].hangar_slots
        if len(ids) > slots:
            m.add_cumulative([ivs[i] for i in ids], [1] * len(ids), slots)
    stock = {(s.iata, p.part): p.qty for s in ctx.state.stations for p in s.parts}
    for pool, ids in by_pool.items():
        if sum(cands[i].part_need for i in ids) > stock.get(pool, 0):
            m.add(sum(cands[i].part_need * xs[i] for i in ids) <= stock.get(pool, 0))

    # stability versus the previous plan: charge delta for every changed (station/start) assignment
    if previous is not None and c_change:
        prior = {a.task_id: a for a in previous.assignments}
        for task in ctx.tasks:
            pa = prior.get(task.id)
            if pa is None:
                continue
            pstart = round(minutes_between(pa.start, ctx.now))
            same_ids = [i for i, c in enumerate(cands)
                        if c.task_id == task.id and c.station == pa.station and c.lo <= pstart <= c.hi]
            keep = m.new_bool_var(f"keep_{task.id}")
            if not same_ids:
                m.add(keep == 0)
            else:
                m.add(sum(xs[i] for i in same_ids) >= keep)
                for i in same_ids:
                    m.add(sv[i] == pstart).only_enforce_if([keep, xs[i]])
            obj.append(c_change * (1 - keep))

    m.minimize(sum(obj))

    if hint is not None:
        chosen = {(p.cand.task_id, p.cand.station, p.cand.win_s, p.cand.tech_id, p.cand.opt.pool): p for p in hint}
        placed_tasks = {p.cand.task_id for p in hint}
        for i, c in enumerate(cands):
            p = chosen.get((c.task_id, c.station, c.win_s, c.tech_id, c.opt.pool))
            m.add_hint(xs[i], 1 if p else 0)
            if p:
                m.add_hint(sv[i], p.start)
        for tid, un in unsched.items():
            m.add_hint(un, 0 if tid in placed_tasks else 1)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_s)
    solver.parameters.random_seed = SEED
    try:
        solver.parameters.num_workers = _workers()
    except (AttributeError, TypeError):  # older OR-Tools
        solver.parameters.num_search_workers = _workers()
    status = solver.solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        log.error("CP-SAT returned %s", solver.status_name(status))
        return None

    placements: list[Placement] = []
    for i, c in enumerate(cands):
        if solver.boolean_value(xs[i]):
            placements.append(Placement(c, solver.value(sv[i])))
    placed = {p.cand.task_id for p in placements}
    ledger = Ledger(ctx)
    for p in placements:
        ledger.commit(p.cand, p.start)
    unscheduled = {t.id: ctx.diagnose(t, ledger=ledger) for t in ctx.tasks if t.id not in placed}
    gap = solver.objective_value - solver.best_objective_bound
    note = ("proven optimal" if status == cp_model.OPTIMAL
            else f"time limit reached; best feasible solution found, not proven optimal (gap {gap / SCALE:.3f} cost units)")
    return CpSatResult(placements, unscheduled, "optimal" if status == cp_model.OPTIMAL else "feasible", note, solver.wall_time)
