"""Cross-kind coupling graph: one kind's state becomes another kind's ``state['stress_ext']``.

Convention: every enabled edge yields a non-negative extra-stress fraction ``c`` per destination unit
(``gain * signal``, clamped to ``[0, edge.clamp]``). ``stress_ext = 1 + sum(c)`` clamped to ``[1, max_total]``.
Models treat ``stress_ext`` as a multiplier on their stress/damage drivers. Call ``apply`` before ``step_cycle``.

Unit alignment: by tail when both kinds have ``Fleet.unit_tail``; identical unit counts map 1:1; a single
source unit broadcasts; otherwise the edge is skipped. Missing kinds or state keys are skipped (logged once).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

log = logging.getLogger(__name__)

Signal = Callable[[dict[str, np.ndarray], np.ndarray | None], np.ndarray | None]


def _first(state: dict[str, np.ndarray], keys: tuple[str, ...]) -> np.ndarray | None:
    for k in keys:
        if k in state:
            return np.asarray(state[k], dtype=float)
    return None


def _hyd_deficit(state: dict[str, np.ndarray], dst_ctx: Any) -> np.ndarray | None:
    """1 - pump efficiency (0..1) or normalised pressure shortfall vs 3000 psi."""
    eff = _first(state, ("pump_efficiency",))
    if eff is not None:
        return np.clip(1.0 - eff, 0.0, 1.0)
    p = _first(state, ("pressure_psi", "pump_pressure_psi"))
    return None if p is None else np.clip(1.0 - p / 3000.0, 0.0, 1.0)


def _bus_deviation(nominal: float) -> Signal:
    def f(state: dict[str, np.ndarray], dst_ctx: Any) -> np.ndarray | None:
        v = _first(state, ("bus_voltage_v", "bus_voltage"))
        return None if v is None else np.abs(v - nominal) / nominal
    return f


def _engine_health_loss(state: dict[str, np.ndarray], dst_ctx: Any) -> np.ndarray | None:
    h = _first(state, ("health_proxy", "bleed_health", "egt_health"))
    return None if h is None else np.clip(1.0 - h, 0.0, 1.0)


def _apu_ground(state: dict[str, np.ndarray], dst_ctx: Any) -> np.ndarray | None:
    """Ground APU hours this cycle (from the destination unit's operating context), in hours."""
    return None if dst_ctx is None else np.asarray(dst_ctx.apu_hours, dtype=float)


def _brake_heat(t0: float, span: float) -> Signal:
    def f(state: dict[str, np.ndarray], dst_ctx: Any) -> np.ndarray | None:
        t = _first(state, ("disc_temp_c", "brake_temp_c"))
        return None if t is None else np.clip((t - t0) / span, 0.0, None)
    return f


@dataclass
class Edge:
    name: str
    src: str
    dst: str
    signal: Signal
    gain: float = 1.0
    clamp: float = 0.5
    enabled: bool = True
    needs_ctx: bool = False


def default_edges() -> list[Edge]:
    return [
        Edge("hydraulics>brakes", "hydraulics", "brakes", _hyd_deficit, gain=1.0, clamp=0.5),
        Edge("hydraulics>landing_gear", "hydraulics", "landing_gear", _hyd_deficit, gain=0.8, clamp=0.4),
        Edge("electrical>avionics", "electrical", "avionics", _bus_deviation(28.0), gain=4.0, clamp=0.6),
        Edge("engine>ecs", "engine", "ecs", _engine_health_loss, gain=0.6, clamp=0.4),
        Edge("apu>ecs", "apu", "ecs", _apu_ground, gain=0.15, clamp=0.3, needs_ctx=True),
        Edge("brakes>tires", "brakes", "tires", _brake_heat(300.0, 400.0), gain=0.5, clamp=0.5),
    ]


@dataclass
class CouplingGraph:
    edges: list[Edge] = field(default_factory=default_edges)
    max_total: float = 2.5
    _warned: set[str] = field(default_factory=set, repr=False)

    def set_enabled(self, name: str, on: bool) -> None:
        for e in self.edges:
            if e.name == name:
                e.enabled = on
                return
        raise KeyError(name)

    def _warn(self, key: str, msg: str) -> None:
        if key not in self._warned:
            self._warned.add(key)
            log.info("coupling: %s", msg)

    @staticmethod
    def _map(fleet: Any, src: str, dst: str) -> np.ndarray | None:
        """Index into source units for each destination unit, or None if not alignable."""
        nd, ns = fleet.counts[dst], fleet.counts[src]
        ts, td = fleet.unit_tail.get(src), fleet.unit_tail.get(dst)
        if ts is not None and td is not None:
            lookup = {int(t): j for j, t in reversed(list(enumerate(ts)))}
            if all(int(t) in lookup for t in td):
                return np.array([lookup[int(t)] for t in td], dtype=np.int64)
            return None
        if ns == nd:
            return np.arange(nd)
        if ns == 1:
            return np.zeros(nd, dtype=np.int64)
        return None

    def apply(self, fleet: Any, ctx: Any = None) -> dict[str, np.ndarray]:
        """Write ``state['stress_ext']`` for every destination kind touched; returns {kind: stress_ext}."""
        total: dict[str, np.ndarray] = {}
        for e in self.edges:
            if not e.enabled:
                continue
            if e.src not in fleet.state or e.dst not in fleet.state:
                self._warn(f"{e.name}:kind", f"edge {e.name} skipped: kind missing")
                continue
            dst_ctx = None
            if e.needs_ctx:
                if ctx is None:
                    self._warn(f"{e.name}:ctx", f"edge {e.name} skipped: no context")
                    continue
                dst_ctx = fleet._ctx_for(e.dst, ctx)
            sig = e.signal(fleet.state[e.src], dst_ctx)
            if sig is None:
                self._warn(f"{e.name}:key", f"edge {e.name} skipped: source key missing")
                continue
            m = self._map(fleet, e.src, e.dst)
            if m is None:
                self._warn(f"{e.name}:map", f"edge {e.name} skipped: units not alignable")
                continue
            c = np.clip(e.gain * (sig if sig.shape[0] == fleet.counts[e.dst] and e.needs_ctx else sig[m]), 0.0, e.clamp)
            total[e.dst] = total.get(e.dst, np.zeros(fleet.counts[e.dst])) + c
        out = {}
        for dst, c in total.items():
            out[dst] = np.clip(1.0 + c, 1.0, self.max_total)
            fleet.state[dst]["stress_ext"] = out[dst]
        return out
