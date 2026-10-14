"""Monotone hydraulic degradation priors; calibration against UCI hydraulic data comes later.

Rates target roughly 2000 nominal flights, not validated service-life predictions.
Filter differential pressure is excess above a fresh filter (zero kPa).
"""

from __future__ import annotations

import numpy as np

from app.sim.v2.core import State, register
from app.sim.v2.drivers import OperatingContext

PARAMS: dict[str, float] = {
    "pressure_healthy": 20700.0,  # literature prior, unverified: GENERIC healthy pressure, kPa.
    "pressure_limit": 17200.0,  # literature prior, unverified: GENERIC failure limit, kPa.
    "life_cycles": 2000.0,  # literature prior, unverified: nominal GENERIC life.
    "param_sigma": 0.25,  # literature prior, unverified: lognormal spread, CV about 0.25.
    "eta_drift": 0.00030,  # literature prior, unverified: efficiency loss per flight.
    "eta_sigma": 0.00006,  # literature prior, unverified: Wiener increment standard deviation.
    "leak_shape": 2.0,  # literature prior, unverified: gamma shape per flight.
    "leak_scale": 0.00005,  # literature prior, unverified: gamma leak increment scale.
    "precharge_rate": 0.000025,  # literature prior, unverified: gas loss per flight.
    "acid_rate": 0.00015,  # literature prior, unverified: acid-number proxy per flight.
    "acid_wear": 0.5,  # literature prior, unverified; weakest: proxy-to-pump coupling lacks calibration.
    "acid_dust": 2.0,  # literature prior, unverified: contamination sensitivity to dust.
    "filter_rate": 0.04,  # literature prior, unverified: kPa per dust/acid exposure.
    "leak_weight": 1.0,  # literature prior, unverified: pressure damage per leak proxy.
    "precharge_weight": 1.0,  # literature prior, unverified: pressure damage per gas-loss proxy.
    "activation_k": 4000.0,  # literature prior, unverified: Arrhenius Ea/R, kelvin.
    "kelvin_offset": 273.15,  # literature prior, unverified: Celsius-to-kelvin conversion.
    "temp_ref_c": 55.0,  # literature prior, unverified: fluid reference temperature.
    "oat_ref_c": 15.0,  # literature prior, unverified: nominal ambient temperature.
    "oat_gain": 0.6,  # literature prior, unverified: ambient-to-fluid thermal transfer.
    "heat_eta": 4.0,  # literature prior, unverified: efficiency-loss heating, C/proxy.
    "hours_ref": 2.0,  # literature prior, unverified: nominal flight hours.
    "heat_hours": 2.0,  # literature prior, unverified: fluid heating per extra hour.
    "pressure_factor_ref": 1.53,  # literature prior, unverified: (1 + payload .7) * (1 - derate .1).
    "pressure_noise": 15.0,  # literature prior, unverified: sensor standard deviation, kPa.
    "temp_noise": 1.5,  # literature prior, unverified: fluid sensor standard deviation, C.
    "drain_base": 0.5,  # literature prior, unverified: fresh case drain, L/min.
    "drain_leak": 8.0,  # literature prior, unverified: case-drain gain, L/min/proxy.
    "drain_noise": 0.03,  # literature prior, unverified: drain sensor standard deviation.
    "qty_base": 100.0,  # literature prior, unverified: fresh fluid quantity, percent.
    "qty_precharge": 30.0,  # literature prior, unverified: quantity loss per gas-loss proxy.
    "qty_leak": 10.0,  # literature prior, unverified: quantity loss per leak proxy.
    "qty_noise": 0.1,  # literature prior, unverified: quantity sensor standard deviation.
    "filter_noise": 0.1,  # literature prior, unverified: differential-pressure sensor noise, kPa.
    "failure_median_accel": 650.0,  # literature prior, unverified: flights at OAT 45, dust .9, stress 2.
    "failure_shape_accel": 6.0,  # literature prior, unverified: reference Weibull shape, not validation.
}


