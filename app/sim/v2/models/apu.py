"""Start/hour-driven APU margin loss, starter wear, oil consumption and shocks.

Life scaling (assumed, no citation): ``start_loss_c`` 0.024 -> 0.015 and ``hour_loss_c`` 0.012 -> 0.0075 (margin life ~1.6x
longer). The first values gave ~17-24 unscheduled APU removals per 24 tails per year (~0.8 per unit-year); the revised
rates give roughly 0.4-0.6 per unit-year, an MTBUR of order 10^3-10^4 FH given ~1 APU hour per flight.

Unit wear factors are lognormal. Margin failures use noise-free loss, with
surge events providing an independent compound-Poisson damage contribution.
"""

from __future__ import annotations

import numpy as np

from app.sim.v2.core import ACTIONS, State, register
from app.sim.v2.drivers import OperatingContext
from app.sim.v2.faults import fault_add, fault_mult

PARAMS = {
    "healthy_margin_c": 90.0,  # assumed (no citation)
    "limit_margin_c": 20.0,  # assumed (no citation)
    "start_loss_c": 0.015,  # assumed (no citation)
    "hour_loss_c": 0.0075,  # assumed (no citation)
    "unit_sigma": 0.2,  # assumed (no citation)
    "reference_oat_c": 15.0,  # assumed (no citation)
    "hot_coefficient": 0.025,  # assumed (no citation)
    "altitude_coefficient": 0.4,  # assumed (no citation)
    "altitude_scale_ft": 10000.0,  # assumed (no citation)
    "starter_rate": 0.004,  # assumed (no citation): seconds per start
    "starter_sigma": 0.15,  # assumed (no citation)
    "start_time_s": 25.0,  # assumed (no citation)
    "cold_s_per_c": 0.25,  # assumed (no citation)
    "oil_initial_pct": 100.0,  # assumed (no citation)
    "oil_rate_pct_per_h": 0.01,  # assumed (no citation)
    "oil_rate_sigma": 0.15,  # assumed (no citation)
    "oil_growth_hours": 5000.0,  # assumed (no citation)
    "oil_growth_power": 2.0,  # assumed (no citation)
    "shock_rate": 0.0005,  # assumed (no citation)
    "shock_scale_c": 1.5,  # assumed (no citation)
    "egt_limit_c": 950.0,  # assumed (no citation)
    "rpm_nominal_pct": 100.0,  # assumed (no citation)
    "rpm_droop_pct": 3.0,  # assumed (no citation)
    "margin_noise_c": 0.1,  # assumed (no citation)
    "rpm_noise_pct": 0.05,  # assumed (no citation)
    "start_noise_s": 0.1,  # assumed (no citation)
    "oil_noise_pct": 0.05,  # assumed (no citation)
    "peak_noise_c": 0.5,  # assumed (no citation)
}


def margin_current(state: State) -> np.ndarray:
    """Noise-free margin, retaining both progressive and shock loss."""
    return PARAMS["healthy_margin_c"] - state["loss"] - state["shock_loss"] - fault_add(state, "egt_overtemp")


def update_health(state: State) -> None:
    """Recompute health and latch margin hard-limit failures."""
    span = PARAMS["healthy_margin_c"] - PARAMS["limit_margin_c"]
    state["hi"][:] = np.clip((margin_current(state) - PARAMS["limit_margin_c"]) / span, 0.0, 1.0)
    state["failed"] |= margin_current(state) <= PARAMS["limit_margin_c"]


def update_latent(state: State, ctx: OperatingContext) -> None:
    """Deterministic vectorised start/hour wear, excluding stochastic shocks."""
    starts = np.maximum(0.0, ctx.apu_starts.astype(float))
    hours = np.maximum(0.0, ctx.apu_hours)
    hot = np.exp(PARAMS["hot_coefficient"] * np.maximum(0.0, ctx.oat_c - PARAMS["reference_oat_c"]))
    altitude = np.maximum(0.0, 1.0 + PARAMS["altitude_coefficient"] * ctx.density_alt_ft / PARAMS["altitude_scale_ft"])
    stress = np.maximum(0.0, state["stress_ext"])
    state["loss"] += (PARAMS["start_loss_c"] * starts * hot * altitude + PARAMS["hour_loss_c"] * hours) * stress * state["unit_factor"]
    state["starter_wear"] += state["starter_rate"] * starts * stress * fault_mult(state, "starter_failure")
    old_hours = state["age_hours"].copy()
    new_hours = old_hours + ctx.flight_hours
    scale = PARAMS["oil_growth_hours"]
    # Trapezoidal cycle-average of the power-law growth; consumed only while running.
    power = PARAMS["oil_growth_power"]
    growth = 1.0 + ((old_hours / scale) ** power + (new_hours / scale) ** power) / 2.0
    state["oil_loss"] += state["oil_rate"] * hours * growth * stress
    state["age_hours"][:] = new_hours
    state["age_cycles"] += 1.0
    state["age_virtual"] += 1.0
    update_health(state)


