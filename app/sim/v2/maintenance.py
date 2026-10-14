"""Maintenance layer for simulator v2: serialised parts, removals, inspection POD, MEL deferrals, scheduled checks.

Everything here is bookkeeping around ``Fleet.maintain``. Cost lookup stays outside (records expose
``cost_class`` = "overhaul" | "replace" | "repair" | "inspect" for the caller to price).
All records are plain dataclasses with ``as_dict()`` and carry ``provenance='SIMULATED'``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

import numpy as np

from app.sim.v2.core import ACTIONS, Fleet, StreamRng

PROVENANCE = "SIMULATED"
# "wear_limit": consumable (tires, brakes) change because the wear / retread limit was reached with no fault, cut or leak
# involved. It is a planned, condition-based removal, so it is never counted as unscheduled.
REMOVAL_REASONS = ("scheduled", "wear_limit", "failure", "inspection_finding", "nff", "deferred_defect_rectification", "opportunistic")
_PLANNED = ("scheduled", "wear_limit", "opportunistic", "deferred_defect_rectification")
COST_CLASS = {"inspect": "inspect", "repair": "repair", "overhaul": "overhaul", "replace": "replace"}
DEFAULT_Q = {"inspect": 1.0, "repair": 0.6, "overhaul": 0.15, "replace": 0.0}
NFF_HEALTH = 0.85  # removed for failure/finding while health index above this => no fault found

MEL_INTERVAL_DAYS: dict[str, float | None] = {"A": None, "B": 3.0, "C": 10.0, "D": 120.0}
MEL_INTERVAL_TEXT = {"A": "A: per limit", "B": "B: 3 days", "C": "C: 10 days", "D": "D: 120 days"}
MEL_RESTRICTIONS = {
    "apu": "APU inoperative: ground power unit required",
    "ecs": "Pack inoperative: max altitude FL310, no RVSM restriction change",
    "hydraulics": "Hydraulic system degraded: increased landing distance",
    "brakes": "Brake unit deactivated: reduced landing weight, increased landing distance",
    "tires": "Tire pressure monitoring inoperative: manual checks each transit",
    "avionics": "Avionics unit inoperative: alternate procedures apply",
    "electrical": "Generator inoperative: reduced electrical load, no ETOPS",
    "landing_gear": "Gear doors secured: gear down flight prohibited unless dispatch approved",
    "engine": "Engine bleed inoperative: anti-ice restrictions apply",
}


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t is not None else None


# ---------------- parts ----------------
@dataclass
class PartInstance:
    serial: str
    part_no: str
    kind: str
    position: str
    tsn_h: float = 0.0
    csn: float = 0.0
    tso_h: float = 0.0
    cso: float = 0.0
    since_repair_cycles: float = 0.0
    llp_limit_cycles: float | None = None
    installed_on: str | None = None
    status: str = "installed"  # installed | removed | scrapped
    provenance: str = PROVENANCE

    def accrue(self, hours: float, cycles: float) -> None:
        self.tsn_h += hours
        self.csn += cycles
        self.tso_h += hours
        self.cso += cycles
        self.since_repair_cycles += cycles

    @property
    def llp_remaining(self) -> float | None:
        return None if self.llp_limit_cycles is None else self.llp_limit_cycles - self.csn

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Removal:
    serial: str
    kind: str
    unit: int
    position: str
    tail: str | None
    action: str
    removal_reason: str
    unscheduled: bool
    nff: bool
    health_before: float
    health_after: float
    q: float
    cost_class: str
    tsn_h: float
    csn: float
    tso_h: float
    cso: float
    reason_text: str
    time: str | None
    new_serial: str | None = None
    provenance: str = PROVENANCE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InspectionRecord:
    kind: str
    unit: int
    defect_size: float
    pod: float
    detected: bool
    time: str | None
    provenance: str = PROVENANCE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class InstallRegistry:
    """Which serial sits on which (kind, unit) plus the removal history."""

    def __init__(self, prefix: str = "SN") -> None:
        self.prefix = prefix
        self._serial_n = 0
        self.parts: dict[str, PartInstance] = {}
        self.installed: dict[tuple[str, int], str] = {}
        self.removals: list[Removal] = []

    def new_serial(self) -> str:
        self._serial_n += 1
        return f"{self.prefix}{self._serial_n:06d}"

    def install(self, kind: str, unit: int, position: str | None = None, tail: str | None = None, part_no: str | None = None,
                llp_limit_cycles: float | None = None, serial: str | None = None) -> PartInstance:
        key = (kind, int(unit))
        if key in self.installed:
            raise ValueError(f"unit {key} already has a part installed")
        p = PartInstance(serial=serial or self.new_serial(), part_no=part_no or f"PN-{kind.upper()}", kind=kind,
                         position=position or f"{kind}_{unit}", llp_limit_cycles=llp_limit_cycles, installed_on=tail)
        self.parts[p.serial] = p
        self.installed[key] = p.serial
        return p

    def populate(self, counts: dict[str, int], tails: dict[str, Iterable[str]] | None = None) -> None:
        for kind in sorted(counts):
            tl = list(tails[kind]) if tails and kind in tails else [None] * counts[kind]
            for i in range(counts[kind]):
                if (kind, i) not in self.installed:
                    self.install(kind, i, tail=tl[i] if i < len(tl) else None)

    def on_unit(self, kind: str, unit: int) -> PartInstance | None:
        s = self.installed.get((kind, int(unit)))
        return self.parts[s] if s else None

    def accrue(self, kind: str, hours: np.ndarray | float, cycles: float = 1.0) -> None:
        for (k, i), s in self.installed.items():
            if k == kind:
                h = float(hours[i]) if np.ndim(hours) else float(hours)
                self.parts[s].accrue(h, cycles)

    def remove(self, kind: str, unit: int, scrap: bool = False) -> PartInstance:
        s = self.installed.pop((kind, int(unit)))
        p = self.parts[s]
        p.status = "scrapped" if scrap else "removed"
        p.installed_on = None
        return p

    def history(self, serial: str) -> list[Removal]:
        return [r for r in self.removals if r.serial == serial]


# ---------------- inspection ----------------
def pod_curve(size: np.ndarray | float, a50: float = 0.25, slope: float = 0.08, floor: float = 0.0, ceil: float = 0.98) -> np.ndarray:
    """Logistic probability of detection vs defect size (0..1); monotone non-decreasing in size.

    a50 is the size detected 50% of the time (before floor/ceil scaling), slope the logistic scale.
    """
    s = np.asarray(size, dtype=float)
    z = np.clip((s - a50) / max(slope, 1e-9), -60.0, 60.0)
    return floor + (ceil - floor) / (1.0 + np.exp(-z))


# ---------------- MEL ----------------
@dataclass
class MelDeferral:
    id: str
    kind: str
    unit: int
    tail: str | None
    category: str
    opened: datetime
    description: str
    interval_days: float | None
    restriction: str
    closed: datetime | None = None
    provenance: str = PROVENANCE

    @property
    def expires(self) -> datetime | None:
        return None if self.interval_days is None else self.opened + timedelta(days=self.interval_days)

    def expired(self, now: datetime) -> bool:
        e = self.expires
        return self.closed is None and e is not None and now >= e

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["opened"], d["closed"], d["expires"] = _iso(self.opened), _iso(self.closed), _iso(self.expires)
        d["interval_text"] = MEL_INTERVAL_TEXT[self.category]
        return d


class MelBook:
    def __init__(self) -> None:
        self.items: list[MelDeferral] = []

    def defer(self, kind: str, unit: int, category: str, now: datetime, description: str = "", tail: str | None = None,
              limit_days: float | None = None, restriction: str | None = None) -> MelDeferral:
        if category not in MEL_INTERVAL_DAYS:
            raise ValueError(f"unknown MEL category: {category}")
        interval = MEL_INTERVAL_DAYS[category] if category != "A" else limit_days
        d = MelDeferral(id=f"MEL{len(self.items) + 1:04d}", kind=kind, unit=int(unit), tail=tail, category=category, opened=now,
                        description=description, interval_days=interval,
                        restriction=restriction or MEL_RESTRICTIONS.get(kind, "Operational restrictions per MEL apply"))
        self.items.append(d)
        return d

    def open_items(self) -> list[MelDeferral]:
        return [d for d in self.items if d.closed is None]

    def expired(self, now: datetime) -> list[MelDeferral]:
        return [d for d in self.items if d.expired(now)]

    def close(self, kind: str, unit: int, now: datetime) -> list[MelDeferral]:
        done = [d for d in self.open_items() if d.kind == kind and d.unit == int(unit)]
        for d in done:
            d.closed = now
        return done

    def restrictions(self, tail: str | None = None) -> list[str]:
        return [d.restriction for d in self.open_items() if tail is None or d.tail == tail]


# ---------------- scheduled checks ----------------
@dataclass(frozen=True)
class CheckDef:
    id: str
    kind: str
    interval_fh: float | None = None
    interval_fc: float | None = None
    interval_days: float | None = None


@dataclass
class DueCheck:
    check_id: str
    kind: str
    unit: int
    trigger: str  # fh | fc | days: whichever limit was reached first
    overrun: float
    provenance: str = PROVENANCE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ScheduledCheckGenerator:
    """Emits due checks when any of FH / FC / calendar limits is reached since the last accomplishment (whichever first)."""

    def __init__(self, checks: Iterable[CheckDef]) -> None:
        self.checks = list(checks)
        self._last: dict[tuple[str, int], tuple[float, float, float]] = {}

    def due(self, kind: str, unit: int, fh: float, fc: float, days: float) -> list[DueCheck]:
        out = []
        for c in self.checks:
            if c.kind != kind:
                continue
            fh0, fc0, d0 = self._last.get((c.id, unit), (0.0, 0.0, 0.0))
            ratios = []
            if c.interval_fh:
                ratios.append(((fh - fh0) / c.interval_fh, "fh"))
            if c.interval_fc:
                ratios.append(((fc - fc0) / c.interval_fc, "fc"))
            if c.interval_days:
                ratios.append(((days - d0) / c.interval_days, "days"))
            if not ratios:
                continue
            r, trig = max(ratios)
            if r >= 1.0:
                out.append(DueCheck(c.id, kind, int(unit), trig, r - 1.0))
        return out

    def accomplish(self, check_id: str, unit: int, fh: float, fc: float, days: float) -> None:
        self._last[(check_id, int(unit))] = (fh, fc, days)


# ---------------- engine ----------------
class MaintenanceEngine:
    def __init__(self, seed: int = 0, registry: InstallRegistry | None = None, q_by_action: dict[str, float] | None = None,
                 pod: dict[str, float] | None = None, nff_health: float = NFF_HEALTH) -> None:
        self.registry = registry or InstallRegistry()
        self.q_by_action = {**DEFAULT_Q, **(q_by_action or {})}
        self.pod_params = dict(pod or {})
        self.nff_health = nff_health
        self.mel = MelBook()
        self._rng = StreamRng(seed, "maintenance.inspect")
        self._n_inspect = 0
        self.inspections: list[InspectionRecord] = []

    @property
    def removals(self) -> list[Removal]:
        return self.registry.removals

    def inspect(self, fleet: Fleet, kind: str, idx: Any, now: datetime | None = None) -> list[InspectionRecord]:
        idx = np.atleast_1d(np.asarray(idx, dtype=np.int64))
        hi = fleet.models[kind].health_index(fleet.state[kind])
        gen = self._rng.at(self._n_inspect)
        self._n_inspect += 1
        u = gen.random(idx.size)
        recs = []
        for j, i in enumerate(idx):
            size = float(np.clip(1.0 - hi[i], 0.0, 1.0))
            p = float(pod_curve(size, **self.pod_params))
            recs.append(InspectionRecord(kind, int(i), size, p, bool(u[j] < p), _iso(now)))
        self.inspections.extend(recs)
        return recs

    def apply(self, fleet: Fleet, kind: str, idx: Any, action: str, q: float | None = None, reason: str = "scheduled",
              now: datetime | None = None, reason_text: str = "") -> list[Removal | InspectionRecord]:
        if action not in ACTIONS:
            raise ValueError(f"unknown action: {action}")
        if reason not in REMOVAL_REASONS:
            raise ValueError(f"unknown removal reason: {reason}")
        idx = np.atleast_1d(np.asarray(idx, dtype=np.int64))
        if action == "inspect":
            fleet.maintain(kind, idx, "inspect", 1.0)
            return list(self.inspect(fleet, kind, idx, now))
        qq = self.q_by_action[action] if q is None else float(q)
        hi0 = fleet.models[kind].health_index(fleet.state[kind]).copy()
        fleet.maintain(kind, idx, action, qq)
        hi1 = fleet.models[kind].health_index(fleet.state[kind])
        out: list[Removal | InspectionRecord] = []
        for i in idx:
            i = int(i)
            part = self.registry.on_unit(kind, i)
            if part is None:
                part = self.registry.install(kind, i)
            nff = reason == "nff" or (reason in ("failure", "inspection_finding") and float(hi0[i]) > self.nff_health)
            new_serial = None
            tail = part.installed_on
            if action == "replace":
                pos = part.position
                self.registry.remove(kind, i)
                new = self.registry.install(kind, i, position=pos, tail=tail, part_no=part.part_no, llp_limit_cycles=part.llp_limit_cycles)
                new_serial = new.serial
            elif action == "overhaul":
                part.tso_h = part.cso = part.since_repair_cycles = 0.0
            elif action == "repair":
                part.since_repair_cycles = 0.0
            rec = Removal(serial=part.serial, kind=kind, unit=i, position=part.position, tail=tail, action=action,
                          removal_reason=reason, unscheduled=reason not in _PLANNED, nff=nff, health_before=float(hi0[i]),
                          health_after=float(hi1[i]), q=qq, cost_class=COST_CLASS[action], tsn_h=part.tsn_h, csn=part.csn,
                          tso_h=part.tso_h, cso=part.cso, reason_text=reason_text, time=_iso(now), new_serial=new_serial)
            self.registry.removals.append(rec)
            out.append(rec)
        return out


__all__ = ["CheckDef", "DueCheck", "InspectionRecord", "InstallRegistry", "MaintenanceEngine", "MelBook", "MelDeferral", "PartInstance",
           "REMOVAL_REASONS", "Removal", "ScheduledCheckGenerator", "pod_curve", "MEL_INTERVAL_DAYS"]
