"""Physics-flavoured degradation models for the simulator (pure functions, deterministic given rng).

``step(kind, state, profile, rng, ctx)`` advances one flight cycle and returns
``(new_state, telemetry_values)``. Telemetry keys are exactly ``TELEMETRY_KEYS[kind]``;
engine values are corrected sensor values (no OAT/altitude), signs follow FD001
(``ml.health._RULE_SIGN``). SYNTHETIC DEMO MODELS, not real limits.
"""
from __future__ import annotations

import copy
import math
import random
from dataclasses import dataclass
from typing import Protocol

from app.ml.sensors import GENERIC, SENSOR_NAMES

BASE = dict(zip(SENSOR_NAMES, [642, 1580, 1400, 554, 2388, 9050, 47, 522, 2388, 8138, 8.4, 392, 39, 23]))
# End-of-life mean shift per sensor (FD001 direction, magnitude of typical end of life).
EOL_SHIFT = {"T24": 2.5, "T30": 20.0, "T50": 25.0, "P30": -2.0, "Nf": 0.5, "Nc": 60.0, "Ps30": 1.2,
             "phi": -3.0, "NRf": 0.5, "NRc": 40.0, "BPR": 0.2, "htBleed": 6.0, "W31": -1.5, "W32": -0.9}
NOISE = {k: max(abs(v) * 2e-4, 1e-3) for k, v in EOL_SHIFT.items()}
HPT_T50_SHIFT = 150.0
DEFAULT_LIFE = {"engine": 1500.0, **{k: g.life_cycles for k, g in GENERIC.items()}}
REF_ENERGY_MJ = 30.0
ARRHENIUS_K = 5800.0   # Ea/k_B for ~0.5 eV, in kelvin
KINDS = tuple(DEFAULT_LIFE)


class ProfileLike(Protocol):
    oat_dep_c: float
    oat_arr_c: float
    density_alt_arr_ft: float
    payload_frac: float
    takeoff_derate: float
    brake_energy_mj: float
    sink_rate_fps: float
    wet_runway: bool
    landing_weight_frac: float


@dataclass(frozen=True)
class AircraftCtx:
    hyd_pressure_frac: float = 1.0
    bus_voltage_frac: float = 1.0
    engine_wear: float = 0.0
    hpt_level: float = 0.0


def new_state(kind: str, seed: int, component_id: str, start_fraction: float = 0.0) -> dict:
    """Fresh state; ``start_fraction`` in [0,1) is the fraction of life already consumed."""
    if kind not in DEFAULT_LIFE:
        raise ValueError(f"unknown kind {kind!r}")
    life = DEFAULT_LIFE[kind]
    f = min(max(float(start_fraction), 0.0), 0.99)
    return {"kind": kind, "seed": seed, "component_id": component_id, "start_fraction": f,
            "life": life, "cycle": 0, "age": f * life, "hazard": 0.0, "leak": 0.0,
            "wear": 0.0, "modes": []}


def restore(state: dict) -> dict:
    """As-new state for the same component (modes cleared, age zero)."""
    return new_state(state["kind"], state["seed"], state["component_id"], 0.0)


def add_fault(state: dict, mode: str, cycle: int, severity: float = 1.0, ramp_cycles: int = 5) -> dict:
    """Append a fault mode; severity ramps linearly from 0 at onset to ``severity`` after ramp_cycles."""
    state["modes"].append({"mode": mode, "onset_cycle": int(cycle), "severity": float(severity),
                           "ramp_cycles": max(1, int(ramp_cycles))})
    return state


def mode_level(state: dict, mode: str, cycle: int | None = None) -> float:
    """Current ramped severity of ``mode`` (sum over matching entries)."""
    c = state["cycle"] if cycle is None else cycle
    tot = 0.0
    for m in state["modes"]:
        if m["mode"] == mode and c >= m["onset_cycle"]:
            tot += m["severity"] * min(1.0, (c - m["onset_cycle"]) / m["ramp_cycles"])
    return tot


def _arrhenius(temp_c: float, ref_c: float = 55.0) -> float:
    return math.exp(ARRHENIUS_K * (1.0 / (ref_c + 273.15) - 1.0 / (temp_c + 273.15)))


def _wear_curve(age: float, life: float) -> float:
    """Linear + late exponential: a*t + b*exp((t-t0)/tau); equals 1 at t = life."""
    a, b, tau = 0.7, 0.3, 0.12 * life
    return a * age / life + b * math.exp((age - life) / tau)


def _accel_all(state: dict) -> float:
    return 1.0 + 4.0 * mode_level(state, "accelerated_wear")


