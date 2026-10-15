"""Serialised rotable layer for simulator v2 (opt-in: ``World(..., rotables=True)``).

Design (why this and not extra Fleet slots): the Fleet keeps ONE vectorised model state per installed position (slot).
That state stays the physical-wear proxy of whatever serial sits in the slot, so ``Fleet.state_hash``, the 5-seed removal
bands and runtime are untouched. On top of it this module keeps a lightweight per-serial record (TSN/CSN/TSO/CSO live in
``PartInstance``; here: part-number variant, mod status, hidden intrinsic quality factor, hidden rogue flag, shop history).
The installed serial's hidden factors map to the model through a per-slot multiplier on ``stress_ext`` (wear/hazard rate),
and a rogue serial can additionally fail early (``failed`` flag). Removed serials go to a shop (turnaround, finding), return
to a station pool and may be installed on ANY tail (nearest stock first).

Everything numeric below is an ASSUMED prior (no citation, no fleet data). Ground-truth fields (``quality``, ``rogue``) are
only exported through ``RotablePool.truth()``; telemetry rows and removal/install records never carry them.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from typing import Any, Sequence

import numpy as np

from app.sim.clock import START
from app.sim.v2.core import StreamRng
from app.sim.v2.maintenance import NFF_RETURN, PartInstance, Removal

PROVENANCE = "SIMULATED"
SWAP_ACTIONS = ("replace", "overhaul", NFF_RETURN)  # actions that physically remove the serial from its position
STATIONS = ("ATL", "DEN", "DFW", "JFK", "LAX", "MIA", "ORD", "SFO")
MODDED_KINDS = ("avionics", "apu", "ecs", "hydraulics", "electrical", "engine")  # kinds with a post-mod part-number variant
ROGUE_KINDS = ("avionics", "apu", "ecs", "hydraulics", "electrical", "engine", "landing_gear")  # consumables carry no latent defect

# ---- ALL ASSUMED priors (no citation) ----
ROGUE_PROB = 0.01          # probability a serial carries a latent defect that survives shop tests (default is deliberately small)
ROGUE_HAZARD0 = 0.004      # per cycle extra failure hazard of a rogue unit right after install
ROGUE_TAU = 300.0          # cycles: decay constant of the early hazard (age since install)
ROGUE_HAZARD_FLOOR = 0.0002  # per cycle residual hazard of a rogue unit
ROGUE_NFF_PROB = 0.7       # probability the shop does NOT reproduce the failure of a rogue unit (finding NFF, unit returned unchanged)
QUALITY_SIGMA = 0.12       # lognormal sigma of the intrinsic per-serial quality factor (mean 1)
POST_MOD_FRACTION = 0.5    # share of serials of a modded kind that are post-mod
MOD_HAZARD = {"pre": 1.15, "post": 0.85, "none": 1.0}  # stress multiplier by mod status (post-mod is more durable)
TURNAROUND_DAYS = {"avionics": 20.0, "apu": 35.0, "ecs": 25.0, "hydraulics": 25.0, "electrical": 20.0, "engine": 60.0,
                   "landing_gear": 40.0, "brakes": 15.0, "tires": 7.0}  # median shop turnaround, days
TURNAROUND_SIGMA = 0.4     # lognormal sigma of the turnaround
NFF_TURNAROUND_FACTOR = 0.4  # a no-fault-found check is quicker than a repair
BER_PROB = 0.04            # probability a confirmed-failure unit is beyond economic repair (scrapped, a new unit is bought)
BER_LEAD_DAYS = 30.0       # purchase lead time of a replacement unit
SHOP_REPAIR_Q = 0.5        # Kijima q of a shop repair after a confirmed failure (overhaul uses the removal's own q)
SPARES_FRACTION = 0.15     # spare units per station = ceil(SPARES_FRACTION * units of kind / n_stations), at least 1
FERRY_DAYS_PER_HOP = 0.25  # AOG exposure per station hop when the nearest stock is elsewhere
AOG_CAP_DAYS = 5.0         # a stock-out is covered by a rush (expedited shop return); exposure is capped at this


@dataclass
class RotableConfig:
    rogue_prob: float = field(default_factory=lambda: ROGUE_PROB)
    rogue_hazard0: float = field(default_factory=lambda: ROGUE_HAZARD0)
    rogue_tau: float = field(default_factory=lambda: ROGUE_TAU)
    rogue_floor: float = field(default_factory=lambda: ROGUE_HAZARD_FLOOR)
    rogue_nff_prob: float = field(default_factory=lambda: ROGUE_NFF_PROB)
    quality_sigma: float = field(default_factory=lambda: QUALITY_SIGMA)
    post_mod_fraction: float = field(default_factory=lambda: POST_MOD_FRACTION)
    ber_prob: float = field(default_factory=lambda: BER_PROB)
    spares_per_station: int | None = None   # None: sized from fleet size (SPARES_FRACTION)
    turnaround_scale: float = 1.0
    rogue_kinds: tuple[str, ...] = field(default_factory=lambda: tuple(ROGUE_KINDS))


def _iso(day: float) -> str:
    return (START + timedelta(days=float(day))).isoformat()


def part_number(kind: str, post_mod: bool) -> str:
    return f"PN-{kind.upper()}-{'02' if post_mod else '01'}"


def _hop(a: str, b: str) -> int:
    ia = STATIONS.index(a) if a in STATIONS else 0
    ib = STATIONS.index(b) if b in STATIONS else 0
    d = abs(ia - ib)
    return min(d, len(STATIONS) - d)


@dataclass
class RotableUnit:
    serial: str
    kind: str
    part_no: str
    mod_status: str
    quality: float          # HIDDEN: intrinsic multiplier on wear/hazard rate
    rogue: bool             # HIDDEN: latent defect surviving shop tests
    status: str = "installed"   # installed | shop | scrapped  (a shop unit is "in stock" once ready_day has passed)
    station: str = "ATL"
    ready_day: float = 0.0      # calendar day (since START) the unit leaves the shop / is available
    installed_day: float = 0.0
    removed_day: float = -1.0
    shop_visits: int = 0
    tails: list[str] = field(default_factory=list)  # tails it has been installed on, in order (visible lineage)


@dataclass
class ShopVisit:
    serial: str
    part_number: str
    kind: str
    tail: str | None
    position: str
    station: str
    shop_in: str
    shop_out: str
    turnaround_days: float
    shop_finding: str           # confirmed | nff | partial
    removal_reason: str
    action: str
    scrapped: bool = False
    provenance: str = PROVENANCE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class InstallRecord:
    serial: str
    part_number: str
    kind: str
    tail: str
    position: str
    installed_at: str
    tsn_h: float
    csn: float
    station: str
    source: str               # initial | stock | rush | purchase
    aog_days: float = 0.0
    provenance: str = PROVENANCE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class RotablePool:
    """Per-kind serial pools + shop; one instance per ``World`` with ``rotables=True``."""

    def __init__(self, seed: int, kinds: Sequence[str], counts: dict[str, int], config: RotableConfig | None = None) -> None:
        self.seed = int(seed)
        self.kinds = list(kinds)
        self.counts = dict(counts)
        self.cfg = config or RotableConfig()
        self.units: dict[str, RotableUnit] = {}
        self.shop_visits: list[ShopVisit] = []
        self.install_log: list[InstallRecord] = []
        self.exposure: dict[str, float] = {"aog_days": 0.0, "ferry_hops": 0.0, "rush_installs": 0.0, "stock_installs": 0.0, "purchases": 0.0}
        self.slot_mult = {k: np.ones(self.counts[k]) for k in self.kinds}
        self.slot_age = {k: np.zeros(self.counts[k]) for k in self.kinds}
        self.rogue_slot = {k: np.zeros(self.counts[k], dtype=bool) for k in self.kinds}
        self._shop_rng = {k: StreamRng(self.seed, f"rotables.shop.{k}") for k in self.kinds}
        self._rogue_rng = {k: StreamRng(self.seed, f"rotables.rogue.{k}") for k in self.kinds}
        self._tail_station: list[str] = []

    # ---- construction ----
    def _hidden(self, kind: str, n: int, tag: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        g = StreamRng(self.seed, f"rotables.serial.{kind}").at(tag)
        u = g.random((n, 3))
        z = g.standard_normal(n)
        s = self.cfg.quality_sigma
        quality = np.exp(s * z - 0.5 * s * s)
        modded = kind in MODDED_KINDS
        post = (u[:, 0] < self.cfg.post_mod_fraction) if modded else np.zeros(n, dtype=bool)
        rogue = (u[:, 1] < self.cfg.rogue_prob) if kind in self.cfg.rogue_kinds else np.zeros(n, dtype=bool)
        return quality, post, rogue

    def bootstrap(self, registry: Any, component_name: Any, tails: Sequence[str], unit_tail: dict[str, np.ndarray]) -> None:
        """Attach hidden attributes to the initial installed population and create the spare units."""
        self._tail_station = ["ATL"] * len(tails)
        for kind in self.kinds:
            n = self.counts[kind]
            quality, post, rogue = self._hidden(kind, n, 0)
            for i in range(n):
                part = registry.on_unit(kind, i)
                part.part_no = part_number(kind, bool(post[i]))
                part.mod_status = "none" if kind not in MODDED_KINDS else ("post" if post[i] else "pre")
                u = RotableUnit(part.serial, kind, part.part_no, part.mod_status, float(quality[i]), bool(rogue[i]),
                                station="ATL", tails=[tails[int(unit_tail[kind][i])]])
                self.units[part.serial] = u
                self.log_install(part, tails[int(unit_tail[kind][i])], part.position, 0.0, "initial", 0.0)
            per = self.cfg.spares_per_station
            if per is None:
                per = max(1, math.ceil(SPARES_FRACTION * n / len(STATIONS)))
            ns = per * len(STATIONS)
            q2, p2, r2 = self._hidden(kind, ns, 1)
            for j in range(ns):   # spares start as zero-hour serials so they add no spurious exposure to the reliability pack
                self._new_spare(registry, kind, float(q2[j]), bool(p2[j]), bool(r2[j]), STATIONS[j % len(STATIONS)], 0.0)
        self.refresh_slots(registry)

    def _new_spare(self, registry: Any, kind: str, quality: float, post: bool, rogue: bool, station: str, ready_day: float) -> RotableUnit:
        serial = registry.new_serial()
        status = "none" if kind not in MODDED_KINDS else ("post" if post else "pre")
        pn = part_number(kind, post)
        part = PartInstance(serial=serial, part_no=pn, kind=kind, position="", installed_on=None, status="spare", mod_status=status)
        registry.parts[serial] = part
        u = RotableUnit(serial, kind, pn, status, quality, rogue, status="shop", station=station, ready_day=ready_day)
        self.units[serial] = u
        return u

    # ---- per-cycle ----
    def step(self, state: dict[str, dict[str, np.ndarray]], cycle: int) -> None:
        """Rogue early-failure hazard on installed rogue serials; ages advance. Draws keyed (seed, kind, cycle)."""
        for k in self.kinds:
            age = self.slot_age[k]
            mask = self.rogue_slot[k]
            if mask.any():
                u = self._rogue_rng[k].at(cycle).random(mask.size)
                h = self.cfg.rogue_floor + self.cfg.rogue_hazard0 * np.exp(-age / self.cfg.rogue_tau)
                fire = mask & (u < h) & ~state[k]["failed"]
                if fire.any():
                    state[k]["failed"] = np.where(fire, True, state[k]["failed"])
            self.slot_age[k] = age + 1.0

    # ---- removal -> shop -> replacement ----
    def _finding(self, rec: Removal, u: RotableUnit, draw: float) -> str:
        if rec.removal_reason == "nff" or rec.action == NFF_RETURN:
            return "nff"
        if u.rogue and rec.removal_reason in ("failure", "inspection_finding", "performance") and draw < self.cfg.rogue_nff_prob:
            return "nff"   # latent defect survives the shop tests
        if rec.removal_reason == "failure":
            return "confirmed"
        return "partial"

    def log_install(self, part: PartInstance, tail: str, position: str, day: float, source: str, aog: float, station: str = "ATL") -> None:
        self.install_log.append(InstallRecord(part.serial, part.part_no, part.kind, tail, position, _iso(day), part.tsn_h, part.csn,
                                              station, source, aog))

    def swap(self, world: Any, kind: str, unit: int, rec: Removal, now_day: float, tail_idx: int) -> None:
        reg = world.registry
        key = (kind, int(unit))
        station = self._tail_station[tail_idx] if tail_idx < len(self._tail_station) else "ATL"
        u = self.units[rec.serial]
        part = reg.parts[rec.serial]
        if reg.installed.get(key) == rec.serial:
            reg.remove(kind, unit)
        # ---- shop ----
        g = self._shop_rng[kind].at(world.fleet.cycle * 65536 + int(unit))
        d = g.random(3)
        z = float(g.standard_normal())
        finding = self._finding(rec, u, float(d[0]))
        rec.part_no = part.part_no
        rec.shop_finding = finding
        rec.installed_at = _iso(u.installed_day)
        turn = TURNAROUND_DAYS[kind] * self.cfg.turnaround_scale * math.exp(TURNAROUND_SIGMA * z - 0.5 * TURNAROUND_SIGMA ** 2)
        scrapped = False
        if finding == "nff":
            turn *= NFF_TURNAROUND_FACTOR   # unit returned unchanged: same serial, TSN/CSN/TSO/CSO, flags
        else:
            if finding == "confirmed" and float(d[1]) < self.cfg.ber_prob:
                scrapped = True
            else:
                q = rec.q if rec.action == "overhaul" else SHOP_REPAIR_Q   # Kijima: virtual age since overhaul scaled by q
                part.tso_h *= q
                part.cso *= q
                part.since_repair_cycles = 0.0
        part.shop_visits += 1
        u.shop_visits += 1
        u.removed_day = now_day
        part.installed_on = None
        if scrapped:
            u.status = "scrapped"
            part.status = "scrapped"
            nu = self._new_spare(reg, kind, 1.0, False, False, station, now_day + BER_LEAD_DAYS)
            nu.mod_status = u.mod_status
            self.exposure["purchases"] += 1.0
        else:
            u.status = "shop"
            part.status = "removed"
            u.station = station
            u.ready_day = now_day + turn
        self.shop_visits.append(ShopVisit(rec.serial, part.part_no, kind, rec.tail, rec.position, station, _iso(now_day),
                                          _iso(now_day + turn), float(turn), finding, rec.removal_reason, rec.action, scrapped))
        # ---- replacement: nearest ready stock first, else rush the earliest shop return ----
        cands = [c for c in self.units.values() if c.kind == kind and c.status == "shop" and c.serial != rec.serial]
        ready = sorted((c for c in cands if c.ready_day <= now_day), key=lambda c: (_hop(c.station, station), c.serial))
        source = "stock"
        aog = 0.0
        if ready:
            pick = ready[0]
            hops = _hop(pick.station, station)
            aog = FERRY_DAYS_PER_HOP * hops
            self.exposure["ferry_hops"] += hops
            self.exposure["stock_installs"] += 1.0
        elif cands:
            pick = min(cands, key=lambda c: (c.ready_day, c.serial))
            source = "rush"
            aog = min(AOG_CAP_DAYS, pick.ready_day - now_day)
            self.exposure["rush_installs"] += 1.0
        else:   # no other serial of the kind exists (zero spares): emergency purchase, full AOG cap
            pick = self._new_spare(reg, kind, 1.0, False, False, station, now_day)
            source = "purchase"
            aog = AOG_CAP_DAYS
            self.exposure["purchases"] += 1.0
        self.exposure["aog_days"] += aog
        pp = reg.parts[pick.serial]
        tail = world.tails[tail_idx]
        pos = world.component_name(kind, unit)
        inst_day = max(now_day, pick.removed_day)
        pick.status = "installed"
        pick.installed_day = inst_day
        pick.tails.append(tail)
        pp.status = "installed"
        pp.installed_on = tail
        pp.position = pos
        reg.installed[key] = pick.serial
        self.slot_mult[kind][unit] = pick.quality * MOD_HAZARD[pick.mod_status]
        self.slot_age[kind][unit] = 0.0
        self.rogue_slot[kind][unit] = pick.rogue
        rec.new_serial = pick.serial if rec.action == "replace" else None
        self.log_install(pp, tail, pos, inst_day, source, aog, station)

    def set_station(self, tail_idx: int, station: str) -> None:
        if tail_idx < len(self._tail_station):
            self._tail_station[tail_idx] = station

    def refresh_slots(self, registry: Any) -> None:
        """Rebuild per-slot multipliers / rogue masks from the installed serials (after restore)."""
        for k in self.kinds:
            for i in range(self.counts[k]):
                s = registry.installed.get((k, i))
                u = self.units.get(s) if s else None
                self.slot_mult[k][i] = 1.0 if u is None else u.quality * MOD_HAZARD[u.mod_status]
                self.rogue_slot[k][i] = bool(u.rogue) if u is not None else False

    # ---- views ----
    def truth(self) -> list[dict[str, Any]]:
        """HIDDEN ground truth per serial (validation only; never telemetry, never a pack input)."""
        return [{"serial": u.serial, "kind": u.kind, "part_number": u.part_no, "mod_status": u.mod_status, "quality": u.quality,
                 "rogue": u.rogue, "shop_visits": u.shop_visits, "status": u.status, "tails": list(u.tails), "provenance": PROVENANCE}
                for u in sorted(self.units.values(), key=lambda x: x.serial)]

    def lineage(self, serial: str) -> list[dict[str, Any]]:
        return [r.as_dict() for r in self.install_log if r.serial == serial]

    def stock(self, kind: str, day: float) -> dict[str, int]:
        out: dict[str, int] = {s: 0 for s in STATIONS}
        for u in self.units.values():
            if u.kind == kind and u.status == "shop" and u.ready_day <= day:
                out[u.station] = out.get(u.station, 0) + 1
        return out

    # ---- persistence ----
    def to_blob(self) -> dict[str, Any]:
        return {"units": [asdict(u) for u in sorted(self.units.values(), key=lambda x: x.serial)],
                "shop": [v.as_dict() for v in self.shop_visits], "installs": [r.as_dict() for r in self.install_log],
                "exposure": dict(self.exposure), "station": list(self._tail_station)}

    def arrays(self) -> dict[str, np.ndarray]:
        return {f"rot/age/{k}": a.copy() for k, a in self.slot_age.items()}

    def restore(self, blob: dict[str, Any], arrays: dict[str, np.ndarray], registry: Any) -> None:
        self.units = {d["serial"]: RotableUnit(**d) for d in blob["units"]}
        self.shop_visits = [ShopVisit(**d) for d in blob["shop"]]
        self.install_log = [InstallRecord(**d) for d in blob["installs"]]
        self.exposure = dict(blob["exposure"])
        self._tail_station = list(blob["station"])
        for k in self.kinds:
            self.slot_age[k] = np.array(arrays[f"rot/age/{k}"], copy=True)
        self.refresh_slots(registry)


# ---- ingest-schema export helpers (visible lineage only; hidden truth is NOT included) ----
def _ingest_reason(rec: Removal) -> str:
    if rec.shop_finding == "nff":
        return "nff_suspected"
    return "unscheduled" if rec.unscheduled else "scheduled"


def removal_rows(world: Any) -> list[dict[str, Any]]:
    """Physical removals (swap actions) as ``app.ingest`` PartRemovalEvent-shaped dicts (shop_finding: confirmed|nff)."""
    out = []
    for r in world.removals():
        if r.action not in SWAP_ACTIONS or r.shop_finding is None:
            continue
        out.append({"part_number": r.part_no, "serial": r.serial, "tail": r.tail, "position": r.position, "installed_at": r.installed_at,
                    "removed_at": r.time, "tsn_h": r.tsn_h, "csn": int(r.csn), "removal_reason": _ingest_reason(r),
                    "scheduled": not r.unscheduled, "shop_finding": "nff" if r.shop_finding == "nff" else "confirmed", "ata_chapter": None})
    return out


def install_rows(world: Any) -> list[dict[str, Any]]:
    """Installs as PartInstallEvent-shaped dicts (initial population included)."""
    if world.rotables is None:
        return []
    return [{"part_number": r.part_number, "serial": r.serial, "tail": r.tail, "position": r.position, "installed_at": r.installed_at,
             "tsn_h": r.tsn_h, "csn": int(r.csn)} for r in world.rotables.install_log]


__all__ = ["InstallRecord", "RotableConfig", "RotablePool", "RotableUnit", "ShopVisit", "SWAP_ACTIONS", "install_rows",
           "part_number", "removal_rows"]
