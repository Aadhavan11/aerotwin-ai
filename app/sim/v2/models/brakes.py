"""Carbon heat-sink brake model (v2).

Latent state: pad/heat-sink wear (mm), lumped disc temperature, oxidation loss, rejected-takeoff (RTO) count.
Wear is driven per brake application (taxi snubs dominate) scaled by energy per application; an Arrhenius oxidation
term switches on above ~400 C-equivalent; RTOs are a Poisson shock process (large wear, very high temperature).

Every constant in PARAMS is a assumed (no citation): nothing here is calibrated against fleet data.
"""

from __future__ import annotations

import numpy as np

from app.sim.v2.core import State, register
from app.sim.v2.drivers import OperatingContext
from app.sim.v2.faults import fault_add

# All values: assumed (no citation).
PARAMS: dict[str, float] = {
    "K_APP_MM": 0.0005,        # mm of wear per brake application at the reference energy (taxi snubs dominate); assumed (no citation)
    "K_APP_SIGMA": 0.25,       # lognormal sigma of per-unit wear coefficient (manufacturing/install spread); assumed (no citation)
    "E_REF_MJ": 1.3,           # reference energy per application, MJ; assumed (no citation)
    "BETA_ENERGY": 0.5,        # exponent of wear on energy per application (<1 keeps wear monotone in application count at fixed total energy); assumed (no citation)
    "WET_FACTOR": 1.15,        # wear multiplier on wet runways (more modulation and re-applications); assumed (no citation)
    "NOISE_SIGMA": 0.15,       # lognormal sigma of cycle-to-cycle wear scatter; assumed (no citation)
    "LIMIT_MM": 12.0,          # mean usable wear-pin limit, mm (matches v1 limit); assumed (no citation)
    "LIMIT_SIGMA_MM": 0.4,     # per-unit spread of the wear limit, mm; assumed (no citation)
    "HEAT_CAP_MJ_K": 0.012,    # lumped heat capacity of one heat-sink, MJ/K; assumed (no citation)
    "N_BRAKES": 4.0,           # braked wheels sharing the landing energy; assumed (no citation)
    "LANDING_ENERGY_FRAC": 0.7,  # fraction of cycle brake energy absorbed in the landing roll (rest is taxi); assumed (no citation)
    "TAU_COOL_MIN": 45.0,      # mean cooling time constant of the heat-sink, min; assumed (no citation)
    "TAU_SIGMA": 0.15,         # lognormal sigma of cooling time constant; assumed (no citation)
    "TURNAROUND_MIN": 35.0,    # ground time between landing and next heat-up, min; assumed (no citation)
    "OX_REF_MM": 0.0003,       # oxidation loss per cycle at the reference temperature, mm; assumed (no citation)
    "OX_T_REF_C": 400.0,       # reference temperature of the oxidation Arrhenius term, C; assumed (no citation)
    "OX_EA_J_MOL": 100000.0,   # apparent activation energy of carbon oxidation, J/mol; assumed (no citation)
    "OX_SIGMA": 0.3,           # lognormal sigma of per-unit oxidation sensitivity (antioxidant coat quality); assumed (no citation)
    "OX_CAP_MM": 2.0,          # cap of oxidation loss in one cycle, mm (dwell time at peak is short); assumed (no citation)
    "RTO_RATE": 4.0e-4,        # rejected-takeoff probability per cycle; assumed (no citation)
    "RTO_WEAR_MIN_MM": 0.4,    # minimum RTO wear, mm; assumed (no citation)
    "RTO_WEAR_MAX_MM": 1.2,    # maximum RTO wear, mm; assumed (no citation)
    "RTO_PEAK_C": 850.0,       # base disc temperature after an RTO, C; assumed (no citation)
    "AGE_COEF": 0.2,           # wear growth with Kijima virtual age (heat-sink fatigue); assumed (no citation)
    "AGE_REF_CYCLES": 2000.0,  # virtual age scale for AGE_COEF, cycles; assumed (no citation)
    "PIN_SD_MM": 0.10,         # wear-pin reading noise between inspections, mm; assumed (no citation)
    "PIN_SD_INSPECT_MM": 0.02,  # wear-pin reading noise on an inspection cycle, mm; assumed (no citation)
    "TEMP_SD_C": 5.0,          # disc temperature sensor noise, C; assumed (no citation)
    "P_BRAKE_KPA": 9000.0,     # brake pressure at reference energy per application, kPa; assumed (no citation)
    "P_SD_KPA": 150.0,         # brake pressure sensor noise, kPa; assumed (no citation)
    "OVERHAUL_FRAC": 0.25,     # damage fraction kept by overhaul (times q); assumed (no citation)
}
_R_GAS = 8.314


def _sample(n: int, rng: np.random.Generator) -> dict[str, np.ndarray]:
    p = PARAMS
    return {
        "k_app": p["K_APP_MM"] * rng.lognormal(0.0, p["K_APP_SIGMA"], n),
        "limit_mm": np.maximum(p["LIMIT_MM"] + p["LIMIT_SIGMA_MM"] * rng.standard_normal(n), 8.0),
        "ox_sens": rng.lognormal(0.0, p["OX_SIGMA"], n),
        "tau_min": p["TAU_COOL_MIN"] * rng.lognormal(0.0, p["TAU_SIGMA"], n),
    }