def _engine(s: dict, p: ProfileLike, rng: random.Random, ctx: AircraftCtx):
    hot = 1.0 + 0.02 * max(0.0, p.oat_dep_c - 15.0)
    thrust = 1.0 + 0.8 * max(0.0, 1.0 - p.takeoff_derate)
    s["age"] += hot * thrust * _accel_all(s)
    w = _wear_curve(s["age"], s["life"])
    s["wear"] = w
    hpt = mode_level(s, "hpt_damage")
    vals = {}
    for k in SENSOR_NAMES:
        v = BASE[k] + EOL_SHIFT[k] * w + rng.gauss(0.0, NOISE[k])
        if k == "T50":
            v += HPT_T50_SHIFT * hpt
        elif k == "T30":
            v += 0.4 * HPT_T50_SHIFT * hpt
        elif k == "phi":
            v -= 1.0 * hpt
        vals[k] = float(v)
    return vals


def _brakes(s: dict, p: ProfileLike, rng: random.Random, ctx: AircraftCtx):
    spec = GENERIC["brakes"]
    per_ref = spec.limit / spec.life_cycles / REF_ENERGY_MJ   # mm per MJ
    stress = 1.0 + 0.5 * max(0.0, 1.0 - ctx.hyd_pressure_frac) * 4
    wet = 1.3 if p.wet_runway else 1.0
    s["wear"] += per_ref * p.brake_energy_mj * wet * stress * _accel_all(s)
    s["age"] += 1.0
    start = s["start_fraction"] * spec.limit
    wear_mm = start + s["wear"]
    disc = 120.0 + 4.5 * p.brake_energy_mj + 0.5 * max(0.0, p.oat_arr_c) + rng.gauss(0.0, 8.0)
    return {"pad_wear_mm": round(wear_mm + rng.gauss(0, 0.01), 3), "disc_temp_c": round(disc, 1)}


def _gear(s: dict, p: ProfileLike, rng: random.Random, ctx: AircraftCtx):
    spec = GENERIC["landing_gear"]
    stress = 1.0 + 1.5 * max(0.0, 1.0 - ctx.hyd_pressure_frac)
    shock = (1.0 + 0.08 * p.sink_rate_fps * (0.6 + 0.4 * p.landing_weight_frac)) * stress
    shock = max(1.05, shock + rng.gauss(0.0, 0.03))
    span = spec.healthy - spec.limit
    base_rate = span / spec.life_cycles
    s["leak"] += base_rate * (1.0 + 0.5 * max(0.0, shock - 1.3)) * (1.0 + 3.0 * mode_level(s, "strut_leak")) * _accel_all(s)
    s["age"] += 1.0
    pressure = spec.healthy - s["start_fraction"] * span - s["leak"]
    return {"strut_pressure_kpa": round(pressure + rng.gauss(0, 3.0), 1), "shock_load_g": round(shock, 3)}


def _hydraulics(s: dict, p: ProfileLike, rng: random.Random, ctx: AircraftCtx):
    spec = GENERIC["hydraulics"]
    span = spec.healthy - spec.limit
    fluid = 48.0 + 0.5 * max(0.0, p.oat_dep_c - 15.0) + 8.0 * s["wear"] + rng.gauss(0.0, 1.5)
    accel = _arrhenius(fluid) * (1.0 + 3.0 * mode_level(s, "pump_leak")) * _accel_all(s)
    s["age"] += 1.0
    s["wear"] = min(1.5, s["wear"] + accel / s["life"])
    frac = min(1.5, s["start_fraction"] + s["wear"])
    return {"pump_pressure_kpa": round(spec.healthy - span * frac + rng.gauss(0, 15.0), 1),
            "fluid_temp_c": round(fluid, 1)}


def _avionics(s: dict, p: ProfileLike, rng: random.Random, ctx: AircraftCtx):
    spec = GENERIC["avionics"]
    shape, lam = 2.2, s["life"]
    box = 32.0 + 0.35 * max(0.0, p.oat_dep_c) + 6.0 * mode_level(s, "overheat") + rng.gauss(0.0, 1.0)
    accel = _arrhenius(box, 40.0) * (1.0 + 2.0 * max(0.0, 1.0 - ctx.bus_voltage_frac) * 5) * _accel_all(s)
    a0 = s["age"]
    s["age"] = a0 + 1.0
    s["hazard"] += ((s["age"] / lam) ** shape - (a0 / lam) ** shape) * accel
    rate = spec.healthy + (spec.limit - spec.healthy) * min(1.5, s["hazard"])
    return {"error_rate_per_khr": round(max(0.0, rate + rng.gauss(0.0, 0.03)), 3), "box_temp_c": round(box, 1)}


