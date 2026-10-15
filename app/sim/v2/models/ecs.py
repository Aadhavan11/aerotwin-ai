"""Air-cycle pack fouling, Weibull bearing life and stuck-valve degradation.

Fouling integrates environmental scaling times increments of 1-exp(-hours/tau).
This is exact for constant environments and monotone when stations change.
Virtual exposure tracks maintenance/coupling separately from cumulative hours.
Health is one minus the largest fouling, bearing or valve damage fraction.
Hard temperature limits use noise-free outlet temperature, never sensor noise.
"""

from __future__ import annotations

import numpy as np

from app.sim.v2.core import ACTIONS, State, register
from app.sim.v2.drivers import OperatingContext
from app.sim.v2.faults import fault_mult, fault_rate

PARAMS = {
    "fouling_inf": 0.8,  # assumed (no citation)
    "fouling_tau_h": 4000.0,  # assumed (no citation)
    "fouling_limit": 1.0,  # assumed (no citation)
    "fouling_sigma": 0.1,  # assumed (no citation)
    "dust_baseline": 0.5,  # assumed (no citation)
    "humidity_reference_pct": 50.0,  # assumed (no citation)
    "humidity_scale_pct": 200.0,  # assumed (no citation)
    "humidity_min_factor": 0.5,  # assumed (no citation)
    "bearing_scale_h": 12000.0,  # assumed (no citation)
    "bearing_beta": 2.0,  # assumed (no citation)
    "bearing_exponent": 2.0,  # assumed (no citation)
    "valve_rate": 0.0002,  # assumed (no citation)
    "valve_damage_step": 0.08,  # assumed (no citation)
    "flow_nominal_kgs": 1.4,  # assumed (no citation)
    "flow_valve_gain": 0.5,  # assumed (no citation)
    "flow_fouling_gain": 0.2,  # assumed (no citation)
    "outlet_healthy_c": 15.0,  # assumed (no citation)
    "outlet_limit_c": 45.0,  # assumed (no citation)
    "outlet_fouling_gain_c": 22.0,  # assumed (no citation)
    "outlet_flow_gain_c": 20.0,  # assumed (no citation)
    "oat_reference_c": 15.0,  # assumed (no citation)
    "outlet_hot_gain": 0.05,  # assumed (no citation)
    "acm_nominal_rpm": 40000.0,  # assumed (no citation)
    "acm_wear_gain": 0.2,  # assumed (no citation)
    "compressor_offset_c": 80.0,  # assumed (no citation)
    "compressor_wear_gain_c": 10.0,  # assumed (no citation)
    "door_nominal_pct": 50.0,  # assumed (no citation)
    "door_max_pct": 100.0,  # assumed (no citation)
    "door_valve_gain_pct": 40.0,  # assumed (no citation)
    "outlet_noise_c": 0.1,  # assumed (no citation)
    "flow_noise_kgs": 0.005,  # assumed (no citation)
    "acm_noise_rpm": 30.0,  # assumed (no citation)
    "compressor_noise_c": 0.2,  # assumed (no citation)
    "door_noise_pct": 0.25,  # assumed (no citation)
}


def valve_effective(state: State) -> np.ndarray:
    """Valve degradation including the injected valve_stuck fault (level effect, reversible when the fault is cleared)."""
    return np.minimum(state["valve_degradation"] + (1.0 - fault_mult(state, "valve_stuck")), 1.0)


def flow_fraction(state: State) -> np.ndarray:
    """Noise-free flow fraction after valve and exchanger losses."""
    return np.maximum(0.0, 1.0 - PARAMS["flow_valve_gain"] * valve_effective(state) - PARAMS["flow_fouling_gain"] * state["fouling"] / PARAMS["fouling_limit"])


def outlet_temperature(state: State) -> np.ndarray:
    """Noise-free outlet temperature including the last operating heat load."""
    return (PARAMS["outlet_healthy_c"] + PARAMS["outlet_fouling_gain_c"] * state["fouling"] / PARAMS["fouling_limit"]
            + PARAMS["outlet_flow_gain_c"] * (1.0 - flow_fraction(state)) + state["thermal_load"])


def update_health(state: State) -> None:
    """Update bearing vibration and worst-fraction health; latch hard limits."""
    state["bearing_wear"][:] = (state["bearing_hours"] / state["bearing_life_h"]) ** PARAMS["bearing_exponent"]
    state["vibration_index"][:] = state["bearing_wear"]
    damage = np.maximum(np.maximum(state["fouling"] / PARAMS["fouling_limit"], state["vibration_index"]), valve_effective(state))
    state["hi"][:] = np.clip(1.0 - damage, 0.0, 1.0)
    state["failed"] |= (state["hi"] <= 0.0) | (outlet_temperature(state) >= PARAMS["outlet_limit_c"])


