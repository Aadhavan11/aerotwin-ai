"""Resource ledger for the heuristic solvers (greedy and static baseline): who is busy when, and what stock is left."""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from app.solver.candidates import Candidate, SolveContext


class Ledger:
    def __init__(self, ctx: SolveContext):
        self.ctx = ctx
        self.tech: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.aircraft: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.hangar: dict[str, list[tuple[int, int]]] = defaultdict(list)
        self.stock: dict[tuple[str, str], int] = {
            (s.iata, p.part): p.qty for s in ctx.state.stations for p in s.parts}

    def earliest_start(self, c: Candidate) -> Optional[int]:
        """Earliest start in [c.lo, c.hi] where the technician, aircraft, a hangar slot and the part are all free."""
        if c.opt.pool and self.stock.get(c.opt.pool, 0) < c.part_need:
            return None
        slots = self.ctx.stations[c.station].hangar_slots
        busy = self.tech[c.tech_id] + self.aircraft[c.tail] + self.hangar[c.station]
        times = sorted({c.lo} | {e for _, e in busy if c.lo < e <= c.hi})
        for s in times:
            if self._free(c, s, slots):
                return s
        return None

    def _free(self, c: Candidate, s: int, slots: int) -> bool:
        e = s + c.dur
        for a, b in self.tech[c.tech_id]:
            if a < e and s < b:
                return False
        for a, b in self.aircraft[c.tail]:
            if a < e and s < b:
                return False
        overlapping = [(a, b) for a, b in self.hangar[c.station] if a < e and s < b]
        if len(overlapping) < slots:
            return True
        # concurrency inside [s, e): count at each start point
        points = {s} | {a for a, _ in overlapping if a > s}
        return all(sum(1 for a, b in overlapping if a <= p < b) < slots for p in points)

    def conflicts(self, c: Candidate) -> list[str]:
        """Which already-placed jobs block this candidate at its earliest start (for infeasibility messages)."""
        out: list[str] = []
        if c.opt.pool and self.stock.get(c.opt.pool, 0) < c.part_need:
            out.append(f"{c.opt.pool[1]} at {c.opt.pool[0]} used up by other jobs")
        s, e = c.lo, c.lo + c.dur
        if any(a < e and s < b for a, b in self.tech[c.tech_id]):
            out.append(f"technician {c.tech_id} busy with another job")
        if any(a < e and s < b for a, b in self.aircraft[c.tail]):
            out.append(f"{c.tail} already in another job")
        slots = self.ctx.stations[c.station].hangar_slots
        if sum(1 for a, b in self.hangar[c.station] if a < e and s < b) >= slots:
            out.append(f"{c.station} hangar slots full")
        return out

    def commit(self, c: Candidate, start: int) -> None:
        end = start + c.dur
        self.tech[c.tech_id].append((start, end))
        self.aircraft[c.tail].append((start, end))
        self.hangar[c.station].append((start, end))
        if c.opt.pool:
            self.stock[c.opt.pool] -= c.part_need
