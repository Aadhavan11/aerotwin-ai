"""Battery and generator degradation priors. A fit against the NASA battery cells is committed
(``calibration/electrical_fit.json``) and loaded by ``_apply_fit``; it overrides 3 of 36 parameters
(resistance_fade, capacity_noise, resistance_noise).

Rates target roughly 6200 nominal flights (~12k FH). The calendar watermark is bookkeeping,
not damage: maintenance preserves it to avoid recharging elapsed fleet calendar time.

Life scaling (assumed, no citation): the first version aged out at ~2000 flights, giving ~19 unscheduled removals per 24
tails per year for a battery + generator aggregate; the per-flight fade rate is divided by 3.1 (life -> 6200 flights). Calendar
fade (sqrt of days) is physical time and is left unchanged. No electrical rate was changed by data (docs/SIM-V2-CALIBRATION.md:
3 of 36 electrical parameters: resistance_fade, capacity_noise, resistance_noise). Reference Weibull shape 4.5 implied a near-deterministic wear-out
(CV ~0.25); mixed battery fade, thermal events and generator wear give beta of order 2-3, so ``param_sigma`` is raised
0.25 -> 0.40 and the reference shape is set to the value measured on the model under accelerated stress.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from app.sim.v2.core import State, register
from app.sim.v2.drivers import OperatingContext
from app.sim.v2.faults import fault_add, fault_level, fault_rate

PARAMS: dict[str, float] = {
    "capacity_healthy": 100.0,  # assumed (no citation): GENERIC healthy capacity, percent.
    "capacity_limit": 70.0,  # assumed (no citation): GENERIC hard failure limit, percent.
    "life_cycles": 6200.0,  # assumed (no citation): nominal GENERIC life.
    "param_sigma": 0.40,  # assumed (no citation): lognormal spread, CV about 0.25.
    "cycle_fade": 0.0022,  # assumed (no citation): percentage points per flight/APU start.
    "calendar_fade": 0.1,  # assumed (no citation): percentage points per sqrt(day).
    "activation_k": 4000.0,  # assumed (no citation): battery Arrhenius Ea/R, kelvin.
    "kelvin_offset": 273.15,  # assumed (no citation): Celsius-to-kelvin conversion.
    "temp_ref_c": 35.0,  # assumed (no citation): battery reference temperature.
    "oat_ref_c": 15.0,  # assumed (no citation): nominal ambient temperature.
    "oat_gain": 0.6,  # assumed (no citation): ambient-to-battery thermal transfer.
    "resistance_base": 8.0,  # assumed (no citation): fresh battery milliohms.
    "resistance_fade": 0.03,  # assumed (no citation): resistance gain per fade percentage point.
    "resistance_rate": 0.0003,  # assumed (no citation): extra milliohms per flight.
    "generator_rate": 0.0002,  # assumed (no citation): generator damage per flight hour.
    "lam_thermal": 0.0001,  # assumed (no citation); weakest: rare thermal-event frequency lacks evidence.
    "thermal_fade": 0.6,  # assumed (no citation); weakest: capacity step per thermal event.
    "thermal_resistance": 0.4,  # assumed (no citation): milliohm step per thermal event.
    "capacity_noise": 0.05,  # assumed (no citation): capacity sensor standard deviation.
    "resistance_noise": 0.01,  # assumed (no citation): resistance sensor standard deviation.
    "voltage_nom": 28.0,  # assumed (no citation): nominal DC bus voltage.
    "voltage_floor": 0.85,  # assumed (no citation): capacity-independent voltage fraction.
    "voltage_capacity": 0.15,  # assumed (no citation): capacity-dependent voltage fraction.
    "load_a": 35.0,  # assumed (no citation): base battery current, amperes.
    "load_payload": 0.5,  # assumed (no citation): payload-dependent current gain.
    "mohm_per_ohm": 1000.0,  # assumed (no citation): resistance unit conversion.
    "voltage_noise": 0.03,  # assumed (no citation): voltage sensor standard deviation.
    "frequency_base": 400.0,  # assumed (no citation): nominal AC bus frequency, Hz.
    "frequency_wear": -0.2,  # assumed (no citation): frequency droop per generator damage.
    "frequency_noise": 0.02,  # assumed (no citation): frequency sensor standard deviation.
    "oil_temp_base": 70.0,  # assumed (no citation): fresh IDG oil temperature, C.
    "oil_oat_gain": 0.4,  # assumed (no citation): ambient-to-oil thermal transfer.
    "oil_wear_gain": 15.0,  # assumed (no citation): oil heating per generator damage.
    "oil_noise": 0.5,  # assumed (no citation): oil sensor standard deviation, C.
    "failure_median_accel": 1200.0,  # assumed (no citation): flights at OAT 45, stress 2.
    "failure_shape_accel": 2.8,  # measured on this model under accelerated stress (beta 2.8; was assumed 4.5), not validation.
}

PRIOR_PARAMS: dict[str, float] = dict(PARAMS)  # the unfitted assumed priors (no citation), kept for before/after comparisons
FIT_PATH = Path(__file__).resolve().parents[1] / "calibration" / "electrical_fit.json"


def _apply_fit(path: Path = FIT_PATH) -> None:
    """Override PARAMS with the committed data-fitted values (JSON key ``overrides``); defaults stay if the file is absent."""
    if not path.is_file():
        return
    for key, value in json.loads(path.read_text()).get("overrides", {}).items():
        if key in PARAMS:
            PARAMS[key] = float(value)


_apply_fit()


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
        state.update(hi=np.ones(n), failed=np.zeros(n, dtype=bool), stress_ext=np.ones(n), bus_voltage_v=np.full(n, PARAMS["voltage_nom"]))
        return state

    def _hi(self, state: State) -> np.ndarray:
        span = PARAMS["capacity_healthy"] - PARAMS["capacity_limit"]
        return 1.0 - np.clip(state["fade_pct"] / span, 0.0, 1.0)

    def _bus_voltage(self, state: State, ctx: OperatingContext) -> np.ndarray:
        capacity = np.clip(PARAMS["capacity_healthy"] - state["fade_pct"], 0.0, PARAMS["capacity_healthy"])
        load = PARAMS["load_a"] * (1.0 + PARAMS["load_payload"] * ctx.payload_frac)
        # Loaded DC voltage = V_nom * (.85 + .15 * cap/100) - I_load * R_mohm/1000.
        voltage = PARAMS["voltage_nom"] * (PARAMS["voltage_floor"] + PARAMS["voltage_capacity"] * capacity / PARAMS["capacity_healthy"])
        return voltage - load * self._resistance(state) / PARAMS["mohm_per_ohm"]

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
        fade_rate = fault_rate(state, "cell_short")
        state["fade_pct"] += ((calendar + cycles) * acceleration + events * PARAMS["thermal_fade"]) * stress * fade_rate
        state["resistance_growth"] += state["p_resistance_rate"] * acceleration + events * PARAMS["thermal_resistance"]
        state["generator_wear"] += state["p_generator_rate"] * np.maximum(ctx.flight_hours, 0.0) * stress * (1.0 + fault_level(state, "idg_bearing"))
        state["cal_days_prev"] = days
        state["age_cycles"] += 1.0
        state["age_virtual"] += 1.0
        state["age_hours"] += ctx.flight_hours
        state["hi"] = self._hi(state)
        state["failed"] |= state["hi"] <= 0.0
        state["bus_voltage_v"] = self._bus_voltage(state, ctx)  # noise-free bus voltage, read by the coupling graph

    def emit(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> dict[str, np.ndarray]:
        capacity = np.clip(PARAMS["capacity_healthy"] - state["fade_pct"], 0.0, PARAMS["capacity_healthy"])
        resistance = self._resistance(state)
        voltage = self._bus_voltage(state, ctx)
        return {
            "battery_capacity_pct": np.clip(capacity + PARAMS["capacity_noise"] * rng.standard_normal(ctx.n), 0.0, PARAMS["capacity_healthy"]),
            "battery_resistance_mohm": resistance + PARAMS["resistance_noise"] * rng.standard_normal(ctx.n),
            "bus_voltage_v": voltage + PARAMS["voltage_noise"] * rng.standard_normal(ctx.n),
            "bus_frequency_hz": PARAMS["frequency_base"] + PARAMS["frequency_wear"] * state["generator_wear"] + PARAMS["frequency_noise"] * rng.standard_normal(ctx.n),
            "idg_oil_temp_c": (PARAMS["oil_temp_base"] + PARAMS["oil_oat_gain"] * (ctx.oat_c - PARAMS["oat_ref_c"])
                               + PARAMS["oil_wear_gain"] * state["generator_wear"] + fault_add(state, "idg_bearing") + PARAMS["oil_noise"] * rng.standard_normal(ctx.n)),
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
