"""Candidate slots shared by the greedy, baseline and CP-SAT solvers (one source of truth for the hard constraints).

A *candidate* is one way to do one task: (ground window, technician, part source). It carries the feasible start range
in integer minutes since ``state.now``. Hard rules baked into the start range, never relaxed here:

* ``end <= task.deadline`` (the safety constraint);
* the job lies inside the technician's shift;
* the station has hangar capacity and the part can be there in time (on hand, arriving, or shipped, with its cost);
* the job may overrun the ground window by at most ``max_ext`` (an *extension*: it delays the next departure and is costed).

Part semantics (PartStock at station S for part P, interpreted conservatively):
* ``qty >= need`` -> local stock, usable ``eta_hours`` from now (0 = on hand);
* otherwise, or additionally if faster -> ship from ``ship_from``: needs stock there, ready at
  ``source.eta_hours + ship_hours`` (for qty 0 with eta_hours > 0 we take the later of the two), cost ``ship_cost_usd``.
No source -> no candidate.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Optional

from app.contracts.models import FleetState, GroundWindow, PartStock, Station, Task
from app.solver.common import (
    DEFAULT_HORIZON_H, DEFAULT_MAX_EXT_H, aware, ceil_min, floor_min, fmt, hours_to_min,
)
from app.solver.windows import ground_windows_checked


@dataclass(frozen=True)
class PartOption:
    pool: Optional[tuple[str, str]]   # (station, part) whose stock unit is consumed; None = no part needed
    source: Optional[str]             # station the part is shipped from; None = local or none needed
    ready_min: int                    # minutes after now until the part is at the station
    cost_usd: float
    label: str


NO_PART = PartOption(pool=None, source=None, ready_min=0, cost_usd=0.0, label="no part needed")


@dataclass(frozen=True)
class Candidate:
    task_id: str
    tail: str
    station: str
    win: GroundWindow
    win_s: int                        # window start/end in minutes since now
    win_e: int
    tech_id: str
    tech_rank: int                    # position of the technician at the station (stable tie-break)
    opt: PartOption
    part_need: int
    lo: int                           # earliest allowed start (minutes)
    hi: int                           # latest allowed start (minutes); end = start + dur <= deadline and <= shift end
    dur: int                          # minutes

    def ext_min(self, start: int) -> int:
        return max(0, start + self.dur - self.win_e)

    @property
    def slot_key(self) -> tuple[str, int]:
        return (self.station, self.win_s)


class SolveContext:
    """Immutable view of one solve: tasks, windows, stations, and cached candidates."""

    def __init__(self, state: FleetState, tasks: list[Task], force_station: Optional[str] = None,
                 max_ext_h: float = DEFAULT_MAX_EXT_H, horizon_h: float = DEFAULT_HORIZON_H,
                 force_tails: Optional[set[str]] = None):
        self.state = state
        self.now = aware(state.now)
        self.tasks = sorted(tasks, key=lambda t: (aware(t.deadline), t.id))
        self.task_by_id = {t.id: t for t in self.tasks}
        wins, self.window_issues = ground_windows_checked(state.legs, state.now, state.aircraft)
        self.windows: dict[str, list[GroundWindow]] = defaultdict(list)
        for w in wins:
            self.windows[w.tail].append(w)
        self.stations: dict[str, Station] = {s.iata: s for s in state.stations}
        self.tech_station = {t.id: s.iata for s in state.stations for t in s.technicians}
        self.home = {a.tail: a.home for a in state.aircraft}
        self.max_ext_min = max(0, hours_to_min(max_ext_h))
        self.max_ext_h = max_ext_h
        self.horizon_h = horizon_h
        self.force_station = force_station
        self.force_tails = force_tails            # None = the forced station applies to every aircraft's tasks
        self._cache: dict[str, list[Candidate]] = {}
        self._blockers: dict[str, dict[str, Counter]] = {}

    # ---- minutes helpers ----
    def deadline_min(self, task: Task) -> int:
        return floor_min(task.deadline, self.now)

    def earliest_min(self, task: Task) -> int:
        return max(0, ceil_min(task.earliest, self.now))

    # ---- parts ----
    def part_options(self, station: str, task: Task) -> list[PartOption]:
        if not task.required_part or task.part_qty <= 0:
            return [NO_PART]
        part, need = task.required_part, task.part_qty
        st = self.stations.get(station)
        ps = _stock(st, part) if st else None
        if ps is None:
            return []
        opts: list[PartOption] = []
        if ps.qty >= need:
            ready = hours_to_min(ps.eta_hours)
            label = f"{part} on hand at {station}" if ps.eta_hours <= 0 else f"{part} at {station} available in {ps.eta_hours:g} h"
            opts.append(PartOption((station, part), None, ready, 0.0, label))
        if ps.ship_from and ps.ship_from != station:
            src = self.stations.get(ps.ship_from)
            sps = _stock(src, part) if src else None
            if sps is not None and sps.qty >= need:
                ship_h = sps.eta_hours + ps.ship_hours
                if ps.qty < need:
                    ship_h = max(ship_h, ps.eta_hours)
                ready = hours_to_min(ship_h)
                if not opts or ready < opts[0].ready_min:
                    opts.append(PartOption((ps.ship_from, part), ps.ship_from, ready, float(ps.ship_cost_usd),
                                           f"{part} shipped from {ps.ship_from} (+${ps.ship_cost_usd:,.0f}, ready in {ship_h:g} h)"))
        return opts

    # ---- candidates ----
    def candidates(self, task: Task) -> list[Candidate]:
        if task.id not in self._cache:
            self._build(task)
        return self._cache[task.id]

    def blockers(self, task: Task, station: Optional[str] = None) -> Counter:
        self.candidates(task)
        per = self._blockers[task.id]
        if station is not None:
            return per.get(station, Counter())
        total: Counter = Counter()
        for c in per.values():
            total.update(c)
        return total

    def _build(self, task: Task) -> None:
        cands: list[Candidate] = []
        per_station: dict[str, Counter] = defaultdict(Counter)
        dl, earliest, dur = self.deadline_min(task), self.earliest_min(task), hours_to_min(task.duration_h)
        for w in self.windows.get(task.tail, []):
            blockers = per_station[w.station]
            w_s, w_e = max(0, ceil_min(w.start, self.now)), floor_min(w.end, self.now)
            if w_e <= earliest or w_s >= dl:
                blockers["outside_deadline"] += 1
                continue
            if (self.force_station and w.station != self.force_station
                    and (self.force_tails is None or task.tail in self.force_tails)):
                blockers["excluded"] += 1
                continue
            st = self.stations.get(w.station)
            if st is None or st.hangar_slots <= 0:
                blockers["hangar_closed"] += 1
                continue
            with_skill = [t for t in st.technicians if task.required_skill in t.skills]
            if not with_skill:
                blockers["no_skill"] += 1
                continue
            avail = [t for t in with_skill if t.available]
            if not avail:
                blockers["tech_unavailable"] += 1
                continue
            opts = self.part_options(w.station, task)
            if not opts:
                blockers["no_part"] += 1
                continue
            made, why = 0, Counter()
            ranks = {t.id: i for i, t in enumerate(st.technicians)}
            for tech in avail:
                sh_lo, sh_hi = ceil_min(tech.shift_start, self.now), floor_min(tech.shift_end, self.now)
                for opt in opts:
                    max_end = min(dl, w_e + self.max_ext_min, sh_hi)
                    lo = max(w_s, earliest, opt.ready_min, sh_lo)
                    hi = max_end - dur
                    if hi < lo:
                        why[_why_not(w_s, w_e, earliest, opt.ready_min, sh_lo, sh_hi, dl, dur, self.max_ext_min)] += 1
                        continue
                    cands.append(Candidate(task.id, task.tail, w.station, w, w_s, w_e, tech.id, ranks[tech.id], opt,
                                           task.part_qty if opt.pool else 0, lo, hi, dur))
                    made += 1
            if not made:
                blockers[why.most_common(1)[0][0] if why else "too_short"] += 1
        cands.sort(key=lambda c: (c.lo, c.station, c.tech_rank, c.tech_id, c.opt.cost_usd))
        self._cache[task.id] = cands
        self._blockers[task.id] = dict(per_station)

    # ---- diagnostics ----
    def diagnose(self, task: Task, station: Optional[str] = None, ledger=None) -> str:
        """Plain-language reason a task could not be scheduled; names the constraint that blocked it.
        ``station`` restricts the explanation to one station (used by the home-base-only baseline)."""
        where = f" at {station}" if station else ""
        head = (f"{task.id} ({task.title}, {task.tail}): no feasible slot{where} before the deadline {fmt(task.deadline)}")
        wins = [w for w in self.windows.get(task.tail, []) if station is None or w.station == station]
        if not wins:
            return head + f"; {task.tail} has no ground windows{where} in the known rotation"
        b = self.blockers(task, station)
        slots = {c.slot_key for c in self.candidates(task) if station is None or c.station == station}
        considered = sum(v for k, v in b.items() if k != "outside_deadline")
        if slots:
            msg = f"; {len(slots)} feasible slot(s) exist but every one is taken by other jobs"
            if ledger is not None:
                why: Counter = Counter()
                for c in self.candidates(task):
                    if station is None or c.station == station:
                        why.update(set(ledger.conflicts(c)))
                if why:
                    msg += ": " + ", ".join(f"{k} ({n} option(s))" for k, n in why.most_common(3))
            return head + msg
        skill, part = task.required_skill, task.required_part or "part"
        names = {
            "hangar_closed": "at stations with no hangar capacity",
            "no_skill": f"at stations with no {skill} technician",
            "tech_unavailable": f"where every {skill} technician is unavailable",
            "no_part": f"where {part} is not stocked and cannot be shipped",
            "part_late": f"where {part} cannot arrive in time",
            "shift": f"where no {skill} technician is on shift in time",
            "too_short": f"too short for the {task.duration_h:g} h job (even with up to {self.max_ext_h:g} h extension)",
            "excluded": "at stations outside the forced station",
        }
        parts = [f"{n} {names[k]}" for k, n in b.most_common() if k in names]
        if considered == 0:
            return head + f"; none of {task.tail}'s {len(wins)} ground windows falls between now and the deadline"
        return head + f"; of {considered} ground window(s){where} before the deadline: " + ", ".join(parts)


def _stock(st: Optional[Station], part: str) -> Optional[PartStock]:
    if st is None:
        return None
    for p in st.parts:
        if p.part == part:
            return p
    return None


def _why_not(w_s: int, w_e: int, earliest: int, part_ready: int, sh_lo: int, sh_hi: int,
             dl: int, dur: int, max_ext: int) -> str:
    """Which single bound makes the start range empty: part timing, shift, or window length."""
    def feasible(part: int, shift_lo: int, shift_hi: int) -> bool:
        return max(w_s, earliest, part, shift_lo) <= min(dl, w_e + max_ext, shift_hi) - dur
    if feasible(0, sh_lo, sh_hi) and not feasible(part_ready, sh_lo, sh_hi):
        return "part_late"
    if feasible(part_ready, -10**9, 10**9) and not feasible(part_ready, sh_lo, sh_hi):
        return "shift"
    if feasible(0, -10**9, 10**9) and not feasible(part_ready, sh_lo, sh_hi):
        return "part_late"
    return "too_short"