@register("hydraulics")
class HydraulicsModel:
    kind = "hydraulics"
    channels = ("pump_pressure_kpa", "fluid_temp_c", "case_drain_flow_lpm", "fluid_qty_pct", "filter_dp_kpa")
    latent_keys = ("eta_loss", "leak", "filter_dp_kpa", "acid_number", "precharge_loss")
    parameter_keys = ("p_drift", "p_leak_scale", "p_acid_rate", "p_precharge_rate", "p_filter_rate")

    def _params(self, n: int, rng: np.random.Generator) -> State:
        priors = ("eta_drift", "leak_scale", "acid_rate", "precharge_rate", "filter_rate")
        return {key: PARAMS[prior] * rng.lognormal(0.0, PARAMS["param_sigma"], n) for key, prior in zip(self.parameter_keys, priors)}

    def init(self, n: int, rng: np.random.Generator) -> State:
        state = self._params(n, rng)
        for key in (*self.latent_keys, "age_cycles", "age_hours", "age_virtual", "n_inspections"):
            state[key] = np.zeros(n)
        state.update(hi=np.ones(n), failed=np.zeros(n, dtype=bool), stress_ext=np.ones(n))
        return state

    def _damage(self, state: State) -> np.ndarray:
        return np.clip(state["eta_loss"] + PARAMS["leak_weight"] * state["leak"] + PARAMS["precharge_weight"] * state["precharge_loss"], 0.0, 1.0)

    def _temperature(self, state: State, ctx: OperatingContext) -> np.ndarray:
        return (PARAMS["temp_ref_c"] + PARAMS["oat_gain"] * (ctx.oat_c - PARAMS["oat_ref_c"])
                + PARAMS["heat_eta"] * state["eta_loss"] + PARAMS["heat_hours"] * (ctx.flight_hours - PARAMS["hours_ref"]))

    def step(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> None:
        temperature_k = np.maximum(self._temperature(state, ctx) + PARAMS["kelvin_offset"], 1.0)
        acceleration = np.exp(PARAMS["activation_k"] * (1.0 / (PARAMS["temp_ref_c"] + PARAMS["kelvin_offset"]) - 1.0 / temperature_k))
        pressure_cycles = (1.0 + np.maximum(ctx.payload_frac, 0.0)) * (1.0 - np.clip(ctx.thrust_derate, 0.0, 1.0)) / PARAMS["pressure_factor_ref"]
        driver = acceleration * pressure_cycles * np.maximum(state["stress_ext"], 0.0) * (1.0 + PARAMS["acid_wear"] * state["acid_number"])
        # Clipping Wiener increments at zero keeps the efficiency latent monotone.
        state["eta_loss"] += np.maximum(0.0, state["p_drift"] + PARAMS["eta_sigma"] * rng.standard_normal(ctx.n)) * driver
        state["leak"] += rng.gamma(PARAMS["leak_shape"], state["p_leak_scale"], ctx.n)
        state["acid_number"] += state["p_acid_rate"] * acceleration * (1.0 + PARAMS["acid_dust"] * np.maximum(ctx.dust_index, 0.0))
        state["filter_dp_kpa"] += state["p_filter_rate"] * (np.maximum(ctx.dust_index, 0.0) + state["acid_number"])
        state["precharge_loss"] += state["p_precharge_rate"]
        state["age_cycles"] += 1.0
        state["age_virtual"] += 1.0
        state["age_hours"] += ctx.flight_hours
        state["hi"] = 1.0 - self._damage(state)
        state["failed"] |= state["hi"] <= 0.0

    def emit(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> dict[str, np.ndarray]:
        span = PARAMS["pressure_healthy"] - PARAMS["pressure_limit"]
        return {
            "pump_pressure_kpa": PARAMS["pressure_healthy"] - span * self._damage(state) + PARAMS["pressure_noise"] * rng.standard_normal(ctx.n),
            "fluid_temp_c": self._temperature(state, ctx) + PARAMS["temp_noise"] * rng.standard_normal(ctx.n),
            "case_drain_flow_lpm": PARAMS["drain_base"] + PARAMS["drain_leak"] * state["leak"] + PARAMS["drain_noise"] * rng.standard_normal(ctx.n),
            "fluid_qty_pct": PARAMS["qty_base"] - PARAMS["qty_precharge"] * state["precharge_loss"] - PARAMS["qty_leak"] * state["leak"] + PARAMS["qty_noise"] * rng.standard_normal(ctx.n),
            "filter_dp_kpa": state["filter_dp_kpa"] + PARAMS["filter_noise"] * rng.standard_normal(ctx.n),
        }

    def health_index(self, state: State) -> np.ndarray:
        return state["hi"]

    def maintain(self, state: State, idx: np.ndarray, action: str, q: float, rng: np.random.Generator) -> None:
        if idx.size == 0:
            return
        if action == "inspect":
            state["n_inspections"][idx] += 1.0
            return
        if action not in ("repair", "overhaul", "replace"):
            raise ValueError(f"unknown action: {action}")
        if not 0.0 <= q <= 1.0:
            raise ValueError("q must be in [0, 1]")
        retained = q if action == "repair" else 0.0
        for key in (*self.latent_keys, "age_virtual"):
            state[key][idx] *= retained
        state["filter_dp_kpa"][idx] = 0.0
        if action == "replace":
            for key, value in self._params(idx.size, rng).items():
                state[key][idx] = value
            for key in ("age_cycles", "age_hours", "n_inspections"):
                state[key][idx] = 0.0
        state["hi"][idx] = 1.0 - self._damage(state)[idx]
        state["failed"][idx] = state["hi"][idx] <= 0.0
