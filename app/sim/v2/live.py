"""Live telemetry source backed by the v2 ``World`` (selected with ``SIM_ENGINE=v2``).

Drop-in for ``app.sim.telemetry.TelemetrySource`` as used by ``app.sim.service.Simulator`` (same attributes that the
service persists, same ``advance_leg`` / ``advance`` / ``restore`` / ``inject`` calls), so ingestion, the twin, health
scoring and the UI are unchanged. Differences by design:

* Rows are the v2 channels restricted to ``TELEMETRY_KEYS[kind]`` (the v1 contract); hidden truth never leaves ``World``.
* The ``World`` advances all tails one flight per world cycle. A tail's n-th completed leg consumes world cycle n; the
  world is stepped lazily and rows for slower tails wait in a cache (persisted), so legs may complete in any order.
* ``World(auto_maintain=False)``: nothing is repaired behind the planner's back; only ``restore`` (work orders) acts.
* Initial wear: non-engines start at ``1 - health`` of the fleet generator (= ``wear_profile`` start fraction), engines at
  ``cycles / (flights_per_model_cycle * 206.3)`` capped at 0.6 (flights since overhaul over the mean on-wing life).
* ``damage`` is the persisted checkpoint: a property that serialises the world (npz, base64) + served counters + row
  cache, and restores them when assigned, which gives the service's snapshot/rollback for free.
* ``inject``: ``anomaly`` forces the kind's fault mode at ``severity`` (engine: wear jumps to a late-life fraction so the
  sensors look late-life, plus HPT erosion in ground truth); ``accelerated_degradation`` sets ``stress_ext``
  = 1 + 4 * severity. ``advance`` then projects 8 cycles of the unit on a state copy so the event is visible at once.
"""

from __future__ import annotations

import base64
import io
from typing import Any

import numpy as np

from app.contracts.models import TelemetryRow
from app.ml.sensors import TELEMETRY_KEYS, kind_from_component_id
from app.sim.clock import START
from app.sim.constants import ENGINE_CMAPSS_MEAN_LIFE_CYCLES
from app.sim.v2.models.engine import PARAMS as ENGINE_PARAMS
from app.sim.v2.world import POLICY, World

SOURCE_LABEL = "simulated_v2_world"
MEAN_FIT_LIFE = ENGINE_CMAPSS_MEAN_LIFE_CYCLES  # C-MAPSS FD001 mean cycles to failure (shared constant)
ENGINE_START_CAP = 0.6
# v1 SPONTANEOUS_MODE analogue -> v2 fault library ids, per kind
ANOMALY_MODES: dict[str, tuple[str, ...]] = {
    "engine": ("hpt_blade_erosion",), "brakes": ("stuck_brake",), "landing_gear": ("strut_seal_leak",),
    "hydraulics": ("pump_cavitation",), "avionics": ("overheating",), "apu": ("starter_failure",), "tires": ("slow_leak",),
    "ecs": ("valve_stuck",), "electrical": ("cell_short",),
}
_PRE_AGED: dict[tuple, dict[str, np.ndarray]] = {}


def initial_wear(state: Any, world: World) -> dict[str, np.ndarray]:
    """Target wear fraction per (kind, unit) from a ``FleetState`` (see module docstring for the mapping)."""
    by_id = {c.component_id: c for c in state.components}
    mean_life = MEAN_FIT_LIFE * float(ENGINE_PARAMS["flights_per_model_cycle"])
    out: dict[str, np.ndarray] = {}
    for kind in world.kinds:
        w = np.zeros(world.counts[kind])
        for i in range(world.counts[kind]):
            comp = by_id.get(world.component_id(kind, i))
            if comp is None:
                continue
            w[i] = min(comp.cycles / mean_life, ENGINE_START_CAP) if kind == "engine" else min(max(1.0 - comp.health, 0.0), 0.95)
        out[kind] = w
    return out


def age_world(w: World, wear: dict[str, np.ndarray]) -> None:
    """``World.pre_age`` with a process-wide cache (same seed/tails/wear/time-scale -> same arrays)."""
    key = (w.seed, tuple(w.tails), float(ENGINE_PARAMS["flights_per_model_cycle"]), w.utilisation,
           tuple(round(float(x), 6) for k in sorted(wear) for x in wear[k]))
    if key not in _PRE_AGED:
        w.pre_age(wear)
        _PRE_AGED[key] = {k: {n: np.array(a, copy=True) for n, a in w.fleet.state[k].items()} for k in w.kinds}
        return
    for k, arrs in _PRE_AGED[key].items():
        for n, a in arrs.items():
            w.fleet.state[k][n] = np.array(a, copy=True)


def _pack(snap: dict[str, np.ndarray]) -> str:
    buf = io.BytesIO()
    np.savez_compressed(buf, **{k.replace("/", "__"): v for k, v in snap.items()})
    return base64.b64encode(buf.getvalue()).decode()


def _unpack(text: str) -> dict[str, np.ndarray]:
    with np.load(io.BytesIO(base64.b64decode(text)), allow_pickle=False) as z:
        return {k.replace("__", "/"): z[k] for k in z.files}