def _apu(s: dict, p: ProfileLike, rng: random.Random, ctx: AircraftCtx):
    spec = GENERIC["apu"]
    # Taxi time approximates APU runtime; age counts equivalent 18-minute runs.
    phases = getattr(p, "phases", None)
    run_hours = sum(minutes for name, minutes in phases if name in ("taxi_out", "taxi_in")) / 60.0 if phases is not None else 0.3
    hot = 1.0 + 0.02 * max(0.0, p.oat_dep_c - 15.0)
    s["age"] += max(0.0, run_hours) / 0.3 * hot * _accel_all(s)
    s["wear"] = max(s["wear"], s["age"] / s["life"])
    margin = spec.healthy - (spec.healthy - spec.limit) * s["wear"]
    rpm = 100.0 - 5.0 * s["wear"] - 25.0 * mode_level(s, "starter_fault")
    return {"apu_egt_margin_c": round(margin + rng.gauss(0.0, 0.1), 3),
            "apu_rpm_pct": round(max(0.0, rpm + rng.gauss(0.0, 0.05)), 3)}


def _tires(s: dict, p: ProfileLike, rng: random.Random, ctx: AircraftCtx):
    spec = GENERIC["tires"]
    stress = max(0.0, p.brake_energy_mj) / REF_ENERGY_MJ * (1.0 + 0.08 * max(0.0, p.sink_rate_fps))
    s["age"] += stress * _accel_all(s)
    s["wear"] = max(s["wear"], s["age"] / s["life"])
    tread = spec.healthy - (spec.healthy - spec.limit) * s["wear"]
    pressure = 200.0 - 5.0 * s["wear"] - 60.0 * mode_level(s, "pressure_leak")
    return {"tread_depth_mm": round(max(0.0, tread + rng.gauss(0.0, 0.005)), 3),
            "tire_pressure_psi": round(max(0.0, pressure + rng.gauss(0.0, 0.3)), 3)}


def _ecs(s: dict, p: ProfileLike, rng: random.Random, ctx: AircraftCtx):
    spec = GENERIC["ecs"]
    bleed = 1.0 + 0.5 * max(0.0, ctx.engine_wear) + max(0.0, ctx.hpt_level)
    hot = 1.0 + 0.025 * max(0.0, p.oat_dep_c - 15.0)
    s["age"] += bleed * hot * _accel_all(s)
    s["wear"] = max(s["wear"], s["age"] / s["life"])
    fault = mode_level(s, "pack_valve_fault")
    temp = spec.healthy + (spec.limit - spec.healthy) * (s["wear"] + 0.5 * fault)
    flow = 1.4 * (1.0 - 0.2 * s["wear"] - 0.5 * fault)
    return {"pack_outlet_temp_c": round(temp + rng.gauss(0.0, 0.05), 3),
            "pack_flow_kgs": round(max(0.0, flow + rng.gauss(0.0, 0.005)), 3)}


def _electrical(s: dict, p: ProfileLike, rng: random.Random, ctx: AircraftCtx):
    spec = GENERIC["electrical"]
    battery_temp = 35.0 + 0.5 * max(0.0, p.oat_dep_c - 15.0)
    s["age"] += _arrhenius(battery_temp, 35.0) * _accel_all(s)
    s["wear"] = max(s["wear"], s["age"] / s["life"])
    fault = mode_level(s, "battery_fault")
    capacity = max(0.0, spec.healthy - (spec.healthy - spec.limit) * s["wear"] - 20.0 * fault)
    voltage = 28.0 * ctx.bus_voltage_frac * (0.9 + 0.1 * capacity / spec.healthy) - 4.0 * fault
    return {"battery_capacity_pct": round(min(100.0, max(0.0, capacity + rng.gauss(0.0, 0.02))), 3),
            "bus_voltage_v": round(max(0.0, voltage + rng.gauss(0.0, 0.03)), 3)}


_MODELS = {"engine": _engine, "brakes": _brakes, "landing_gear": _gear,
           "hydraulics": _hydraulics, "avionics": _avionics,
           "apu": _apu, "tires": _tires, "ecs": _ecs, "electrical": _electrical}


def step(kind: str, state: dict, p: ProfileLike, rng: random.Random,
         ctx: AircraftCtx | None = None) -> tuple[dict, dict]:
    """Advance one cycle; returns (new_state, telemetry values). Input state is not mutated."""
    if kind not in _MODELS:
        raise ValueError(f"unknown kind {kind!r}")
    s = copy.deepcopy(state)
    s["cycle"] += 1
    return s, _MODELS[kind](s, p, rng, ctx or AircraftCtx())
