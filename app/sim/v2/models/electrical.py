"""Battery and generator degradation priors; NASA battery calibration comes later.

Rates target roughly 2000 nominal flights. The calendar watermark is bookkeeping,
not damage: maintenance preserves it to avoid recharging elapsed fleet calendar time.
"""

from __future__ import annotations

import numpy as np

from app.sim.v2.core import State, register
from app.sim.v2.drivers import OperatingContext

PARAMS: dict[str, float] = {
    "capacity_healthy": 100.0,  # literature prior, unverified: GENERIC healthy capacity, percent.
    "capacity_limit": 70.0,  # literature prior, unverified: GENERIC hard failure limit, percent.
    "life_cycles": 2000.0,  # literature prior, unverified: nominal GENERIC life.
    "param_sigma": 0.25,  # literature prior, unverified: lognormal spread, CV about 0.25.
    "cycle_fade": 0.0068,  # literature prior, unverified: percentage points per flight/APU start.
    "calendar_fade": 0.1,  # literature prior, unverified: percentage points per sqrt(day).
    "activation_k": 4000.0,  # literature prior, unverified: battery Arrhenius Ea/R, kelvin.
    "kelvin_offset": 273.15,  # literature prior, unverified: Celsius-to-kelvin conversion.
    "temp_ref_c": 35.0,  # literature prior, unverified: battery reference temperature.
    "oat_ref_c": 15.0,  # literature prior, unverified: nominal ambient temperature.
    "oat_gain": 0.6,  # literature prior, unverified: ambient-to-battery thermal transfer.
    "resistance_base": 8.0,  # literature prior, unverified: fresh battery milliohms.
    "resistance_fade": 0.03,  # literature prior, unverified: resistance gain per fade percentage point.
    "resistance_rate": 0.0003,  # literature prior, unverified: extra milliohms per flight.
    "generator_rate": 0.0002,  # literature prior, unverified: generator damage per flight hour.
    "lam_thermal": 0.0001,  # literature prior, unverified; weakest: rare thermal-event frequency lacks evidence.
    "thermal_fade": 0.6,  # literature prior, unverified; weakest: capacity step per thermal event.
    "thermal_resistance": 0.4,  # literature prior, unverified: milliohm step per thermal event.
    "capacity_noise": 0.05,  # literature prior, unverified: capacity sensor standard deviation.
    "resistance_noise": 0.01,  # literature prior, unverified: resistance sensor standard deviation.
    "voltage_nom": 28.0,  # literature prior, unverified: nominal DC bus voltage.
    "voltage_floor": 0.85,  # literature prior, unverified: capacity-independent voltage fraction.
    "voltage_capacity": 0.15,  # literature prior, unverified: capacity-dependent voltage fraction.
    "load_a": 35.0,  # literature prior, unverified: base battery current, amperes.
    "load_payload": 0.5,  # literature prior, unverified: payload-dependent current gain.
    "mohm_per_ohm": 1000.0,  # literature prior, unverified: resistance unit conversion.
    "voltage_noise": 0.03,  # literature prior, unverified: voltage sensor standard deviation.
    "frequency_base": 400.0,  # literature prior, unverified: nominal AC bus frequency, Hz.
    "frequency_wear": -0.2,  # literature prior, unverified: frequency droop per generator damage.
    "frequency_noise": 0.02,  # literature prior, unverified: frequency sensor standard deviation.
    "oil_temp_base": 70.0,  # literature prior, unverified: fresh IDG oil temperature, C.
    "oil_oat_gain": 0.4,  # literature prior, unverified: ambient-to-oil thermal transfer.
    "oil_wear_gain": 15.0,  # literature prior, unverified: oil heating per generator damage.
    "oil_noise": 0.5,  # literature prior, unverified: oil sensor standard deviation, C.
    "failure_median_accel": 470.0,  # literature prior, unverified: flights at OAT 45, stress 2.
    "failure_shape_accel": 4.5,  # literature prior, unverified: reference Weibull shape, not validation.
}


