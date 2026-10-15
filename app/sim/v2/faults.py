"""Fault library and injector for simulator v2 (component FAULT layer).

Two distinct layers exist and must not be confused:

* **Component faults** (this module): physical degradation modes (blade erosion, seal leaks...) that change the
  hidden true state of a unit. They are sampled from an onset hazard, ramp in severity, and are written into
  ``state['fault_<mode>']`` arrays (severity 0..1) which concrete models may read. Ground truth is logged
  separately (``GroundTruthFault``) and must be hidden from scorers.
* **Sensor faults** (``app.sim.sensor_faults``): dropout/stuck/drift/spike/noise applied to the *measured*
  values after emission; the true component state is untouched. ``sensor_fault_layer`` re-exports it.

Determinism: draws come from ``StreamRng(seed, "fault.<kind>.<mode>").at(cycle)``; the only mutable state is the
onset array (+ log), so snapshot/restore is bit-identical regardless of how steps were batched.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from app.sim import sensor_faults as sensor_fault_layer
from app.sim.v2.core import StreamRng

PROVENANCE = "SIMULATED"
# Explicit "no fault" marker for ``FaultInjector.onset``. A real onset may be negative: ``World.inject_fault`` back-dates the
# onset by the cycles a fault needs to reach the requested severity, which before that many cycles have elapsed lies before
# cycle 0. (A plain -1 sentinel made such early injections silently inert.)
NO_ONSET = int(np.iinfo(np.int64).min)


@dataclass(frozen=True)
class FaultMode:
    id: str
    kind: str
    ata_chapter: str
    base_hazard: float              # onset probability rate per cycle at age 0
    age_ref_cycles: float = 0.0     # hazard x (1 + age/age_ref); 0 disables age dependence
    driver: str | None = None       # OperatingContext field scaling the hazard
    driver_gain: float = 0.0        # hazard x (1 + gain * driver)
    ramp_cycles: int = 100          # cycles from onset to full severity
    shape: str = "linear"           # linear | exponential
    detect_channels: tuple[str, ...] = ()
    effect_key: str = ""            # latent/state key the model applies the effect to (engine modes: engine model's own keys)
    effect_mult: float = 1.0        # target multiplier at full severity
    effect_shift: float = 0.0       # additive shift at full severity

    def hazard(self, age: np.ndarray | float, driver: np.ndarray | float = 0.0) -> np.ndarray:
        h = self.base_hazard * np.ones_like(np.asarray(age, dtype=float))
        if self.age_ref_cycles > 0:
            h = h * (1.0 + np.asarray(age, dtype=float) / self.age_ref_cycles)
        return h * (1.0 + self.driver_gain * np.asarray(driver, dtype=float))

    def severity(self, since_onset: np.ndarray | float) -> np.ndarray:
        x = np.clip(np.asarray(since_onset, dtype=float) / max(self.ramp_cycles, 1), 0.0, 1.0)
        if self.shape == "exponential":
            return np.expm1(4.0 * x) / math.expm1(4.0)
        if self.shape == "linear":
            return x
        raise ValueError(f"unknown ramp shape: {self.shape}")

    def effect_multiplier(self, sev: np.ndarray | float) -> np.ndarray:
        return 1.0 + (self.effect_mult - 1.0) * np.asarray(sev, dtype=float)

    def effect_additive(self, sev: np.ndarray | float) -> np.ndarray:
        return self.effect_shift * np.asarray(sev, dtype=float)


@dataclass
class GroundTruthFault:
    """Hidden truth record: never expose to scorers/UI."""

    kind: str
    unit: int
    mode: str
    onset_cycle: int
    ramp_cycles: int
    shape: str
    cleared_cycle: int | None = None
    severity_trajectory: list[list[float]] = field(default_factory=list)
    provenance: str = PROVENANCE

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _m(id: str, kind: str, ata: str, h: float, **kw: Any) -> FaultMode:
    return FaultMode(id, kind, ata, h, **kw)


LIBRARY: dict[str, FaultMode] = {m.id: m for m in (
    _m("hpt_blade_erosion", "engine", "72", 2e-5, age_ref_cycles=6000, driver="dust_index", driver_gain=2.0, ramp_cycles=1500,
       detect_channels=("egt_margin_c", "T50"), effect_key="egt_margin_c", effect_mult=0.6),
    _m("compressor_fouling", "engine", "72", 4e-5, driver="humidity_pct", driver_gain=0.01, ramp_cycles=800, shape="exponential",
       detect_channels=("hpc_eff_pct", "T30"), effect_key="hpc_eff_pct", effect_mult=0.93),
    _m("stuck_brake", "brakes", "32", 1.5e-4, age_ref_cycles=3000, ramp_cycles=15, shape="exponential",
       detect_channels=("disc_temp_c", "pad_wear_mm"), effect_key="temp_c", effect_shift=250.0),
    _m("rejected_takeoff_overheat", "brakes", "32", 5e-5, driver="brake_energy_mj", driver_gain=0.05, ramp_cycles=5,
       detect_channels=("disc_temp_c",), effect_key="temp_c", effect_shift=400.0),
    _m("slow_leak", "tires", "32", 6e-4, age_ref_cycles=400, ramp_cycles=60,
       detect_channels=("tire_pressure_psi",), effect_key="p_cold", effect_mult=0.8),
    _m("fod_cut", "tires", "32", 2e-4, driver="dust_index", driver_gain=1.0, ramp_cycles=10, shape="exponential",
       detect_channels=("tire_pressure_psi", "tread_depth_mm"), effect_key="tread_loss_mm", effect_mult=2.0),
    _m("strut_seal_leak", "landing_gear", "32", 3e-4, age_ref_cycles=8000, ramp_cycles=400,
       detect_channels=("strut_pressure_kpa", "strut_extension_mm"), effect_key="leak_kpa", effect_mult=0.75),
    # Overload landing (beyond a routine hard landing): the strut is condemned at the next inspection (effect 0.8 of life).
    # base_hazard 1e-4 -> 4e-5 (assumed, no citation): ~4 events per 24 tails per year, with effect_shift 0.2 -> 0.8 so a
    # finding leads to a removal. Routine hard landings stay inspection + on-wing servicing (see landing_gear.py).
    _m("hard_landing_damage", "landing_gear", "32", 4e-5, driver="sink_rate_fps", driver_gain=0.3, ramp_cycles=3,
       detect_channels=("shock_load_g", "strut_extension_mm"), effect_key="fatigue", effect_shift=0.8),
    _m("pump_cavitation", "hydraulics", "29", 3e-4, age_ref_cycles=5000, ramp_cycles=250, shape="exponential",
       detect_channels=("pump_pressure_kpa", "fluid_temp_c"), effect_key="eta_loss", effect_mult=0.35),
    _m("line_leak", "hydraulics", "29", 4e-4, age_ref_cycles=7000, ramp_cycles=300,
       detect_channels=("fluid_qty_pct", "case_drain_flow_lpm"), effect_key="leak", effect_mult=0.6),
    _m("cell_short", "electrical", "24", 1.5e-4, age_ref_cycles=4000, ramp_cycles=120, shape="exponential",
       detect_channels=("battery_capacity_pct", "bus_voltage_v"), effect_key="fade_pct", effect_mult=0.5),
    _m("idg_bearing", "electrical", "24", 3e-4, age_ref_cycles=9000, ramp_cycles=600,
       detect_channels=("idg_oil_temp_c", "bus_frequency_hz"), effect_key="idg_oil_temp_c", effect_shift=35.0),
    _m("intermittent_connector", "avionics", "34", 6e-4, driver="humidity_pct", driver_gain=0.01, ramp_cycles=200,
       detect_channels=("bite_fault_count", "error_rate_per_khr"), effect_key="bite_rate", effect_mult=5.0),
    _m("overheating", "avionics", "34", 3e-4, age_ref_cycles=10000, driver="oat_c", driver_gain=0.01, ramp_cycles=150,
       detect_channels=("box_temp_c",), effect_key="box_temperature", effect_shift=20.0),
    _m("starter_failure", "apu", "49", 2.5e-4, driver="apu_starts", driver_gain=0.2, ramp_cycles=80, shape="exponential",
       detect_channels=("apu_start_time_s",), effect_key="starter_wear", effect_mult=1.8),
    _m("egt_overtemp", "apu", "49", 3e-4, age_ref_cycles=6000, ramp_cycles=200,
       detect_channels=("apu_peak_start_egt_c", "apu_egt_margin_c"), effect_key="margin_c", effect_shift=60.0),
    _m("valve_stuck", "ecs", "21", 3e-4, age_ref_cycles=8000, ramp_cycles=20, shape="exponential",
       detect_channels=("pack_flow_kgs", "ram_door_pos_pct"), effect_key="valve_degradation", effect_mult=0.5),
    _m("hx_fouling", "ecs", "21", 5e-4, driver="dust_index", driver_gain=1.5, ramp_cycles=700,
       detect_channels=("pack_outlet_temp_c", "pack_flow_kgs"), effect_key="fouling", effect_mult=0.7),
)}


def modes_for(kind: str) -> list[FaultMode]:
    return [m for m in LIBRARY.values() if m.kind == kind]


# ---- helpers concrete models call to consume ``state['fault_<mode>']`` (all return neutral values when absent) ----
RATE_EXPONENT = 3.0  # assumed (no citation): an effect_mult m<1 on a *measured* quantity boosts degradation rate by (1/m)**3


def fault_level(state: dict[str, np.ndarray], mode_id: str, idx: Any = None) -> np.ndarray | float:
    """Severity 0..1 of ``mode_id`` (0.0 when no injector has written the key)."""
    sev = state.get(f"fault_{mode_id}")
    if sev is None:
        return 0.0
    return sev if idx is None else sev[idx]


def fault_mult(state: dict[str, np.ndarray], mode_id: str, idx: Any = None) -> np.ndarray | float:
    """``LIBRARY[mode].effect_multiplier(severity)``; 1.0 when absent."""
    sev = fault_level(state, mode_id, idx)
    return 1.0 if isinstance(sev, float) else LIBRARY[mode_id].effect_multiplier(sev)


def fault_add(state: dict[str, np.ndarray], mode_id: str, idx: Any = None) -> np.ndarray | float:
    """``LIBRARY[mode].effect_additive(severity)``; 0.0 when absent."""
    sev = fault_level(state, mode_id, idx)
    return 0.0 if isinstance(sev, float) else LIBRARY[mode_id].effect_additive(sev)


def fault_rate(state: dict[str, np.ndarray], mode_id: str, idx: Any = None) -> np.ndarray | float:
    """Degradation-rate multiplier: ``m`` if effect_mult>=1 else ``(1/m)**RATE_EXPONENT``; 1.0 when absent."""
    m = fault_mult(state, mode_id, idx)
    if isinstance(m, float):
        return 1.0
    return m if LIBRARY[mode_id].effect_mult >= 1.0 else np.power(1.0 / np.maximum(m, 1e-6), RATE_EXPONENT)


def fault_log_loss(state: dict[str, np.ndarray], mode_id: str, idx: Any = None) -> np.ndarray | float:
    """``-ln(effect_mult) * severity``: exponential decay rate implied by a multiplier<1 at full severity (0.0 when absent)."""
    sev = fault_level(state, mode_id, idx)
    return 0.0 if isinstance(sev, float) else -math.log(LIBRARY[mode_id].effect_mult) * np.asarray(sev, dtype=float)


class FaultInjector:
    """Samples onsets per (kind, mode, unit) and exposes severity through ``state['fault_<mode>']``."""

    def __init__(self, seed: int, counts: dict[str, int], library: dict[str, FaultMode] | None = None,
                 enabled: dict[str, bool] | None = None) -> None:
        self.seed = int(seed)
        self.counts = dict(counts)
        self.library = dict(LIBRARY if library is None else library)
        self.enabled = dict(enabled or {})
        self._modes = {k: [m for m in self.library.values() if m.kind == k] for k in counts}
        self._rng = {(m.kind, m.id): StreamRng(self.seed, f"fault.{m.kind}.{m.id}") for ms in self._modes.values() for m in ms}
        self.onset = {(m.kind, m.id): np.full(counts[k], NO_ONSET, dtype=np.int64) for k, ms in self._modes.items() for m in ms}
        self._cleared: dict[tuple[str, str, int], int] = {}
        self._log: list[dict[str, Any]] = []
        self._open: dict[tuple[str, str, int], dict[str, Any]] = {}

    def _on(self, mode_id: str) -> bool:
        return self.enabled.get(mode_id, True)

    def active(self, kind: str, mode_id: str, unit: Any = None) -> Any:
        """True where a fault of ``mode_id`` is present (onset set, possibly back-dated before cycle 0)."""
        a = self.onset[(kind, mode_id)] != NO_ONSET
        return a if unit is None else a[unit]

    def severity(self, kind: str, mode: FaultMode, cycle: int) -> np.ndarray:
        on = self.onset[(kind, mode.id)]
        act = on != NO_ONSET
        since = np.where(act, cycle - np.where(act, on, cycle), 0)
        return np.where(act, mode.severity(np.maximum(since, 0)), 0.0)

    def step(self, kind: str, state: dict[str, np.ndarray], cycle: int, ctx: Any = None, age: np.ndarray | None = None) -> None:
        """Sample onsets for this cycle and write severity arrays into ``state``. Call once per cycle per kind, before the model step."""
        n = self.counts[kind]
        if age is None:
            age = state.get("age_cycles", np.zeros(n))
        for mode in self._modes.get(kind, []):
            key = (kind, mode.id)
            if self._on(mode.id):
                u = self._rng[key].at(cycle).random(n)
                drv = getattr(ctx, mode.driver, None) if (ctx is not None and mode.driver) else None
                h = mode.hazard(age, 0.0 if drv is None else drv)
                p = -np.expm1(-h)
                new = (u < p) & (self.onset[key] == NO_ONSET)
                if new.any():
                    self.onset[key][new] = cycle
                    for i in np.flatnonzero(new):
                        rec = GroundTruthFault(kind, int(i), mode.id, int(cycle), mode.ramp_cycles, mode.shape).as_dict()
                        self._log.append(rec)
                        self._open[(kind, mode.id, int(i))] = rec
            sev = self.severity(kind, mode, cycle)
            state[f"fault_{mode.id}"] = sev
            for i in np.flatnonzero(self.onset[key] != NO_ONSET):
                rec = self._open.get((kind, mode.id, int(i)))
                if rec is not None:
                    rec["severity_trajectory"].append([int(cycle), float(sev[i])])

    def clear(self, kind: str, idx: Any, cycle: int, state: dict[str, np.ndarray] | None = None, modes: list[str] | None = None) -> None:
        """Maintenance removed the fault on these units (repair/overhaul/replace)."""
        idx = np.atleast_1d(np.asarray(idx, dtype=np.int64))
        for mode in self._modes.get(kind, []):
            if modes is not None and mode.id not in modes:
                continue
            on = self.onset[(kind, mode.id)]
            for i in idx:
                if on[i] != NO_ONSET:
                    on[i] = NO_ONSET
                    rec = self._open.pop((kind, mode.id, int(i)), None)
                    if rec is not None:
                        rec["cleared_cycle"] = int(cycle)
            if state is not None and f"fault_{mode.id}" in state:
                state[f"fault_{mode.id}"][idx] = 0.0

    def ground_truth(self) -> list[dict[str, Any]]:
        """Complete hidden-truth log (deep copy). Not for scorers' inputs."""
        return json.loads(json.dumps(self._log))

    def snapshot(self) -> dict[str, Any]:
        return {"onset": {f"{k}/{m}": a.copy() for (k, m), a in self.onset.items()}, "log": json.loads(json.dumps(self._log))}

    def restore(self, snap: dict[str, Any]) -> None:
        for (k, m) in self.onset:
            self.onset[(k, m)] = np.array(snap["onset"][f"{k}/{m}"], dtype=np.int64, copy=True)
        self._log = json.loads(json.dumps(snap["log"]))
        self._open = {}
        for rec in self._log:
            if rec["cleared_cycle"] is None and self.onset[(rec["kind"], rec["mode"])][rec["unit"]] == rec["onset_cycle"]:
                self._open[(rec["kind"], rec["mode"], rec["unit"])] = rec

    def state_hash(self) -> str:
        h = hashlib.sha256()
        for key in sorted(self.onset):
            h.update(repr(key).encode())
            h.update(self.onset[key].tobytes())
        h.update(json.dumps(self._log, sort_keys=True).encode())
        return h.hexdigest()


__all__ = ["FaultInjector", "FaultMode", "GroundTruthFault", "LIBRARY", "NO_ONSET", "fault_add", "fault_level", "fault_log_loss", "fault_mult",
           "fault_rate", "modes_for", "sensor_fault_layer"]