@register("apu")
class ApuModel:
    kind = "apu"
    channels: tuple[str, ...] = ("apu_egt_margin_c", "apu_rpm_pct", "apu_start_time_s", "apu_oil_qty_pct", "apu_peak_start_egt_c")

    def init(self, n: int, rng: np.random.Generator) -> State:
        return {
            "unit_factor": rng.lognormal(0.0, PARAMS["unit_sigma"], n),
            "starter_rate": rng.lognormal(np.log(PARAMS["starter_rate"]), PARAMS["starter_sigma"], n),
            "oil_rate": rng.lognormal(np.log(PARAMS["oil_rate_pct_per_h"]), PARAMS["oil_rate_sigma"], n),
            "loss": np.zeros(n),
            "shock_loss": np.zeros(n),
            "shock_count": np.zeros(n, dtype=np.int64),
            "starter_wear": np.zeros(n),
            "oil_loss": np.zeros(n),
            "age_cycles": np.zeros(n),
            "age_hours": np.zeros(n),
            "age_virtual": np.zeros(n),
            "hi": np.ones(n),
            "failed": np.zeros(n, dtype=bool),
            "stress_ext": np.ones(n),
        }

    def step(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> None:
        update_latent(state, ctx)
        events = rng.poisson(PARAMS["shock_rate"] * np.maximum(0.0, state["stress_ext"]))
        state["shock_count"] += events
        # Sum of k exponential shocks is Gamma(k, scale); k=0 gives exactly zero.
        state["shock_loss"] += rng.gamma(events.astype(float), PARAMS["shock_scale_c"])
        update_health(state)

    def emit(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> dict[str, np.ndarray]:
        margin = margin_current(state)
        start = (PARAMS["start_time_s"] + state["starter_wear"] + PARAMS["cold_s_per_c"] * np.maximum(0.0, PARAMS["reference_oat_c"] - ctx.oat_c)) * fault_mult(state, "starter_failure")
        return {
            "apu_egt_margin_c": margin + rng.normal(0.0, PARAMS["margin_noise_c"], ctx.n),
            "apu_rpm_pct": PARAMS["rpm_nominal_pct"] - PARAMS["rpm_droop_pct"] * (1.0 - state["hi"]) + rng.normal(0.0, PARAMS["rpm_noise_pct"], ctx.n),
            "apu_start_time_s": np.maximum(0.0, start + rng.normal(0.0, PARAMS["start_noise_s"], ctx.n)),
            "apu_oil_qty_pct": np.clip(PARAMS["oil_initial_pct"] - state["oil_loss"] + rng.normal(0.0, PARAMS["oil_noise_pct"], ctx.n), 0.0, PARAMS["oil_initial_pct"]),
            "apu_peak_start_egt_c": PARAMS["egt_limit_c"] - margin + rng.normal(0.0, PARAMS["peak_noise_c"], ctx.n),
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
        for key in ("loss", "shock_loss", "starter_wear", "oil_loss", "age_virtual"):
            state[key][idx] *= factor
        if action == "overhaul":
            state["shock_count"][idx] = 0
        span = PARAMS["healthy_margin_c"] - PARAMS["limit_margin_c"]
        margin = PARAMS["healthy_margin_c"] - state["loss"][idx] - state["shock_loss"][idx]
        state["hi"][idx] = np.clip((margin - PARAMS["limit_margin_c"]) / span, 0.0, 1.0)
        state["failed"][idx] = margin <= PARAMS["limit_margin_c"]
