"""Simulator v2 world: one deterministic loop wiring drivers, faults, coupling, models, sensors and maintenance.

Per cycle: build per-tail ``OperatingContext`` -> ``FaultInjector.step`` per kind (hidden truth) ->
``CouplingGraph.apply`` -> ``Fleet.step_cycle`` -> ``emit_all`` (+ ``app.sim.sensor_faults`` layer when
``stochastic``) -> accrue ``PartInstance`` hours/cycles -> telemetry rows -> scheduled checks and a
condition-based policy executed through ``MaintenanceEngine``.

Units per tail: engine_1, engine_2 (only if an ``engine`` model is registered), brakes, landing_gear, hydraulics,
avionics, apu, tires, ecs, electrical. ``engine`` has 2 units per tail (unit ``2*t + p``); every other kind has one
(unit ``t``). ``Fleet.unit_tail`` maps units to tail indices so contexts and coupling align by tail.

Determinism: every random draw is keyed on (seed, stream, cycle) (see ``app.sim.v2.core.StreamRng``); the only
mutable state is captured by ``snapshot()``, so ``restore()`` + ``run()`` is bit-identical to an uninterrupted run.

Ground-truth faults (``ground_truth()``) are never written into telemetry rows. Policy thresholds in ``POLICY`` are
priors, not calibrated values.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, fields
from datetime import datetime, timedelta
from typing import Any, Sequence

import numpy as np

from app.sim import sensor_faults
from app.sim.clock import START
from app.sim.profile import FlightProfile, profile_for, profile_for_cycle
from app.sim.v2 import core
from app.sim.v2 import models as _models
from app.sim.v2.core import Fleet, StreamRng
from app.sim.v2.coupling import CouplingGraph
from app.sim.v2.drivers import OperatingContext, build_context
from app.sim.v2.faults import FaultInjector
from app.sim.v2.models.tires import PARAMS as TIRE_PARAMS
from app.sim.v2.maintenance import (NFF_RETURN, CheckDef, InspectionRecord, InstallRegistry, MaintenanceEngine, MelDeferral,
                                    PartInstance, Removal, ScheduledCheckGenerator)

_models.load_all()  # make sure every kind (incl. engine when present) is registered

PROVENANCE = "SIMULATED"
KIND_ORDER = ("engine", "hydraulics", "brakes", "landing_gear", "avionics", "apu", "tires", "ecs", "electrical")
UNITS_PER_TAIL = {"engine": 2}
STATIONS_ORDER = ("ATL", "DEN", "DFW", "JFK", "LAX", "MIA", "ORD", "SFO")
SENSOR_FAULT_RATE = 0.002  # per component per cycle (same prior as v1 telemetry)


@dataclass(frozen=True)
class KindPolicy:
    wear_below: float      # planned (condition-based) action when hi drops below this
    wear_action: str       # overhaul | replace
    check_days: float      # scheduled inspection interval, calendar days
    defer_ok: bool = False  # inspection findings may be deferred under MEL category C
    finding_action: str | None = None  # None: repair if hi > REPAIR_ABOVE else replace
    finding_below: float | None = None  # per-kind override of FINDING_BELOW (None: use the module default)


# Priors, not calibrated. finding_below tuned so a year of nominal operations lands in the prior bands in
# docs/RELIABILITY.md: hydraulics / electrical / gear degrade slowly, findings are actioned only when clearly worn.
POLICY: dict[str, KindPolicy] = {
    "engine": KindPolicy(0.15, "overhaul", 45.0, finding_action="repair"),  # a finding is repaired on wing; the shop visit is the hi < 0.15 performance path
    "brakes": KindPolicy(0.10, "replace", 30.0),
    "landing_gear": KindPolicy(0.10, "replace", 60.0, finding_below=0.3),
    "hydraulics": KindPolicy(0.15, "replace", 45.0, finding_below=0.3),
    "avionics": KindPolicy(0.10, "replace", 90.0, defer_ok=True),
    "apu": KindPolicy(0.15, "overhaul", 60.0, defer_ok=True),
    "tires": KindPolicy(0.15, "overhaul", 7.0, finding_action="overhaul", finding_below=0.2),
    "ecs": KindPolicy(0.15, "overhaul", 90.0, defer_ok=True),
    "electrical": KindPolicy(0.15, "replace", 60.0, finding_below=0.3),
}
FINDING_BELOW = 0.4        # inspection finding is actioned only if hi is below this (was 0.6: mild ageing was removed)
REPAIR_ABOVE = 0.3         # finding with hi above this is repaired, else replaced
NFF_WINDOW = 60            # cycles: repeat-defect window for intermittent avionics BITE reports
NFF_BITE_DELTA = 5         # BITE count increase within the window that triggers a pilot-reported removal
NFF_ESCALATE_N = 1         # NFF returns on one serial after which the next call swaps the unit (troubleshooting escalation)
NFF_HEALTH_MIN = 0.6       # a unit above this health index is serviceable (above every action threshold): an indication on it is a false call

# ---- no-fault-found (false call) priors, ALL ASSUMED (no citation), per kind ----
# (spurious fault indications per 1000 cycles per healthy unit, probability an indication ends in a shop removal).
# An indication is a BITE / CMC / crew-reported fault message that does not correspond to a degraded unit. The removal
# probability is the "false-call probability": the share of such indications acted on by pulling the unit rather than
# cleared by a reset / troubleshooting step. The unit then goes to the shop, passes, and is returned unchanged
# (Removal.action == "nff_return", nff=True). Expected NFF removals per 24 tails per year = 52.6 * rate * p_call.
#   avionics     : HIGH. Handled separately: repeat BITE reports (``NFF_BITE_DELTA`` in ``NFF_WINDOW``) trigger a removal and the
#                  shop finding is drawn from ``avionics.nff_prob`` (0.35 base, rising with BITE history).
#   hydraulics, electrical, ecs, apu : MODERATE (sensor / connector / indication-system false warnings).
#   mechanical (brakes, landing_gear, tires, engine): LOW (indications are physical: temperature, pressure, position).
FALSE_CALL: dict[str, tuple[float, float]] = {
    "hydraulics": (0.10, 0.5), "electrical": (0.10, 0.5), "ecs": (0.12, 0.5), "apu": (0.12, 0.5),
    "brakes": (0.02, 0.2), "landing_gear": (0.02, 0.2), "tires": (0.02, 0.1), "engine": (0.01, 0.2),
}

# ---- early-life (infant mortality) hazard multiplier after replace / overhaul, ASSUMED (no citation) ----
# (peak multiplier on the degradation rate right after the work, decay constant in cycles). m(age) = 1 + (M - 1) exp(-age/tau).
# Total extra wear is (M - 1) * tau cycles of life: a few tenths of a percent to ~2% of the part life (bounded, small).
# Consumables (brakes, tires) are wear-limited by design and carry no infant term.
INFANT: dict[str, tuple[float, float]] = {
    "avionics": (1.5, 200.0), "electrical": (1.3, 150.0), "hydraulics": (1.3, 150.0), "apu": (1.3, 100.0),
    "ecs": (1.2, 100.0), "landing_gear": (1.15, 200.0), "engine": (1.15, 300.0),
}
# ---- maintenance-induced fault: probability a repair / overhaul leaves a new low-severity fault (ASSUMED, no citation) ----
MX_INDUCED_P = {"repair": 0.03, "overhaul": 0.04}
MX_INDUCED_SEVERITY = 0.25


def _arrival_station(seed: int, tail: str, cycle: int) -> str:
    """Arrival station chosen exactly like ``profile_for_cycle`` (same RNG key and draw order)."""
    from app.sim.profile import _rng
    from app.sim.fleet import STATIONS
    rng = _rng(f"cycle|{seed}|{tail}|{cycle}")
    codes = sorted(STATIONS)
    dep = rng.choice(codes)
    return rng.choice([c for c in codes if c != dep])


def _arr_name(arr: str) -> str:
    return arr if arr in STATIONS_ORDER else "ATL"


class World:
    """Deterministic fleet world. ``legs_or_profiles``: None (synthetic per-cycle flights), a callable
    ``(tail, cycle) -> FlightProfile``, a sequence of ``FlightProfile`` (cycled, shared) or of ``Leg`` (cycled per tail)."""

    def __init__(self, seed: int, tails: Sequence[str], legs_or_profiles: Any = None, stochastic: bool = True,
                 coupling: bool = True, faults: bool = True, utilisation: float = 1.0, auto_maintain: bool = True) -> None:
        self.seed = int(seed)
        self.utilisation = float(utilisation)  # multiplies drivers.LEGS_PER_DAY (cycles per calendar day); 1.0 = 6 legs/day
        self.auto_maintain = bool(auto_maintain)  # False: only explicit actions (live service) change components
        self.tails = list(tails)
        self.stochastic = bool(stochastic)
        self._source = legs_or_profiles
        n = len(self.tails)
        avail = set(core.kinds())
        self.kinds = [k for k in KIND_ORDER if k in avail]
        self.counts = {k: n * UNITS_PER_TAIL.get(k, 1) for k in self.kinds}
        self.unit_tail = {k: np.repeat(np.arange(n), UNITS_PER_TAIL.get(k, 1)) for k in self.kinds}
        self.fleet = Fleet(self.seed, self.counts, unit_tail=self.unit_tail)
        self.injector = FaultInjector(self.seed, self.counts, enabled=None if faults else {m: False for m in _all_modes()})
        self.coupling = CouplingGraph(strict=True)
        self.coupling_enabled = bool(coupling)
        self.registry = InstallRegistry()
        for k in self.kinds:
            for i in range(self.counts[k]):
                self.registry.install(k, i, position=self.component_name(k, i), tail=self.tails[int(self.unit_tail[k][i])])
        self.maintenance = MaintenanceEngine(self.seed, self.registry)
        self._fc_rng = StreamRng(self.seed, "world.false_call")
        self._mx_rng = StreamRng(self.seed, "world.mx_induced")
        self._shop_rng = StreamRng(self.seed, "world.nff_shop")
        self.infant_age = {k: np.full(self.counts[k], 1e9) for k in self.kinds}  # cycles since last replace/overhaul (1e9: none yet)
        self.checks = ScheduledCheckGenerator([CheckDef(f"{k}_inspect", k, interval_days=POLICY[k].check_days) for k in self.kinds])
        self.sensor_state: dict[str, dict] = {}
        self.bite_hist: dict[int, list[tuple[int, int]]] = {}
        self.events: list[dict[str, Any]] = []
        self.rows: list[dict[str, Any]] = []
        self._ctx: OperatingContext | None = None
        self._legs: dict[str, list[Any]] = {}
        if self._source is not None and not callable(self._source):
            items = list(self._source)
            if items and hasattr(items[0], "tail"):
                for leg in items:
                    self._legs.setdefault(leg.tail, []).append(leg)
            self._items = items
        else:
            self._items = []

    # ---- naming ----
    def component_name(self, kind: str, unit: int) -> str:
        return f"engine_{unit % 2 + 1}" if kind == "engine" else kind

    def component_id(self, kind: str, unit: int) -> str:
        return f"{self.tails[int(self.unit_tail[kind][unit])]}/{self.component_name(kind, unit)}"

    @property
    def cycle(self) -> int:
        return self.fleet.cycle

    # ---- inputs ----
    def _profile(self, tail: str, cycle: int) -> tuple[FlightProfile, str]:
        """(profile, arrival station) for ``tail`` on 1-based ``cycle``."""
        src = self._source
        if src is None:
            return profile_for_cycle(self.seed, tail, cycle), _arrival_station(self.seed, tail, cycle)
        if callable(src):
            return src(tail, cycle), "ATL"
        if self._legs:
            legs = self._legs.get(tail) or next(iter(self._legs.values()))
            leg = legs[(cycle - 1) % len(legs)]
            return profile_for(self.seed, leg), leg.arr
        p = self._items[(cycle - 1) % len(self._items)]
        return p, "ATL"

    def _tail_now(self, tail_idx: int) -> datetime:
        days = float(self._ctx.calendar_days[tail_idx]) if self._ctx is not None else 0.0
        return START + timedelta(days=days)

    # ---- one cycle ----
    def step(self) -> list[dict[str, Any]]:
        cyc = self.fleet.cycle + 1
        pairs = [self._profile(t, cyc) for t in self.tails]
        ctx = build_context([p for p, _ in pairs], self.tails, self.seed, [_arr_name(a) for _, a in pairs], utilisation=self.utilisation, prev=self._ctx)
        self._ctx = ctx
        fl = self.fleet
        for k in self.kinds:
            self.injector.step(k, fl.state[k], fl.cycle, fl._ctx_for(k, ctx))
        if self.coupling_enabled:
            self.coupling.apply(fl, ctx)
        saved: dict[str, np.ndarray] = {}
        for k in self.kinds:
            if k in INFANT:
                m, tau = INFANT[k]
                saved[k] = fl.state[k]["stress_ext"]
                fl.state[k]["stress_ext"] = saved[k] * (1.0 + (m - 1.0) * np.exp(-self.infant_age[k] / tau))
        fl.step_cycle(ctx)
        for k, v in saved.items():
            fl.state[k]["stress_ext"] = v
        for k in self.kinds:
            self.infant_age[k] = self.infant_age[k] + 1.0
        emitted = fl.emit_all(ctx)
        for k in self.kinds:
            self.registry.accrue(k, fl._ctx_for(k, ctx).flight_hours, 1.0)
        rows = self._rows(emitted, ctx, fl.cycle)
        if self.auto_maintain:
            self._maintain(ctx)
        self.rows.extend(rows)
        return rows

    def run(self, n_cycles: int) -> list[dict[str, Any]]:
        if n_cycles < 0:
            raise ValueError("n_cycles must be nonnegative")
        out: list[dict[str, Any]] = []
        for _ in range(int(n_cycles)):
            out.extend(self.step())
        return out

    # ---- telemetry ----
    def _rows(self, emitted: dict[str, dict[str, np.ndarray]], ctx: OperatingContext, cycle: int) -> list[dict[str, Any]]:
        rows = []
        for k in self.kinds:
            chans = emitted[k]
            for i in range(self.counts[k]):
                t = int(self.unit_tail[k][i])
                cid = self.component_id(k, i)
                values = {c: float(v[i]) for c, v in chans.items()}
                if self.stochastic:
                    values = self._sensor_layer(cid, cycle, values)
                ts = (START + timedelta(days=float(ctx.calendar_days[t]))).isoformat()
                rows.append({"tail": self.tails[t], "component_id": cid, "cycle": cycle, "ts": ts, "values": values})
        return rows

    def _sensor_layer(self, cid: str, cycle: int, values: dict[str, float]) -> dict[str, float]:
        d = hashlib.sha256(f"sensor|{self.seed}|{cid}|{cycle}".encode()).digest()
        rng = random.Random(int.from_bytes(d[:8], "big"))
        state = self.sensor_state.get(cid) or sensor_faults.empty_state()
        if values and sensor_faults.stochastic_onset(rng, SENSOR_FAULT_RATE):
            key = rng.choice(sorted(values))
            mode = rng.choice(sensor_faults.MODES)
            magnitude = abs(values[key]) * rng.uniform(0.01, 0.05) or 0.01
            state = sensor_faults.start_fault(state, key, mode, cycle, rng.randint(3, 15), magnitude)
        out, state = sensor_faults.apply(cid, cycle, values, state, rng)
        self.sensor_state[cid] = state
        return out

    # ---- maintenance ----
    def _do(self, kind: str, unit: int, action: str, reason: str, text: str, ctx: OperatingContext, induce: bool = False) -> None:
        st = self.fleet.state[kind]
        t = int(self.unit_tail[kind][unit])
        now = START + timedelta(days=float(ctx.calendar_days[t]))
        hi0 = float(st["hi"][unit])
        if action not in ("inspect", NFF_RETURN):   # a no-fault-found return leaves faults, MEL items and state untouched
            # An on-wing engine repair (borescope blade blend / compressor wash) only clears fouling: eroded blades stay eroded
            # until a shop visit, so hpt_blade_erosion keeps acting (the model also caps the health a repair gives back).
            wash_only = kind == "engine" and action == "repair"
            self.injector.clear(kind, [unit], self.fleet.cycle, st, modes=["compressor_fouling"] if wash_only else None)
            self.maintenance.mel.close(kind, unit, now)
        if action == NFF_RETURN:
            recs = self.maintenance.shop_check(self.fleet, kind, [unit], reason="nff", now=now, reason_text=text)
        else:
            recs = self.maintenance.apply(self.fleet, kind, [unit], action, reason=reason, now=now, reason_text=text)
            if action in ("replace", "overhaul") and recs and not getattr(recs[0], "nff", False):
                self.infant_age[kind][unit] = 0.0
        ev: dict[str, Any] = {"cycle": self.fleet.cycle, "kind": kind, "unit": unit, "tail": self.tails[t], "action": action,
                              "reason": reason, "hi_before": hi0, "hi_after": float(st["hi"][unit])}
        if action == "inspect":
            ev["detected"] = bool(recs[0].detected)
        elif induce and action in MX_INDUCED_P and not getattr(recs[0], "nff", False):
            mode = self._maybe_induce(kind, unit, action)
            if mode:
                ev["induced_fault"] = mode
        self.events.append(ev)

    def _maybe_induce(self, kind: str, unit: int, action: str) -> str | None:
        """Maintenance-induced fault: with probability ``MX_INDUCED_P[action]`` the work leaves a low-severity fault."""
        from app.sim.v2.faults import modes_for
        modes = modes_for(kind)
        if not modes:
            return None
        u = self._mx_rng.at(self.fleet.cycle * 65536 + unit).random(2)
        if u[0] >= MX_INDUCED_P[action]:
            return None
        mode = modes[min(int(u[1] * len(modes)), len(modes) - 1)]
        self.inject_fault(kind, unit, mode.id, MX_INDUCED_SEVERITY)
        return mode.id

    def _wear_driven(self, kind: str, i: int) -> bool:
        """True when a consumable (tires, brakes) is at its wear / retread limit and nothing else is wrong: no active
        injected fault and (tires) no cut damage or pressure loss. Such a change is a planned ``wear_limit`` removal."""
        if kind not in ("tires", "brakes"):
            return False
        if any(self.injector.active(kind, m.id, i) for m in self.injector._modes.get(kind, [])):
            return False
        st = self.fleet.state[kind]
        if kind == "brakes":
            return True
        pr = TIRE_PARAMS
        h_p = (float(st["p_cold"][i]) / pr["P_SET_PSI"] - pr["P_LOW_FRAC"]) / (1.0 - pr["P_LOW_FRAC"])
        return float(st["cut_dmg"][i]) < 0.2 and h_p > 0.5

    def _maintain(self, ctx: OperatingContext) -> None:
        fl = self.fleet
        for k in self.kinds:
            st = fl.state[k]
            pol = POLICY[k]
            ck = fl._ctx_for(k, ctx)
            hi, failed = st["hi"], st["failed"]
            done: set[int] = set()
            for i in np.flatnonzero(failed):
                wear = self._wear_driven(k, int(i))
                self._do(k, int(i), "replace", "wear_limit" if wear else "failure",
                         "wear/retread limit reached" if wear else "failed in service", ctx, induce=True)
                done.add(int(i))
            for i in np.flatnonzero(~failed & (hi < pol.wear_below)):
                i = int(i)
                if k == "engine":   # EGT-margin / performance erosion driven shop visit (hi is its proxy), not a calendar task
                    self._do(k, i, pol.wear_action, "performance", "performance restoration: EGT margin / health below threshold", ctx, induce=True)
                else:
                    self._do(k, i, pol.wear_action, "scheduled", "condition-based: health below threshold", ctx, induce=True)
                done.add(i)
            if k == "avionics":
                self._nff_removals(k, ctx, done)
            else:
                self._false_calls(k, ctx, done)
            for i in range(self.counts[k]):
                if i in done:
                    continue
                due = self.checks.due(k, i, float(ck.total_flight_hours[i]), float(ck.flight_cycles[i]), float(ck.calendar_days[i]))
                for d in due:
                    self.checks.accomplish(d.check_id, i, float(ck.total_flight_hours[i]), float(ck.flight_cycles[i]), float(ck.calendar_days[i]))
                    self._do(k, i, "inspect", "scheduled", f"scheduled check {d.check_id}", ctx)
                    detected = self.events[-1]["detected"]
                    h = float(st["hi"][i])
                    # engines: a borescope finding is a defect (an active fault), not mere wear; wear is handled by the
                    # condition-based shop-visit threshold. Without this every borescope on a half-worn engine would trigger
                    # an on-wing repair that resets part of its wear, so engines would never reach a shop visit.
                    defect = k != "engine" or any(self.injector.active(k, m.id, i) for m in self.injector._modes.get(k, []))
                    if detected and h < (FINDING_BELOW if pol.finding_below is None else pol.finding_below) and defect:
                        t = int(self.unit_tail[k][i])
                        already = any(m.kind == k and m.unit == i for m in self.maintenance.mel.open_items())
                        if pol.defer_ok and h > REPAIR_ABOVE and not already:
                            self.maintenance.mel.defer(k, i, "C", self._tail_now(t), description="deferred inspection finding", tail=self.tails[t])
                        elif not already:
                            act = pol.finding_action or ("repair" if h > REPAIR_ABOVE else "replace")
                            wear = self._wear_driven(k, i)
                            self._do(k, i, act, "wear_limit" if wear else "inspection_finding",
                                     "wear limit reached at inspection" if wear else "inspection finding", ctx, induce=True)
                    break
        self._mel_expiry(ctx)

    def _nff_removals(self, kind: str, ctx: OperatingContext, done: set[int]) -> None:
        """Avionics: repeat BITE reports trigger a removal; the shop finding is drawn from ``avionics.nff_prob``.

        NFF -> unit returned unchanged (``nff_return``); otherwise a confirmed failure is replaced (reason ``failure``) and
        confirmed degradation is replaced as an ``inspection_finding``. Shop draws are keyed (seed, cycle, unit).
        """
        from app.sim.v2.models import avionics as av
        st = self.fleet.state[kind]
        bite = st["bite_fault_count"]
        cyc = self.fleet.cycle
        for i in range(self.counts[kind]):
            h = self.bite_hist.setdefault(i, [])
            h.append((cyc, int(bite[i])))
            while h and h[0][0] < cyc - NFF_WINDOW:
                h.pop(0)
            if i in done or len(h) < 2:
                continue
            if h[-1][1] - h[0][1] >= NFF_BITE_DELTA:
                self.bite_hist[i] = []
                finding = av.removal_reason(st, np.array([i]), self._shop_rng.at(cyc * 65536 + i))[0]
                part = self.registry.on_unit(kind, i)
                prior_nff = 0 if part is None else sum(1 for r in self.registry.history(part.serial) if r.nff)
                if finding == "NFF" and prior_nff >= NFF_ESCALATE_N:
                    # persistent fault the shop cannot reproduce: the box is swapped to clear it (counted as a confirmed removal)
                    self._do(kind, i, "replace", "failure", "repeat NFF: box swapped to clear persistent fault", ctx, induce=True)
                elif finding == "NFF":
                    self._do(kind, i, NFF_RETURN, "nff", "repeat BITE report: box removed, no fault found, returned to service", ctx)
                elif finding == "confirmed_failure":
                    self._do(kind, i, "replace", "failure", "repeat BITE report: box failed at shop", ctx, induce=True)
                else:
                    self._do(kind, i, "replace", "inspection_finding", "repeat BITE report: degradation confirmed at shop", ctx, induce=True)
                done.add(i)

    def _false_calls(self, kind: str, ctx: OperatingContext, done: set[int]) -> None:
        """Non-avionics NFF: spurious indication on a healthy unit that ends in a removal (see ``FALSE_CALL``)."""
        rate, p_call = FALSE_CALL.get(kind, (0.0, 0.0))
        if rate <= 0.0:
            return
        n = self.counts[kind]
        off = sum(self.counts[k] for k in self.kinds[:self.kinds.index(kind)])
        total = sum(self.counts[k] for k in self.kinds)
        u = self._fc_rng.at(self.fleet.cycle).random((total, 2))[off:off + n]
        st = self.fleet.state[kind]
        hot = (u[:, 0] < rate / 1000.0) & (u[:, 1] < p_call) & (st["hi"] > NFF_HEALTH_MIN) & ~st["failed"]
        for i in np.flatnonzero(hot):
            i = int(i)
            if i not in done:
                self._do(kind, i, NFF_RETURN, "nff", "false fault indication: unit removed, no fault found, returned to service", ctx)
                done.add(i)

    def _mel_expiry(self, ctx: OperatingContext) -> None:
        for d in list(self.maintenance.mel.open_items()):
            t = self.tails.index(d.tail) if d.tail in self.tails else 0
            if d.expired(self._tail_now(t)):
                self._do(d.kind, d.unit, "repair", "deferred_defect_rectification", f"MEL {d.id} expired", ctx, induce=True)
                self.maintenance.mel.close(d.kind, d.unit, self._tail_now(t))


    # ---- scenario / live-service hooks ----
    def pre_age(self, wear: dict[str, Sequence[float]], stress: float = 15.0, max_steps: int = 4000) -> int:
        """Start units part-worn: ``wear[kind][unit]`` is the target wear fraction (health index 1 - wear).

        A throwaway faultless world is run with ``stress_ext`` raised for units still above their target health and
        zero for units that reached it; the resulting arrays replace this world's state (stress back to 1, cycle
        counter and RNG streams untouched, so determinism is unchanged). Returns the steps used.
        """
        tmp = World(self.seed, self.tails, self._source, stochastic=False, coupling=False, faults=False,
                    utilisation=self.utilisation, auto_maintain=False)
        target = {k: 1.0 - np.clip(np.asarray(wear[k], dtype=float), 0.0, 0.95) for k in wear if k in tmp.kinds}
        frozen: dict[str, dict[str, np.ndarray]] = {k: {n: np.array(a, copy=True) for n, a in tmp.fleet.state[k].items()} for k in tmp.kinds}
        done = {k: np.zeros(tmp.counts[k], dtype=bool) for k in tmp.kinds}
        steps = 0
        for steps in range(0, max_steps + 1):
            active = False
            for k, tgt in target.items():
                st = tmp.fleet.state[k]
                hi = np.asarray(tmp.fleet.models[k].health_index(st), dtype=float)
                hit = ~done[k] & ((hi <= tgt) | np.asarray(st["failed"], dtype=bool))
                for i in np.flatnonzero(hit):  # capture the unit the moment it reaches its target (calendar effects keep acting)
                    for n, a in st.items():
                        frozen[k].setdefault(n, np.array(a, copy=True))[i] = a[i]
                done[k] |= hit
                # taper the acceleration near the target so a coarse step cannot overshoot far
                st["stress_ext"] = np.where(done[k], 0.0, np.clip(40.0 * (hi - tgt), 4.0, float(stress)))
                active = active or not bool(done[k].all())
            if not active or steps == max_steps:
                break
            tmp.step()
        for k in tmp.kinds:
            if k in target:
                for i in np.flatnonzero(~done[k]):
                    for n, a in tmp.fleet.state[k].items():
                        frozen[k].setdefault(n, np.array(a, copy=True))[i] = a[i]
            for name, arr in frozen[k].items():
                self.fleet.state[k][name] = arr
            self.fleet.state[k]["stress_ext"] = np.ones(self.counts[k])
        return steps

    def _preview_ctx(self) -> OperatingContext:
        if self._ctx is not None:
            return self._ctx
        pairs = [self._profile(t, 1) for t in self.tails]
        return build_context([p for p, _ in pairs], self.tails, self.seed, [_arr_name(a) for _, a in pairs], utilisation=self.utilisation)

    def inject_fault(self, kind: str, unit: int, mode_id: str, severity: float = 1.0) -> None:
        """Force fault ``mode_id`` on one unit at ``severity`` (0..1) right now; recorded as ground truth.

        The onset is back-dated by the cycles the ramp needs to reach ``severity`` (it may lie before cycle 0: the injector marks
        "no fault" with ``NO_ONSET``, not a negative number), so the fault is active at that severity immediately and keeps
        ramping to 1 over the documented ramp length."""
        from app.sim.v2.faults import GroundTruthFault, LIBRARY
        mode = LIBRARY[mode_id]
        if mode.kind != kind:
            raise ValueError(f"fault {mode_id} does not apply to {kind}")
        sev = float(np.clip(severity, 0.0, 1.0))
        inj, cyc = self.injector, self.fleet.cycle
        key = (kind, mode_id)
        if inj.active(kind, mode_id, unit) and float(inj.severity(kind, mode, cyc)[unit]) >= sev:
            return
        lo, hi = 0, max(int(mode.ramp_cycles) * 20, 1)
        if float(mode.severity(hi)) >= sev:
            while lo < hi:
                mid = (lo + hi) // 2
                if float(mode.severity(mid)) >= sev:
                    hi = mid
                else:
                    lo = mid + 1
        inj.onset[key][unit] = cyc - hi
        rec = GroundTruthFault(kind, int(unit), mode_id, int(cyc - hi), mode.ramp_cycles, mode.shape).as_dict()
        inj._log.append(rec)
        inj._open[(kind, mode_id, int(unit))] = rec
        self.fleet.state[kind][f"fault_{mode_id}"] = inj.severity(kind, mode, cyc)

    def raise_state(self, kind: str, unit: int, key: str, value: float) -> None:
        """Raise a latent state variable (never lowers it) and refresh derived health (scenario hook)."""
        st = self.fleet.state[kind]
        st[key][unit] = max(float(st[key][unit]), float(value))
        upd = getattr(self.fleet.models[kind], "_update", None)
        if upd is not None:
            upd(st)

    def set_stress(self, kind: str, unit: int, stress: float) -> None:
        self.fleet.state[kind]["stress_ext"][unit] = float(stress)

    def apply_action(self, kind: str, unit: int, action: str, reason: str = "scheduled", text: str = "explicit action") -> None:
        """Explicit maintenance on one unit (clears injected faults, resets stress, logs removal/inspection)."""
        self._do(kind, unit, action, reason, text, self._preview_ctx())
        if action != "inspect":
            self.fleet.state[kind]["stress_ext"][unit] = 1.0

    def preview(self, kind: str, unit: int, n: int) -> list[dict[str, float]]:
        """The next ``n`` emissions of one unit projected on a COPY of its kind's state (world state untouched).

        Active faults and ``stress_ext`` are honoured; used to give a freshly injected event immediate telemetry.
        """
        fl = self.fleet
        model = fl.models[kind]
        ctx = fl._ctx_for(kind, self._preview_ctx())
        st = {k: np.array(v, copy=True) for k, v in fl.state[kind].items()}
        out = []
        for j in range(n):
            for m in self.injector._modes.get(kind, []):
                st[f"fault_{m.id}"] = self.injector.severity(kind, m, fl.cycle + j)
            c = 10**9 + fl.cycle * 64 + j
            model.step(st, ctx, fl._rng[kind]["step"].at(c))
            em = model.emit(st, ctx, fl._rng[kind]["emit"].at(c))
            out.append({ch: float(v[unit]) for ch, v in em.items()})
        return out

    # ---- truth / views ----
    def ground_truth(self) -> list[dict[str, Any]]:
        """Hidden fault truth (never in telemetry rows)."""
        return self.injector.ground_truth()

    def removals(self) -> list[Removal]:
        return self.maintenance.removals

    def health_index(self) -> dict[str, np.ndarray]:
        return self.fleet.health_index()

    # ---- persistence ----
    def snapshot(self) -> dict[str, np.ndarray]:
        out: dict[str, np.ndarray] = {f"fleet/{k}": v for k, v in self.fleet.snapshot().items()}
        for (k, m), a in self.injector.onset.items():
            out[f"inj/onset/{k}/{m}"] = a.copy()
        for k, a in self.infant_age.items():
            out[f"infant/{k}"] = a.copy()
        blob = {
            "inj_log": self.injector.snapshot()["log"],
            "parts": [p.as_dict() for p in self.registry.parts.values()],
            "installed": [[k, i, s] for (k, i), s in self.registry.installed.items()],
            "serial_n": self.registry._serial_n,
            "removals": [r.as_dict() for r in self.registry.removals],
            "inspections": [r.as_dict() for r in self.maintenance.inspections],
            "n_inspect": self.maintenance._n_inspect,
            "mel": [d.as_dict() for d in self.maintenance.mel.items],
            "checks": [[cid, u, list(v)] for (cid, u), v in self.checks._last.items()],
            "sensor": self.sensor_state,
            "bite": {str(i): h for i, h in self.bite_hist.items()},
            "events": self.events,
        }
        out["blob"] = np.array(json.dumps(blob, sort_keys=True))
        has_ctx = self._ctx is not None
        out["ctx/present"] = np.array(has_ctx)
        if has_ctx:
            for f in fields(OperatingContext):
                out[f"ctx/{f.name}"] = np.array(getattr(self._ctx, f.name), copy=True)
        return out

    def restore(self, snap: dict[str, np.ndarray]) -> None:
        self.fleet.restore({k[len("fleet/"):]: v for k, v in snap.items() if k.startswith("fleet/")})
        blob = json.loads(str(snap["blob"]))
        self.injector.restore({"onset": {f"{k}/{m}": np.asarray(snap[f"inj/onset/{k}/{m}"]) for (k, m) in self.injector.onset},
                               "log": blob["inj_log"]})
        for k in self.kinds:
            key = f"infant/{k}"
            self.infant_age[k] = np.array(snap[key], copy=True) if key in snap else np.full(self.counts[k], 1e9)
        reg = self.registry
        reg.parts = {d["serial"]: PartInstance(**d) for d in blob["parts"]}
        reg.installed = {(k, int(i)): s for k, i, s in blob["installed"]}
        reg._serial_n = int(blob["serial_n"])
        reg.removals = [Removal(**d) for d in blob["removals"]]
        eng = self.maintenance
        eng.inspections = [InspectionRecord(**d) for d in blob["inspections"]]
        eng._n_inspect = int(blob["n_inspect"])
        eng.mel.items = []
        for d in blob["mel"]:
            d = dict(d)
            d.pop("expires", None)
            d.pop("interval_text", None)
            d["opened"] = datetime.fromisoformat(d["opened"])
            d["closed"] = datetime.fromisoformat(d["closed"]) if d["closed"] else None
            eng.mel.items.append(MelDeferral(**d))
        self.checks._last = {(cid, int(u)): tuple(v) for cid, u, v in blob["checks"]}
        self.sensor_state = blob["sensor"]
        self.bite_hist = {int(i): [tuple(x) for x in h] for i, h in blob["bite"].items()}
        self.events = blob["events"]
        self.rows = []
        if bool(snap["ctx/present"]):
            self._ctx = OperatingContext(**{f.name: np.array(snap[f"ctx/{f.name}"], copy=True) for f in fields(OperatingContext)})
        else:
            self._ctx = None

    def state_hash(self) -> str:
        h = hashlib.sha256()
        snap = self.snapshot()
        for key in sorted(snap):
            a = np.ascontiguousarray(snap[key])
            h.update(key.encode())
            h.update(str(a.dtype).encode())
            h.update(a.tobytes())
        return h.hexdigest()


def _all_modes() -> list[str]:
    from app.sim.v2.faults import LIBRARY
    return list(LIBRARY)


__all__ = ["KindPolicy", "POLICY", "World"]