def update_latent(state: State, ctx: OperatingContext) -> None:
    """Deterministic vectorised fouling/bearing update from cycle exposure."""
    hours = np.maximum(0.0, ctx.flight_hours) * np.maximum(0.0, state["stress_ext"])
    old = state["fouling_hours"] / PARAMS["fouling_tau_h"]
    humidity = np.maximum(PARAMS["humidity_min_factor"], 1.0 + (ctx.humidity_pct - PARAMS["humidity_reference_pct"]) / PARAMS["humidity_scale_pct"])
    environment = (PARAMS["dust_baseline"] + np.maximum(0.0, ctx.dust_index)) * humidity
    state["fouling"] += fault_rate(state, "hx_fouling") * PARAMS["fouling_inf"] * state["fouling_factor"] * environment * np.exp(-old) * (-np.expm1(-hours / PARAMS["fouling_tau_h"]))
    state["fouling_hours"] += hours
    state["bearing_hours"] += hours
    state["thermal_load"][:] = PARAMS["outlet_hot_gain"] * np.maximum(0.0, ctx.oat_c - PARAMS["oat_reference_c"])
    state["age_virtual"] += hours
    state["age_cycles"] += 1.0
    state["age_hours"] += ctx.flight_hours
    update_health(state)


@register("ecs")
class EcsModel:
    kind = "ecs"
    channels: tuple[str, ...] = ("pack_outlet_temp_c", "pack_flow_kgs", "acm_speed_rpm", "compressor_outlet_temp_c", "ram_door_pos_pct")

    def init(self, n: int, rng: np.random.Generator) -> State:
        return {
            "fouling_factor": rng.lognormal(0.0, PARAMS["fouling_sigma"], n),
            "bearing_life_h": PARAMS["bearing_scale_h"] * np.maximum(rng.weibull(PARAMS["bearing_beta"], n), np.finfo(float).tiny),
            "fouling": np.zeros(n),
            "fouling_hours": np.zeros(n),
            "bearing_hours": np.zeros(n),
            "bearing_wear": np.zeros(n),
            "vibration_index": np.zeros(n),
            "valve_degradation": np.zeros(n),
            "valve_count": np.zeros(n, dtype=np.int64),
            "thermal_load": np.zeros(n),
            "age_cycles": np.zeros(n),
            "age_hours": np.zeros(n),
            "age_virtual": np.zeros(n),
            "hi": np.ones(n),
            "failed": np.zeros(n, dtype=bool),
            "stress_ext": np.ones(n),
        }

    def step(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> None:
        update_latent(state, ctx)
        events = rng.poisson(PARAMS["valve_rate"] * np.maximum(0.0, state["stress_ext"]))
        state["valve_count"] += events
        state["valve_degradation"] += events * PARAMS["valve_damage_step"]
        update_health(state)

    def emit(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> dict[str, np.ndarray]:
        outlet = outlet_temperature(state) - state["thermal_load"] + PARAMS["outlet_hot_gain"] * np.maximum(0.0, ctx.oat_c - PARAMS["oat_reference_c"])
        speed = PARAMS["acm_nominal_rpm"] * np.maximum(0.0, 1.0 - PARAMS["acm_wear_gain"] * state["bearing_wear"])
        compressor = outlet + PARAMS["compressor_offset_c"] + PARAMS["compressor_wear_gain_c"] * state["bearing_wear"]
        door = PARAMS["door_nominal_pct"] - PARAMS["door_valve_gain_pct"] * valve_effective(state)
        return {
            "pack_outlet_temp_c": outlet + rng.normal(0.0, PARAMS["outlet_noise_c"], ctx.n),
            "pack_flow_kgs": np.maximum(0.0, PARAMS["flow_nominal_kgs"] * flow_fraction(state) + rng.normal(0.0, PARAMS["flow_noise_kgs"], ctx.n)),
            "acm_speed_rpm": np.maximum(0.0, speed + rng.normal(0.0, PARAMS["acm_noise_rpm"], ctx.n)),
            "compressor_outlet_temp_c": compressor + rng.normal(0.0, PARAMS["compressor_noise_c"], ctx.n),
            "ram_door_pos_pct": np.clip(door + rng.normal(0.0, PARAMS["door_noise_pct"], ctx.n), 0.0, PARAMS["door_max_pct"]),
        }

    def health_index(self, state: State) -> np.ndarray:
        return state["hi"]

    def maintain(self, state: State, idx: np.ndarray, action: str, q: float, rng: np.random.Generator) -> None:
        if action not in ACTIONS:
            raise ValueError(f"unknown action: {action}")
        if action == "inspect" or idx.size == 0:
            return
        if action == "replace":
            fresh = self.init(idx.size, rng)
            for key, value in fresh.items():
                state[key][idx] = value
            return
        factor = float(np.clip(q, 0.0, 1.0)) if action == "repair" else 0.0
        for key in ("fouling", "fouling_hours", "valve_degradation", "age_virtual"):
            state[key][idx] *= factor
        state["bearing_hours"][idx] *= factor ** (1.0 / PARAMS["bearing_exponent"])
        if action == "overhaul":
            state["valve_count"][idx] = 0
            state["thermal_load"][idx] = 0.0
        # Only selected units may change, including cached damage/health arrays.
        selected = {key: value[idx].copy() for key, value in state.items()}
        selected["failed"][:] = False
        update_health(selected)
        for key in ("bearing_wear", "vibration_index", "hi", "failed"):
            state[key][idx] = selected[key]