class V2Source:
    def __init__(self, seed: int = 1234, trajectories: Any = None, stochastic: bool = False) -> None:
        self.seed, self.stochastic = int(seed), bool(stochastic)
        self.trajectories: dict = {}
        self.positions: dict[str, int] = {}
        self.origins: dict[str, int] = {}
        self.units: dict = {}
        self.wear: dict = {}
        self.faults: dict = {}
        self.modes: dict = {}
        self.sensor_faults: dict = {}
        self.now = START
        self.world: World | None = None
        self._tails: list[str] = []
        self._index: dict[str, tuple[str, int]] = {}
        self._served: dict[str, int] = {}
        self._cache: dict[str, dict[str, float]] = {}

    # the service sets this to a provenance label; v2 always reports itself
    @property
    def source(self) -> str:
        return SOURCE_LABEL

    @source.setter
    def source(self, _value: str) -> None:
        pass

    # ---- world lifecycle ----
    def _build(self, tails: list[str]) -> World:
        self._tails = list(tails)
        self.world = World(self.seed, self._tails, stochastic=self.stochastic, auto_maintain=False)
        self._index = {self.world.component_id(k, i): (k, i) for k in self.world.kinds for i in range(self.world.counts[k])}
        return self.world

    def bind(self, state: Any, force: bool = False) -> None:
        """Create the world for ``state`` (pre-aged to the fleet's initial wear) unless one is already loaded."""
        if self.world is not None and not force:
            return
        tails = [a.tail for a in state.aircraft]
        w = self._build(tails)
        age_world(w, initial_wear(state, w))
        self._served, self._cache = {}, {}

    # ---- persisted checkpoint ----
    @property
    def damage(self) -> dict[str, Any]:
        if self.world is None:
            return {}
        meta = {"tails": self._tails, "served": self._served, "cache": self._cache, "seed": self.seed, "stochastic": self.stochastic}
        return {"v2": {"npz": _pack(self.world.snapshot()), "meta": meta}}

    @damage.setter
    def damage(self, value: dict[str, Any]) -> None:
        pack = (value or {}).get("v2")
        if not pack:
            return
        meta = pack["meta"]
        if self.world is None or self._tails != meta["tails"] or self.seed != meta["seed"]:
            self.seed, self.stochastic = int(meta["seed"]), bool(meta["stochastic"])
            self._build(meta["tails"])
        self.world.restore(_unpack(pack["npz"]))
        self.world.rows = []
        self._served = {k: int(v) for k, v in meta["served"].items()}
        self._cache = {k: dict(v) for k, v in meta["cache"].items()}

    # ---- advancing ----
    def _ensure(self, n: int) -> None:
        w = self.world
        while w.cycle < n:
            for r in w.step():
                self._cache[f"{r['component_id']}|{r['cycle']}"] = r["values"]
            w.rows = []

    def advance_leg(self, leg: Any, component_ids: list[str]) -> list[TelemetryRow]:
        n = self._served.get(leg.tail, 0) + 1
        self._served[leg.tail] = n
        self._ensure(n)
        rows = []
        for cid in component_ids:
            kind = kind_from_component_id(cid)
            vals = self._cache.pop(f"{cid}|{n}", None)
            cycle = self.positions.get(cid, 0) + 1
            self.positions[cid] = cycle
            keys = TELEMETRY_KEYS.get(kind, [])
            rows.append(TelemetryRow(tail=leg.tail, component_id=cid, cycle=cycle, ts=self.now,
                                     values={k: vals[k] for k in keys if vals and k in vals}))
        return rows

    def advance(self, tail: str, component_id: str, n_cycles: int) -> list[TelemetryRow]:
        if n_cycles < 0 or not isinstance(n_cycles, int):
            raise ValueError("n_cycles must be a nonnegative integer")
        kind, unit = self._index[component_id]
        keys = TELEMETRY_KEYS.get(kind, [])
        rows = []
        for vals in self.world.preview(kind, unit, n_cycles):
            cycle = self.positions.get(component_id, 0) + 1
            self.positions[component_id] = cycle
            rows.append(TelemetryRow(tail=tail, component_id=component_id, cycle=cycle, ts=self.now,
                                     values={k: vals[k] for k in keys if k in vals}))
        return rows

    # ---- scenario hooks ----
    def restore(self, component_id: str) -> str:
        """Work order completed: planned shop action for the kind (overhaul or replace per ``POLICY``)."""
        kind, unit = self._index[component_id]
        self.world.apply_action(kind, unit, POLICY[kind].wear_action, reason="scheduled", text="work order completed")
        self.positions[component_id] = 0
        self.origins[component_id] = 0
        return kind

    def inject(self, component_id: str, event_type: str, severity: float = 1.0) -> None:
        kind, unit = self._index[component_id]
        if event_type == "anomaly":
            if kind == "engine":
                # acute late-life event: wear jumps to p = 0.72 + 0.16 * severity of the life axis (severity 1 = ~25 model
                # cycles / ~2,100 flights left), with HPT erosion on top so the fault shows in ground truth
                self.world.raise_state(kind, unit, "progress", 0.72 + 0.16 * severity)
                self.world.inject_fault(kind, unit, "hpt_blade_erosion", 0.2 * severity)
            else:
                for mode in ANOMALY_MODES.get(kind, ()):
                    self.world.inject_fault(kind, unit, mode, severity)
        elif event_type == "accelerated_degradation":
            self.world.set_stress(kind, unit, 1.0 + 4.0 * severity)
