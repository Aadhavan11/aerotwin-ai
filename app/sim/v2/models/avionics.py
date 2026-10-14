"""Thermal-accelerated Weibull avionics life and intermittent BITE faults.

NFF removals call maintain(..., "inspect"): the shop finds nothing and reinstalls
the unit with bit-identical state. Confirmed removals call repair or replace.
Hard limits use noise-free signals; measurement noise cannot create failures.
"""

from __future__ import annotations

import numpy as np

from app.sim.v2.core import ACTIONS, State, register
from app.sim.v2.drivers import OperatingContext
from app.sim.v2.faults import fault_add, fault_mult

PARAMS = {
    "beta": 1.1,  # literature prior, unverified
    "eta_cycles": 3000.0,  # literature prior, unverified
    "eta_sigma": 0.15,  # literature prior, unverified
    "ea_over_k": 0.5 / 8.617333262e-5,  # literature prior, unverified: eV / (eV/K)
    "kelvin_offset": 273.15,  # literature prior, unverified
    "reference_box_c": 40.0,  # literature prior, unverified
    "box_offset_c": 30.0,  # literature prior, unverified
    "box_offset_sigma_c": 1.0,  # literature prior, unverified
    "soak_coefficient": 0.35,  # literature prior, unverified
    "soak_minutes": 30.0,  # literature prior, unverified
    "flight_heat_c_per_h": 0.5,  # literature prior, unverified
    "cruise_cold_offset_c": 25.0,  # literature prior, unverified
    "thermal_range_c": 60.0,  # literature prior, unverified
    "thermal_exponent": 2.0,  # literature prior, unverified
    "thermal_weight": 0.2,  # literature prior, unverified
    "error_healthy": 0.2,  # literature prior, unverified
    "error_limit": 5.0,  # literature prior, unverified
    "error_noise": 0.025,  # literature prior, unverified
    "box_noise_c": 0.25,  # literature prior, unverified
    "bite_rate": 0.015,  # literature prior, unverified
    "bite_damage_gain": 4.0,  # literature prior, unverified
    "fan_nominal_rpm": 4000.0,  # literature prior, unverified
    "fan_temp_gain": 45.0,  # literature prior, unverified
    "fan_age_loss": 0.08,  # literature prior, unverified
    "fan_factor_sigma": 0.025,  # literature prior, unverified
    "fan_noise_rpm": 15.0,  # literature prior, unverified
    "nff_base": 0.35,  # literature prior, unverified
    "nff_fault_scale": 10.0,  # literature prior, unverified
}


def box_temperature(state: State, ctx: OperatingContext) -> np.ndarray:
    """Noise-free ground heat soak plus flight heating, in Celsius."""
    taxi = np.maximum(0.0, ctx.taxi_out_min + ctx.taxi_in_min)
    soak = PARAMS["soak_coefficient"] * np.maximum(0.0, ctx.oat_c) * taxi / PARAMS["soak_minutes"]
    return state["box_offset_c"] + soak + PARAMS["flight_heat_c_per_h"] * np.maximum(0.0, ctx.flight_hours) + fault_add(state, "overheating")


def temperature_acceleration(state: State, ctx: OperatingContext) -> np.ndarray:
    """Arrhenius acceleration relative to a 40 C box."""
    temperature = box_temperature(state, ctx) + PARAMS["kelvin_offset"]
    reference = PARAMS["reference_box_c"] + PARAMS["kelvin_offset"]
    return np.exp(PARAMS["ea_over_k"] * (1.0 / reference - 1.0 / temperature))


def update_health(state: State) -> None:
    """Recompute latent health and latch noise-free hard-limit failures."""
    state["hi"][:] = np.clip(1.0 - state["eff_age"] / state["life_eff"], 0.0, 1.0)
    state["failed"] |= state["hi"] <= 0.0


