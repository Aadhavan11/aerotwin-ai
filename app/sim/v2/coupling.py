"""Cross-kind coupling graph: one kind's state becomes another kind's ``state['stress_ext']``.

Convention: every enabled edge yields a non-negative extra-stress fraction ``c`` per destination unit
(``gain * signal``, clamped to ``[0, edge.clamp]``). ``stress_ext = 1 + sum(c)`` clamped to ``[1, max_total]``.
Models treat ``stress_ext`` as a multiplier on their stress/damage drivers. Call ``apply`` before ``step_cycle``.

Unit alignment: by tail when both kinds have ``Fleet.unit_tail``; identical unit counts map 1:1; a single
source unit broadcasts; otherwise the edge is skipped.

Skips are never silent: a kind that is not registered is an expected skip (logged at INFO once); a *registered* source
kind that lacks a state key the edge reads raises ``MissingKey`` when ``strict`` and is otherwise logged once at
WARNING. ``report()`` gives per-edge ``applied_count`` / ``skipped_count`` / ``skipped_reason``.

Real keys read: hydraulics ``eta_loss, leak, precharge_loss``; electrical ``bus_voltage_v`` (noise-free, written by
the model each step); engine ``health_proxy`` or ``hi``; brakes ``temp_c``; apu: destination ctx ``apu_hours``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

import numpy as np

log = logging.getLogger(__name__)

Signal = Callable[[dict[str, np.ndarray], np.ndarray | None], np.ndarray | None]


Signal = Callable[[dict[str, np.ndarray], np.ndarray | None], np.ndarray]


class MissingKey(KeyError):
    """A source state key the edge needs is absent although the source kind is registered."""


def _need(state: dict[str, np.ndarray], *keys: str) -> np.ndarray:
    for k in keys:
        if k in state:
            return np.asarray(state[k], dtype=float)
    raise MissingKey(keys[0] if len(keys) == 1 else "|".join(keys))


def _hyd_deficit(state: dict[str, np.ndarray], dst_ctx: Any) -> np.ndarray:
    """Pump/system damage 0..1 = eta_loss + leak + precharge_loss (same sum as ``HydraulicsModel._damage``)."""
    d = _need(state, "eta_loss") + _need(state, "leak") + _need(state, "precharge_loss")
    return np.clip(d, 0.0, 1.0)


def _bus_deficit(ref_v: float, nominal: float) -> Signal:
    """Voltage shortfall below the fresh loaded bus level (``ref_v``), as a fraction of nominal; reads ``bus_voltage_v``."""
    def f(state: dict[str, np.ndarray], dst_ctx: Any) -> np.ndarray:
        return np.maximum(ref_v - _need(state, "bus_voltage_v"), 0.0) / nominal
    return f


def _engine_health_loss(state: dict[str, np.ndarray], dst_ctx: Any) -> np.ndarray:
    """1 - engine health index (``health_proxy`` if the engine model publishes one, else ``hi``)."""
    return np.clip(1.0 - _need(state, "health_proxy", "hi"), 0.0, 1.0)


def _apu_ground(state: dict[str, np.ndarray], dst_ctx: Any) -> np.ndarray:
    """Ground APU hours this cycle, from the destination unit's operating context (hours)."""
    if dst_ctx is None:
        raise MissingKey("ctx.apu_hours")
    return np.asarray(dst_ctx.apu_hours, dtype=float)


def _brake_heat(t0: float, span: float) -> Signal:
    """Excess of the (noise-free) brake peak temperature ``temp_c`` over ``t0``, per ``span`` degrees."""
    def f(state: dict[str, np.ndarray], dst_ctx: Any) -> np.ndarray:
        return np.clip((_need(state, "temp_c") - t0) / span, 0.0, None)
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
        Edge("electrical>avionics", "electrical", "avionics", _bus_deficit(27.5, 28.0), gain=4.0, clamp=0.6),
        Edge("engine>ecs", "engine", "ecs", _engine_health_loss, gain=0.6, clamp=0.4),
        Edge("apu>ecs", "apu", "ecs", _apu_ground, gain=0.15, clamp=0.3, needs_ctx=True),
        Edge("brakes>tires", "brakes", "tires", _brake_heat(300.0, 400.0), gain=0.5, clamp=0.5),
    ]


@dataclass
class CouplingGraph:
    edges: list[Edge] = field(default_factory=default_edges)
    max_total: float = 2.5
    strict: bool = False
    _warned: set[Any] = field(default_factory=set, repr=False)
    _stats: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)

    def set_enabled(self, name: str, on: bool) -> None:
        for e in self.edges:
            if e.name == name:
                e.enabled = on
                return
        raise KeyError(name)

    def _skip(self, e: Edge, why: str, level: int = logging.INFO) -> None:
        st = self._stats.setdefault(e.name, {"applied_count": 0, "skipped_count": 0, "skipped_reason": None})
        st["skipped_count"] += 1
        st["skipped_reason"] = why
        if (e.name, why) not in self._warned:
            self._warned.add((e.name, why))
            log.log(level, "coupling: edge %s skipped: %s", e.name, why)

    def report(self) -> dict[str, dict[str, Any]]:
        """Per-edge ``{applied_count, skipped_count, skipped_reason}`` (reason of the most recent skip, or None)."""
        out = {}
        for e in self.edges:
            st = dict(self._stats.get(e.name, {"applied_count": 0, "skipped_count": 0, "skipped_reason": None}))
            st["enabled"] = e.enabled
            out[e.name] = st
        return out

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
        for dst in {e.dst for e in self.edges if e.dst in fleet.state}:
            fleet.state[dst]["stress_ext"] = np.ones(fleet.counts[dst])  # no stale coupling when an edge is disabled/skipped
        for e in self.edges:
            st = self._stats.setdefault(e.name, {"applied_count": 0, "skipped_count": 0, "skipped_reason": None})
            if not e.enabled:
                st["skipped_reason"] = "disabled"
                continue
            if e.src not in fleet.state or e.dst not in fleet.state:
                missing = e.src if e.src not in fleet.state else e.dst
                self._skip(e, f"kind not registered: {missing}")
                continue
            dst_ctx = None
            if e.needs_ctx:
                if ctx is None:
                    self._skip(e, "no operating context supplied")
                    continue
                dst_ctx = fleet._ctx_for(e.dst, ctx)
            try:
                sig = e.signal(fleet.state[e.src], dst_ctx)
            except MissingKey as exc:
                self._skip(e, f"source key missing: {exc.args[0]} (kind {e.src})", logging.WARNING)
                if self.strict:
                    raise
                continue
            m = self._map(fleet, e.src, e.dst)
            if m is None:
                self._skip(e, "units not alignable")
                if self.strict:
                    raise ValueError(f"coupling edge {e.name}: units not alignable")
                continue
            c = np.clip(e.gain * (sig if sig.shape[0] == fleet.counts[e.dst] and e.needs_ctx else sig[m]), 0.0, e.clamp)
            total[e.dst] = total.get(e.dst, np.zeros(fleet.counts[e.dst])) + c
            st["applied_count"] += 1
            st["skipped_reason"] = None
        out = {}
        for dst, c in total.items():
            out[dst] = np.clip(1.0 + c, 1.0, self.max_total)
            fleet.state[dst]["stress_ext"] = out[dst]
        return out