@register("electrical")
class ElectricalModel:
    kind = "electrical"
    channels = ("battery_capacity_pct", "battery_resistance_mohm", "bus_voltage_v", "bus_frequency_hz", "idg_oil_temp_c")
    latent_keys = ("fade_pct", "resistance_growth", "generator_wear")
    parameter_keys = ("p_cycle_fade", "p_calendar_fade", "p_resistance_rate", "p_generator_rate", "p_r0")

    def _params(self, n: int, rng: np.random.Generator) -> State:
        priors = ("cycle_fade", "calendar_fade", "resistance_rate", "generator_rate", "resistance_base")
        return {key: PARAMS[prior] * rng.lognormal(0.0, PARAMS["param_sigma"], n) for key, prior in zip(self.parameter_keys, priors)}

    def init(self, n: int, rng: np.random.Generator) -> State:
        state = self._params(n, rng)
        for key in (*self.latent_keys, "cal_days_prev", "age_cycles", "age_hours", "age_virtual", "n_inspections"):
            state[key] = np.zeros(n)
        state.update(hi=np.ones(n), failed=np.zeros(n, dtype=bool), stress_ext=np.ones(n))
        return state

    def _hi(self, state: State) -> np.ndarray:
        span = PARAMS["capacity_healthy"] - PARAMS["capacity_limit"]
        return 1.0 - np.clip(state["fade_pct"] / span, 0.0, 1.0)

    def _resistance(self, state: State) -> np.ndarray:
        return state["p_r0"] * (1.0 + PARAMS["resistance_fade"] * state["fade_pct"]) + state["resistance_growth"]

    def step(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> None:
        temperature_k = np.maximum(PARAMS["temp_ref_c"] + PARAMS["oat_gain"] * (ctx.oat_c - PARAMS["oat_ref_c"]) + PARAMS["kelvin_offset"], 1.0)
        acceleration = np.exp(PARAMS["activation_k"] * (1.0 / (PARAMS["temp_ref_c"] + PARAMS["kelvin_offset"]) - 1.0 / temperature_k))
        days = np.maximum(state["cal_days_prev"], ctx.calendar_days)
        # The watermark prevents negative calendar increments if a context repeats or goes backwards.
        calendar = state["p_calendar_fade"] * (np.sqrt(days) - np.sqrt(state["cal_days_prev"]))
        cycles = state["p_cycle_fade"] * (1.0 + np.maximum(ctx.apu_starts, 0.0))
        events = rng.poisson(PARAMS["lam_thermal"], ctx.n)
        stress = np.maximum(state["stress_ext"], 0.0)
        state["fade_pct"] += ((calendar + cycles) * acceleration + events * PARAMS["thermal_fade"]) * stress
        state["resistance_growth"] += state["p_resistance_rate"] * acceleration + events * PARAMS["thermal_resistance"]
        state["generator_wear"] += state["p_generator_rate"] * np.maximum(ctx.flight_hours, 0.0) * stress
        state["cal_days_prev"] = days
        state["age_cycles"] += 1.0
        state["age_virtual"] += 1.0
        state["age_hours"] += ctx.flight_hours
        state["hi"] = self._hi(state)
        state["failed"] |= state["hi"] <= 0.0

    def emit(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> dict[str, np.ndarray]:
        capacity = np.clip(PARAMS["capacity_healthy"] - state["fade_pct"], 0.0, PARAMS["capacity_healthy"])
        resistance = self._resistance(state)
        load = PARAMS["load_a"] * (1.0 + PARAMS["load_payload"] * ctx.payload_frac)
        # Loaded DC voltage = V_nom * (.85 + .15 * cap/100) - I_load * R_mohm/1000.
        voltage = PARAMS["voltage_nom"] * (PARAMS["voltage_floor"] + PARAMS["voltage_capacity"] * capacity / PARAMS["capacity_healthy"])
        voltage -= load * resistance / PARAMS["mohm_per_ohm"]
        return {
            "battery_capacity_pct": np.clip(capacity + PARAMS["capacity_noise"] * rng.standard_normal(ctx.n), 0.0, PARAMS["capacity_healthy"]),
            "battery_resistance_mohm": resistance + PARAMS["resistance_noise"] * rng.standard_normal(ctx.n),
            "bus_voltage_v": voltage + PARAMS["voltage_noise"] * rng.standard_normal(ctx.n),
            "bus_frequency_hz": PARAMS["frequency_base"] + PARAMS["frequency_wear"] * state["generator_wear"] + PARAMS["frequency_noise"] * rng.standard_normal(ctx.n),
            "idg_oil_temp_c": (PARAMS["oil_temp_base"] + PARAMS["oil_oat_gain"] * (ctx.oat_c - PARAMS["oat_ref_c"])
                               + PARAMS["oil_wear_gain"] * state["generator_wear"] + PARAMS["oil_noise"] * rng.standard_normal(ctx.n)),
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
        if action == "replace":
            for key, value in self._params(idx.size, rng).items():
                state[key][idx] = value
            for key in ("age_cycles", "age_hours", "n_inspections"):
                state[key][idx] = 0.0
        state["hi"][idx] = self._hi(state)[idx]
        state["failed"][idx] = state["hi"][idx] <= 0.0