@register("brakes")
class BrakesModel:
    kind = "brakes"
    channels = ("pad_wear_mm", "disc_temp_c", "brake_pressure_kpa", "brake_apps_cycle")

    def init(self, n: int, rng: np.random.Generator) -> State:
        st: State = _sample(n, rng)
        st.update(
            wear_mm=np.zeros(n), ox_mm=np.zeros(n), temp_c=np.full(n, 20.0), rto_count=np.zeros(n),
            stress_ext=np.ones(n), inspected=np.zeros(n, dtype=bool), e_app=np.full(n, PARAMS["E_REF_MJ"]),
            age_cycles=np.zeros(n), age_hours=np.zeros(n), age_virtual=np.zeros(n), hi=np.ones(n), failed=np.zeros(n, dtype=bool),
        )
        return st

    def step(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> None:
        p = PARAMS
        n = ctx.n
        z_wear, u_rto, u_sev = rng.standard_normal(n), rng.random(n), rng.random(n)
        apps = np.maximum(ctx.brake_applications.astype(float), 1.0)
        e_app = ctx.brake_energy_mj / apps
        # Thermal: cool from the previous peak toward ambient, then heat by the landing-roll energy.
        dt_min = ctx.flight_hours * 60.0 + p["TURNAROUND_MIN"]
        temp0 = ctx.oat_c + (state["temp_c"] - ctx.oat_c) * np.exp(-dt_min / state["tau_min"])
        peak = temp0 + p["LANDING_ENERGY_FRAC"] * ctx.brake_energy_mj / (p["N_BRAKES"] * p["HEAT_CAP_MJ_K"])
        rto = u_rto < p["RTO_RATE"]
        peak = np.where(rto, np.maximum(peak, p["RTO_PEAK_C"] + 100.0 * u_sev), peak)
        # Component faults (hidden truth, written by FaultInjector): stuck brake drag / RTO overheat raise the peak temperature.
        peak = peak + fault_add(state, "stuck_brake") + fault_add(state, "rejected_takeoff_overheat")
        # Mechanical wear per application.
        wet = np.where(ctx.wet_runway, p["WET_FACTOR"], 1.0)
        age_f = 1.0 + p["AGE_COEF"] * state["age_virtual"] / p["AGE_REF_CYCLES"]
        mech = state["k_app"] * apps * (e_app / p["E_REF_MJ"]) ** p["BETA_ENERGY"] * wet * age_f * state["stress_ext"] * np.exp(p["NOISE_SIGMA"] * z_wear - 0.5 * p["NOISE_SIGMA"] ** 2)
        # Arrhenius oxidation relative to the reference temperature.
        tk, tref = peak + 273.15, p["OX_T_REF_C"] + 273.15
        ox = np.minimum(p["OX_REF_MM"] * state["ox_sens"] * np.exp(p["OX_EA_J_MOL"] / _R_GAS * (1.0 / tref - 1.0 / tk)), p["OX_CAP_MM"])
        shock = np.where(rto, p["RTO_WEAR_MIN_MM"] + (p["RTO_WEAR_MAX_MM"] - p["RTO_WEAR_MIN_MM"]) * u_sev, 0.0)
        state["wear_mm"] += mech + ox + shock
        state["ox_mm"] += ox
        state["rto_count"] += rto
        state["temp_c"] = peak
        state["e_app"] = e_app
        state["inspected"] = np.zeros(n, dtype=bool)
        state["age_cycles"] += 1
        state["age_virtual"] += 1
        state["age_hours"] += ctx.flight_hours
        hi_new = np.clip(1.0 - state["wear_mm"] / state["limit_mm"], 0.0, 1.0)
        state["hi"] = np.minimum(state["hi"], hi_new)
        state["failed"] = state["failed"] | (state["wear_mm"] >= state["limit_mm"])

    def emit(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> dict[str, np.ndarray]:
        p = PARAMS
        n = ctx.n
        z_pin, z_t, z_p = rng.standard_normal(n), rng.standard_normal(n), rng.standard_normal(n)
        sd = np.where(state["inspected"], p["PIN_SD_INSPECT_MM"], p["PIN_SD_MM"])
        press = p["P_BRAKE_KPA"] * np.sqrt(np.maximum(state["e_app"], 0.0) / p["E_REF_MJ"] / np.maximum(state["stress_ext"], 1e-6))
        return {
            "pad_wear_mm": np.maximum(state["wear_mm"] + sd * z_pin, 0.0),
            "disc_temp_c": state["temp_c"] + p["TEMP_SD_C"] * z_t,
            "brake_pressure_kpa": press + p["P_SD_KPA"] * z_p,
            "brake_apps_cycle": ctx.brake_applications.astype(float),
        }

    def health_index(self, state: State) -> np.ndarray:
        return state["hi"]

    def maintain(self, state: State, idx: np.ndarray, action: str, q: float, rng: np.random.Generator) -> None:
        if action not in ("inspect", "repair", "overhaul", "replace"):
            raise ValueError(f"unknown action: {action}")
        if idx.size == 0:
            return
        if action == "inspect":
            state["inspected"][idx] = True  # next emit reads the pin at inspection resolution; no state change
            return
        if action in ("repair", "overhaul"):
            f = q if action == "repair" else PARAMS["OVERHAUL_FRAC"] * q
            for key in ("wear_mm", "ox_mm", "age_virtual"):
                state[key][idx] *= f
        else:
            fresh = _sample(idx.size, rng)
            for key, val in fresh.items():
                state[key][idx] = val
            for key in ("wear_mm", "ox_mm", "rto_count", "age_cycles", "age_hours", "age_virtual"):
                state[key][idx] = 0.0
            state["temp_c"][idx] = 20.0
        state["failed"][idx] = state["wear_mm"][idx] >= state["limit_mm"][idx]
        state["hi"][idx] = np.clip(1.0 - state["wear_mm"][idx] / state["limit_mm"][idx], 0.0, 1.0)
