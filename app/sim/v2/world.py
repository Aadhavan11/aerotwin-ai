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
from app.sim.v2.core import Fleet
from app.sim.v2.coupling import CouplingGraph
from app.sim.v2.drivers import OperatingContext, build_context
from app.sim.v2.faults import FaultInjector
from app.sim.v2.maintenance import (CheckDef, InspectionRecord, InstallRegistry, MaintenanceEngine, MelDeferral,
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


# Priors, not calibrated.
POLICY: dict[str, KindPolicy] = {
    "engine": KindPolicy(0.15, "overhaul", 45.0),
    "brakes": KindPolicy(0.10, "replace", 30.0),
    "landing_gear": KindPolicy(0.10, "replace", 60.0),
    "hydraulics": KindPolicy(0.15, "replace", 45.0),
    "avionics": KindPolicy(0.10, "replace", 90.0, defer_ok=True),
    "apu": KindPolicy(0.15, "overhaul", 60.0, defer_ok=True),
    "tires": KindPolicy(0.15, "overhaul", 7.0, finding_action="overhaul"),
    "ecs": KindPolicy(0.15, "overhaul", 90.0, defer_ok=True),
    "electrical": KindPolicy(0.15, "replace", 60.0),
}
FINDING_BELOW = 0.6        # inspection finding is actioned only if hi is below this
REPAIR_ABOVE = 0.3         # finding with hi above this is repaired, else replaced
NFF_WINDOW = 60            # cycles: repeat-defect window for intermittent avionics BITE reports
NFF_BITE_DELTA = 5         # BITE count increase within the window that triggers a pilot-reported removal


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
                 coupling: bool = True, faults: bool = True) -> None:
        self.seed = int(seed)
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
        ctx = build_context([p for p, _ in pairs], self.tails, self.seed, [_arr_name(a) for _, a in pairs], prev=self._ctx)
        self._ctx = ctx
        fl = self.fleet
        for k in self.kinds:
            self.injector.step(k, fl.state[k], fl.cycle, fl._ctx_for(k, ctx))
        if self.coupling_enabled:
            self.coupling.apply(fl, ctx)
        fl.step_cycle(ctx)
        emitted = fl.emit_all(ctx)
        for k in self.kinds:
            self.registry.accrue(k, fl._ctx_for(k, ctx).flight_hours, 1.0)
        rows = self._rows(emitted, ctx, fl.cycle)
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
    def _do(self, kind: str, unit: int, action: str, reason: str, text: str, ctx: OperatingContext) -> None:
        st = self.fleet.state[kind]
        t = int(self.unit_tail[kind][unit])
        now = START + timedelta(days=float(ctx.calendar_days[t]))
        hi0 = float(st["hi"][unit])
        if action != "inspect":
            self.injector.clear(kind, [unit], self.fleet.cycle, st)
            self.maintenance.mel.close(kind, unit, now)
        recs = self.maintenance.apply(self.fleet, kind, [unit], action, reason=reason, now=now, reason_text=text)
        ev: dict[str, Any] = {"cycle": self.fleet.cycle, "kind": kind, "unit": unit, "tail": self.tails[t], "action": action,
                              "reason": reason, "hi_before": hi0, "hi_after": float(st["hi"][unit])}
        if action == "inspect":
            ev["detected"] = bool(recs[0].detected)
        self.events.append(ev)

    def _maintain(self, ctx: OperatingContext) -> None:
        fl = self.fleet
        for k in self.kinds:
            st = fl.state[k]
            pol = POLICY[k]
            ck = fl._ctx_for(k, ctx)
            hi, failed = st["hi"], st["failed"]
            done: set[int] = set()
            for i in np.flatnonzero(failed):
                self._do(k, int(i), "replace", "failure", "failed in service", ctx)
                done.add(int(i))
            for i in np.flatnonzero(~failed & (hi < pol.wear_below)):
                i = int(i)
                self._do(k, i, pol.wear_action, "scheduled", "condition-based: health below threshold", ctx)
                done.add(i)
            if k == "avionics":
                self._nff_removals(k, ctx, done)
            for i in range(self.counts[k]):
                if i in done:
                    continue
                due = self.checks.due(k, i, float(ck.total_flight_hours[i]), float(ck.flight_cycles[i]), float(ck.calendar_days[i]))
                for d in due:
                    self.checks.accomplish(d.check_id, i, float(ck.total_flight_hours[i]), float(ck.flight_cycles[i]), float(ck.calendar_days[i]))
                    self._do(k, i, "inspect", "scheduled", f"scheduled check {d.check_id}", ctx)
                    detected = self.events[-1]["detected"]
                    h = float(st["hi"][i])
                    if detected and h < FINDING_BELOW:
                        t = int(self.unit_tail[k][i])
                        already = any(m.kind == k and m.unit == i for m in self.maintenance.mel.open_items())
                        if pol.defer_ok and h > REPAIR_ABOVE and not already:
                            self.maintenance.mel.defer(k, i, "C", self._tail_now(t), description="deferred inspection finding", tail=self.tails[t])
                        elif not already:
                            act = pol.finding_action or ("repair" if h > REPAIR_ABOVE else "replace")
                            self._do(k, i, act, "inspection_finding", "inspection finding", ctx)
                    break
        self._mel_expiry(ctx)

    def _nff_removals(self, kind: str, ctx: OperatingContext, done: set[int]) -> None:
        bite = self.fleet.state[kind]["bite_fault_count"]
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
                self._do(kind, i, "replace", "failure", "repeat BITE report: box removed for troubleshooting", ctx)
                done.add(i)

    def _mel_expiry(self, ctx: OperatingContext) -> None:
        for d in list(self.maintenance.mel.open_items()):
            t = self.tails.index(d.tail) if d.tail in self.tails else 0
            if d.expired(self._tail_now(t)):
                self._do(d.kind, d.unit, "repair", "deferred_defect_rectification", f"MEL {d.id} expired", ctx)
                self.maintenance.mel.close(d.kind, d.unit, self._tail_now(t))

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
