"""Single source for cross-module simulator constants (one constant per concept).

Lives in ``app.sim`` (not ``app.sim.v2``) so v1 modules can import it without the v2 package import cycle;
``app.sim.v2.constants`` re-exports the same objects.

Everything that needs "flights per day", "engine mean on-wing life" or an LLP cycle limit imports it from here;
``tests/test_sim_v2_constants.py`` asserts nobody re-defines them. This module must stay dependency-free.

All values are ASSUMED (no citation) unless a comment says "fitted": they are documented priors for a SIMULATED
narrow-body fleet, not operator data.
"""
from __future__ import annotations

# ---- utilisation ----
# Flown legs (= flight cycles) per aircraft per calendar day at utilisation 1.0. 365 * 6 = 2,190 cycles = 1 year.
FLIGHTS_PER_DAY = 6.0
BLOCK_HOURS_PER_FLIGHT = 2.0  # the schedule flies 2 h legs (app.sim.fleet / app.twin.state use the same figure)

# ---- engine life ----
# C-MAPSS FD001 mean cycles-to-failure (fitted on the NASA data, docs/SIM-V2-ENGINE.md). These are model cycles, not flights.
ENGINE_CMAPSS_MEAN_LIFE_CYCLES = 206.3
# One model cycle = this many real flights (ASSUMED, no citation): maps the C-MAPSS axis onto a narrow-body
# shop-visit interval of 15-25k flights.
ENGINE_FLIGHTS_PER_MODEL_CYCLE = 87.0
# THE engine mean on-wing life in flights (v2): ~17,950 flights. Every v2 consumer derives from this.
ENGINE_MEAN_ON_WING_FLIGHTS = ENGINE_CMAPSS_MEAN_LIFE_CYCLES * ENGINE_FLIGHTS_PER_MODEL_CYCLE

# ---- life-limited parts (cycles = flights) ----
# ASSUMED (no citation) limits, narrow-body range 20-30k; fictional part numbers live in app.ops.llp.
LLP_CYCLE_LIMITS: dict[str, float] = {
    "fan_disk": 30000.0, "hpc_rotor": 25000.0, "hpt_disk": 20000.0, "lpt_shaft": 30000.0,
    "mlg_leg_l": 25000.0, "mlg_leg_r": 25000.0, "nlg_leg": 30000.0, "mlg_retract_actuator": 20000.0,
}
# The v2 engine model carries ONE surrogate LLP counter; it uses the HPC rotor limit as the representative value.
ENGINE_MODEL_LLP_LIMIT_CYCLES = LLP_CYCLE_LIMITS["hpc_rotor"]

# ---- v1 only ----
# v1 fleet counters (app.sim.fleet) cap an engine's cycles-since-overhaul here. It is a legacy counter scale, NOT an
# engine life; v1 callers (app.insights.reliability.report) keep it so the v1 output stays byte-identical.
LEGACY_V1_ENGINE_COUNTER_CAP = 8200
# Fallback legs/day when a tail's rotation is too short to estimate a rate (planner/LLP), same as the fleet rate.
DEFAULT_LEGS_PER_DAY = FLIGHTS_PER_DAY
