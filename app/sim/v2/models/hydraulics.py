"""Monotone hydraulic degradation priors. A fit against the UCI hydraulic test-rig data is committed
(``calibration/hydraulics_fit.json``) and loaded by ``_apply_fit``; its two recorded overrides (pressure_noise, temp_noise) equal the priors, so 0 of 36 parameters changed.

Rates target roughly 9000 nominal flights (~18k FH at 2 FH/flight), not validated service-life predictions.
Filter differential pressure is excess above a fresh filter (zero kPa).

Life scaling (assumed, no citation): the first version used 2000 flights (~4k FH), which produced ~27 unscheduled
removals per 24 tails per year (MTBUR ~3.9k FH), far below pump/system MTBUR of order 10^4 FH. All per-flight damage
rates below were divided by 4.5 (life 2000 -> 9000 flights) so every channel keeps its shape as a function of life
fraction; none of these rates was changed by data (docs/SIM-V2-CALIBRATION.md: 0 of 36 hydraulics parameters).
Pump cavitation (``fault_pump_cavitation``): cavitation erodes the pump, so the efficiency-loss drift is multiplied by
``(1/m(sev))**3`` with ``m(sev) = 1 - (1 - effect_mult) * sev``. ``effect_mult`` was 0.7 (severity 1 -> 2.9x drift: hi
0.98 -> 0.90 in 700 cycles, no removal) and is 0.35 after the stress-scenario review (severity 1 -> 23x drift: hi below
the 0.3 finding threshold in roughly 300-700 cycles, so the next 45-day inspection detects it, removal within ~1000
cycles; severity 0.3 -> 1.9x). Case-drain flow also rises with severity x efficiency loss (worn pump internals).
Wear-out shape: the earlier reference Weibull shape 6.0 (CV ~0.2) was near-deterministic wear-out, unlike
pumps/valves/lines whose mixed contamination, leak and wear causes give beta of order 2-3. ``param_sigma`` is raised
0.25 -> 0.45 and the reference shape set to the measured value of the model under accelerated stress.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from app.sim.v2.core import State, register
from app.sim.v2.drivers import OperatingContext
from app.sim.v2.faults import fault_level, fault_rate

PARAMS: dict[str, float] = {
    "pressure_healthy": 20700.0,  # assumed (no citation): GENERIC healthy pressure, kPa.
    "pressure_limit": 17200.0,  # assumed (no citation): GENERIC failure limit, kPa.
    "life_cycles": 9000.0,  # assumed (no citation): nominal GENERIC life.
    "param_sigma": 0.45,  # assumed (no citation): lognormal spread, CV about 0.25.
    "eta_drift": 0.0000667,  # assumed (no citation): efficiency loss per flight.
    "eta_sigma": 0.0000133,  # assumed (no citation): Wiener increment standard deviation.
    "leak_shape": 2.0,  # assumed (no citation): gamma shape per flight.
    "leak_scale": 0.0000111,  # assumed (no citation): gamma leak increment scale.
    "precharge_rate": 0.00000556,  # assumed (no citation): gas loss per flight.
    "acid_rate": 0.0000333,  # assumed (no citation): acid-number proxy per flight.
    "acid_wear": 0.5,  # assumed (no citation); weakest: proxy-to-pump coupling lacks calibration.
    "acid_dust": 2.0,  # assumed (no citation): contamination sensitivity to dust.
    "filter_rate": 0.00889,  # assumed (no citation): kPa per dust/acid exposure.
    "leak_weight": 1.0,  # assumed (no citation): pressure damage per leak proxy.
    "precharge_weight": 1.0,  # assumed (no citation): pressure damage per gas-loss proxy.
    "activation_k": 4000.0,  # assumed (no citation): Arrhenius Ea/R, kelvin.
    "kelvin_offset": 273.15,  # assumed (no citation): Celsius-to-kelvin conversion.
    "temp_ref_c": 55.0,  # assumed (no citation): fluid reference temperature.
    "oat_ref_c": 15.0,  # assumed (no citation): nominal ambient temperature.
    "oat_gain": 0.6,  # assumed (no citation): ambient-to-fluid thermal transfer.
    "heat_eta": 4.0,  # assumed (no citation): efficiency-loss heating, C/proxy.
    "hours_ref": 2.0,  # assumed (no citation): nominal flight hours.
    "heat_hours": 2.0,  # assumed (no citation): fluid heating per extra hour.
    "pressure_factor_ref": 1.53,  # assumed (no citation): (1 + payload .7) * (1 - derate .1).
    "pressure_noise": 15.0,  # assumed (no citation): sensor standard deviation, kPa.
    "temp_noise": 1.5,  # assumed (no citation): fluid sensor standard deviation, C.
    "drain_base": 0.5,  # assumed (no citation): fresh case drain, L/min.
    "drain_leak": 8.0,  # assumed (no citation): case-drain gain, L/min/proxy.
    "drain_noise": 0.03,  # assumed (no citation): drain sensor standard deviation.
    "qty_base": 100.0,  # assumed (no citation): fresh fluid quantity, percent.
    "qty_precharge": 30.0,  # assumed (no citation): quantity loss per gas-loss proxy.
    "qty_leak": 10.0,  # assumed (no citation): quantity loss per leak proxy.
    "qty_noise": 0.1,  # assumed (no citation): quantity sensor standard deviation.
    "filter_noise": 0.1,  # assumed (no citation): differential-pressure sensor noise, kPa.
    "failure_median_accel": 2925.0,  # assumed (no citation): flights at OAT 45, dust .9, stress 2.
    "failure_shape_accel": 3.0,  # measured on this model under accelerated stress (beta 2.9; was assumed 6.0), not validation.
}

PRIOR_PARAMS: dict[str, float] = dict(PARAMS)  # the unfitted assumed priors (no citation), kept for before/after comparisons
FIT_PATH = Path(__file__).resolve().parents[1] / "calibration" / "hydraulics_fit.json"


def _apply_fit(path: Path = FIT_PATH) -> None:
    """Override PARAMS with the committed data-fitted values (JSON key ``overrides``); defaults stay if the file is absent."""
    if not path.is_file():
        return
    for key, value in json.loads(path.read_text()).get("overrides", {}).items():
        if key in PARAMS:
            PARAMS[key] = float(value)


_apply_fit()


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
        driver = acceleration * pressure_cycles * np.maximum(state["stress_ext"], 0.0) * (1.0 + PARAMS["acid_wear"] * state["acid_number"]) * fault_rate(state, "pump_cavitation")
        # Clipping Wiener increments at zero keeps the efficiency latent monotone.
        state["eta_loss"] += np.maximum(0.0, state["p_drift"] + PARAMS["eta_sigma"] * rng.standard_normal(ctx.n)) * driver
        state["leak"] += rng.gamma(PARAMS["leak_shape"], state["p_leak_scale"], ctx.n) * fault_rate(state, "line_leak")
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
            # a cavitating pump erodes its internals: case-drain flow rises with severity x efficiency loss (no new constant)
            "case_drain_flow_lpm": (PARAMS["drain_base"] + PARAMS["drain_leak"] * (state["leak"] + fault_level(state, "pump_cavitation") * state["eta_loss"])
                                    + PARAMS["drain_noise"] * rng.standard_normal(ctx.n)),
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