def update_latent(state: State, ctx: OperatingContext) -> None:
    """Deterministic, vectorised effective-age update; BITE is independent."""
    hot = box_temperature(state, ctx)
    cold = ctx.oat_c - PARAMS["cruise_cold_offset_c"]
    cycling = (np.maximum(0.0, hot - cold) / PARAMS["thermal_range_c"]) ** PARAMS["thermal_exponent"]
    increment = temperature_acceleration(state, ctx) * (1.0 + PARAMS["thermal_weight"] * cycling) * np.maximum(0.0, state["stress_ext"])
    state["eff_age"] += increment
    state["age_virtual"] += increment
    state["age_cycles"] += 1.0
    state["age_hours"] += ctx.flight_hours
    update_health(state)


def nff_prob(state: State, idx: np.ndarray) -> np.ndarray:
    """Shop NFF probability, increasing with intermittent fault history."""
    saturation = -np.expm1(-state["bite_fault_count"][idx] / PARAMS["nff_fault_scale"])
    probability = PARAMS["nff_base"] + (1.0 - PARAMS["nff_base"]) * saturation
    return np.where(state["failed"][idx], 0.0, np.clip(probability, 0.0, 1.0))


def removal_reason(state: State, idx: np.ndarray, rng: np.random.Generator) -> list[str]:
    """Sample shop findings solely from the supplied removal RNG."""
    confirmed = np.where(state["failed"][idx], "confirmed_failure", "confirmed_degradation")
    return np.where(rng.random(idx.size) < nff_prob(state, idx), "NFF", confirmed).tolist()


@register("avionics")
class AvionicsModel:
    kind = "avionics"
    channels: tuple[str, ...] = ("error_rate_per_khr", "box_temp_c", "bite_fault_count", "cooling_fan_rpm")

    def init(self, n: int, rng: np.random.Generator) -> State:
        eta = rng.lognormal(np.log(PARAMS["eta_cycles"]), PARAMS["eta_sigma"], n)
        life = eta * (-np.log(np.maximum(rng.random(n), np.finfo(float).tiny))) ** (1.0 / PARAMS["beta"])
        return {
            "eta_cycles": eta,
            "life_eff": life,
            "box_offset_c": rng.normal(PARAMS["box_offset_c"], PARAMS["box_offset_sigma_c"], n),
            "fan_factor": rng.lognormal(0.0, PARAMS["fan_factor_sigma"], n),
            "eff_age": np.zeros(n),
            "bite_fault_count": np.zeros(n, dtype=np.int64),
            "age_cycles": np.zeros(n),
            "age_hours": np.zeros(n),
            "age_virtual": np.zeros(n),
            "hi": np.ones(n),
            "failed": np.zeros(n, dtype=bool),
            "stress_ext": np.ones(n),
        }

    def step(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> None:
        update_latent(state, ctx)
        lam = PARAMS["bite_rate"] * (1.0 + PARAMS["bite_damage_gain"] * (1.0 - state["hi"])) * temperature_acceleration(state, ctx) * fault_mult(state, "intermittent_connector")
        state["bite_fault_count"] += rng.poisson(lam)

    def emit(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> dict[str, np.ndarray]:
        damage = 1.0 - state["hi"]
        box = box_temperature(state, ctx)
        error = PARAMS["error_healthy"] + (PARAMS["error_limit"] - PARAMS["error_healthy"]) * damage
        fan = (PARAMS["fan_nominal_rpm"] + PARAMS["fan_temp_gain"] * (box - PARAMS["reference_box_c"])) * state["fan_factor"]
        fan *= 1.0 - PARAMS["fan_age_loss"] * damage
        return {
            "error_rate_per_khr": np.maximum(0.0, error + rng.normal(0.0, PARAMS["error_noise"], ctx.n)),
            "box_temp_c": box + rng.normal(0.0, PARAMS["box_noise_c"], ctx.n),
            "bite_fault_count": state["bite_fault_count"].copy(),
            "cooling_fan_rpm": np.maximum(0.0, fan + rng.normal(0.0, PARAMS["fan_noise_rpm"], ctx.n)),
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
        state["eff_age"][idx] *= factor
        state["age_virtual"][idx] *= factor
        if action == "overhaul":
            state["bite_fault_count"][idx] = 0
        state["hi"][idx] = np.clip(1.0 - state["eff_age"][idx] / state["life_eff"][idx], 0.0, 1.0)
        state["failed"][idx] = state["hi"][idx] <= 0.0
