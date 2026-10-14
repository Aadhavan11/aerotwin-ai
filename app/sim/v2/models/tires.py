"""Main-gear tire model (v2).

Latent state: tread loss (mm), cold inflation pressure (psi at 15 C), cut damage, retread count.
Tread is lost per landing (weight, sink rate, wet runway) plus per-km taxi scrub, amplified by under-inflation.
Pressure leaks per calendar day with temperature dependence; FOD/cut shocks are a Poisson process that add a
step pressure loss, extra leak and a failure hazard. maintain('inspect') tops up with nitrogen,
maintain('overhaul') is a retread limited to RETREAD_MAX.

Every constant in PARAMS is a assumed (no citation): nothing here is calibrated against fleet data.
"""

from __future__ import annotations

import numpy as np

from app.sim.v2.core import State, register
from app.sim.v2.drivers import OperatingContext
from app.sim.v2.faults import fault_log_loss, fault_mult

# All values: assumed (no citation).
PARAMS: dict[str, float] = {
    "TREAD_NEW_MM": 9.0,        # new tread groove depth, mm (matches v1 healthy); assumed (no citation)
    "TREAD_LIMIT_MM": 1.6,      # mean minimum groove depth at removal, mm (matches v1 limit); assumed (no citation)
    "TREAD_LIMIT_SD_MM": 0.1,   # per-unit spread of removal depth, mm; assumed (no citation)
    "K_LAND_MM": 0.019,         # tread loss per landing at reference weight and sink rate, mm; assumed (no citation)
    "K_LAND_SIGMA": 0.2,        # lognormal sigma of per-unit wear coefficient (compound/spin-up spread); assumed (no citation)
    "W_REF": 0.85,              # reference landing weight fraction; assumed (no citation)
    "W_EXP": 1.5,               # exponent of wear on landing weight; assumed (no citation)
    "SINK_REF_FPS": 3.0,        # reference sink rate, ft/s; assumed (no citation)
    "SINK_EXP": 0.8,            # exponent of wear on sink rate (spin-up scrub); assumed (no citation)
    "WET_FACTOR": 1.2,          # wear multiplier on wet runways (hydroplane spin-up scrub, locked-wheel skids); assumed (no citation)
    "K_TAXI_MM_KM": 0.0009,     # scrub loss per taxi km, mm; assumed (no citation)
    "NOISE_SIGMA": 0.15,        # lognormal sigma of cycle-to-cycle wear scatter; assumed (no citation)
    "P_SET_PSI": 200.0,         # cold nominal inflation pressure, psi; assumed (no citation)
    "P_LOW_FRAC": 0.75,         # cold pressure fraction below which the tire is removed (flat/under-inflated); assumed (no citation)
    "UNDERINFL_WEAR": 3.0,      # extra wear multiplier slope per unit fractional pressure deficit; assumed (no citation)
    "LEAK_FRAC_DAY": 0.0008,    # permeation loss as fraction of cold pressure per calendar day at 15 C; assumed (no citation)
    "LEAK_SIGMA": 0.35,         # lognormal sigma of per-unit leak rate (valve/bead seat quality); assumed (no citation)
    "LEAK_TEMP_COEF": 0.03,     # fractional leak growth per C above 15 C; assumed (no citation)
    "RETREAD_LEAK_GROWTH": 0.15,  # leak multiplier growth per retread (carcass ageing); assumed (no citation)
    "RETREAD_MAX": 5.0,         # maximum number of retreads before the carcass is condemned; assumed (no citation)
    "FOD_RATE": 1.0 / 1500.0,   # cut/FOD events per landing at dust index 0; assumed (no citation)
    "FOD_DUST_GAIN": 3.0,       # FOD rate multiplier slope per unit dust index; assumed (no citation)
    "CUT_DMG_MIN": 0.25,        # minimum cut damage increment per event (1.0 = tire removed); assumed (no citation)
    "CUT_DMG_MAX": 0.6,         # maximum cut damage increment per event; assumed (no citation)
    "CUT_P_LOSS_MIN": 0.02,     # minimum step pressure loss fraction per cut; assumed (no citation)
    "CUT_P_LOSS_MAX": 0.15,     # maximum step pressure loss fraction per cut; assumed (no citation)
    "CUT_LEAK_ADD": 0.004,      # added daily leak fraction per cut; assumed (no citation)
    "CUT_WEAR_MM": 0.3,         # extra tread loss per cut, mm; assumed (no citation)
    "TIRE_HEAT_C": 25.0,        # tire temperature rise over ambient after landing and taxi, C; assumed (no citation)
    "TIRE_HEAT_ENERGY_C": 0.5,  # additional rise per MJ of brake energy (conducted heat), C; assumed (no citation)
    "TREAD_SD_MM": 0.25,        # tread reading noise between inspections, mm; assumed (no citation)
    "TREAD_SD_INSPECT_MM": 0.05,  # tread reading noise at inspection (depth gauge resolution), mm; assumed (no citation)
    "PRESS_SD_PSI": 0.5,        # pressure sensor noise, psi; assumed (no citation)
    "TEMP_SD_C": 3.0,           # tire temperature sensor noise, C; assumed (no citation)
    "AGE_COEF": 0.1,            # leak growth with virtual age (rubber permeability ageing); assumed (no citation)
    "AGE_REF_CYCLES": 300.0,    # virtual age scale for AGE_COEF, cycles; assumed (no citation)
    "REPAIR_TREAD_FRAC": 0.5,   # fraction of tread loss retained by repair (times q); assumed (no citation)
}


def _sample(n: int, rng: np.random.Generator) -> dict[str, np.ndarray]:
    p = PARAMS
    return {
        "k_land": p["K_LAND_MM"] * rng.lognormal(0.0, p["K_LAND_SIGMA"], n),
        "leak_frac": p["LEAK_FRAC_DAY"] * rng.lognormal(0.0, p["LEAK_SIGMA"], n),
        "limit_mm": np.maximum(p["TREAD_LIMIT_MM"] + p["TREAD_LIMIT_SD_MM"] * rng.standard_normal(n), 1.0),
    }


def _hi(state: State, idx: np.ndarray | slice) -> np.ndarray:
    p = PARAMS
    span = p["TREAD_NEW_MM"] - state["limit_mm"][idx]
    h_tread = (span - state["tread_loss_mm"][idx]) / span
    h_p = (state["p_cold"][idx] / p["P_SET_PSI"] - p["P_LOW_FRAC"]) / (1.0 - p["P_LOW_FRAC"])
    h_cut = 1.0 - state["cut_dmg"][idx]
    return np.clip(np.minimum(np.minimum(h_tread, h_p), h_cut), 0.0, 1.0)


@register("tires")
class TiresModel:
    kind = "tires"
    channels = ("tread_depth_mm", "tire_pressure_psi", "tire_temp_c")

    def init(self, n: int, rng: np.random.Generator) -> State:
        st: State = _sample(n, rng)
        st.update(
            tread_loss_mm=np.zeros(n), p_cold=np.full(n, PARAMS["P_SET_PSI"]), cut_dmg=np.zeros(n), cut_leak=np.zeros(n),
            retreads=np.zeros(n), cuts=np.zeros(n), n2_topups=np.zeros(n), condemned=np.zeros(n, dtype=bool),
            last_cal_days=np.zeros(n), tire_temp=np.full(n, 15.0), stress_ext=np.ones(n), inspected=np.zeros(n, dtype=bool),
            age_cycles=np.zeros(n), age_hours=np.zeros(n), age_virtual=np.zeros(n), hi=np.ones(n), failed=np.zeros(n, dtype=bool),
        )
        return st

    def step(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> None:
        p = PARAMS
        n = ctx.n
        z_wear, u_fod, u_dmg, u_ploss = rng.standard_normal(n), rng.random(n), rng.random(n), rng.random(n)
        # Foreign-object / cut shocks.
        rate = p["FOD_RATE"] * (1.0 + p["FOD_DUST_GAIN"] * ctx.dust_index)
        cut = u_fod < rate
        state["cuts"] += cut
        state["cut_dmg"] += np.where(cut, p["CUT_DMG_MIN"] + (p["CUT_DMG_MAX"] - p["CUT_DMG_MIN"]) * u_dmg, 0.0)
        state["p_cold"] *= 1.0 - np.where(cut, p["CUT_P_LOSS_MIN"] + (p["CUT_P_LOSS_MAX"] - p["CUT_P_LOSS_MIN"]) * u_ploss, 0.0)
        state["cut_leak"] += np.where(cut, p["CUT_LEAK_ADD"], 0.0)
        # Tread loss per landing + taxi scrub, amplified by under-inflation.
        wet = np.where(ctx.wet_runway, p["WET_FACTOR"], 1.0)
        land = state["k_land"] * (ctx.landing_weight_frac / p["W_REF"]) ** p["W_EXP"] * (np.maximum(ctx.sink_rate_fps, 0.1) / p["SINK_REF_FPS"]) ** p["SINK_EXP"]
        underinfl = 1.0 + p["UNDERINFL_WEAR"] * np.maximum(0.0, 1.0 - state["p_cold"] / p["P_SET_PSI"])
        scatter = np.exp(p["NOISE_SIGMA"] * z_wear - 0.5 * p["NOISE_SIGMA"] ** 2)
        loss = (land * wet + p["K_TAXI_MM_KM"] * ctx.taxi_distance_km) * underinfl * state["stress_ext"] * scatter * fault_mult(state, "fod_cut")
        state["tread_loss_mm"] += loss + np.where(cut, p["CUT_WEAR_MM"], 0.0)
        # Pressure leak per calendar day.
        dt_days = np.clip(ctx.calendar_days - state["last_cal_days"], 0.0, 30.0)
        state["last_cal_days"] = ctx.calendar_days.astype(float).copy()
        age_f = 1.0 + p["AGE_COEF"] * state["age_virtual"] / p["AGE_REF_CYCLES"]
        temp_f = np.exp(p["LEAK_TEMP_COEF"] * (ctx.oat_c - 15.0))
        daily = (state["leak_frac"] * temp_f * (1.0 + p["RETREAD_LEAK_GROWTH"] * state["retreads"]) * age_f) + state["cut_leak"] + fault_log_loss(state, "slow_leak") / 30.0
        state["p_cold"] *= np.exp(-daily * dt_days)
        state["tire_temp"] = ctx.oat_c + p["TIRE_HEAT_C"] + p["TIRE_HEAT_ENERGY_C"] * ctx.brake_energy_mj
        state["inspected"] = np.zeros(n, dtype=bool)
        state["age_cycles"] += 1
        state["age_virtual"] += 1
        state["age_hours"] += ctx.flight_hours
        sl = slice(None)
        state["hi"] = np.minimum(state["hi"], _hi(state, sl))
        remaining = p["TREAD_NEW_MM"] - state["tread_loss_mm"]
        flat = state["p_cold"] < p["P_LOW_FRAC"] * p["P_SET_PSI"]
        state["failed"] = state["failed"] | (remaining <= state["limit_mm"]) | flat | (state["cut_dmg"] >= 1.0)

    def emit(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> dict[str, np.ndarray]:
        p = PARAMS
        n = ctx.n
        z_tread, z_p, z_t = rng.standard_normal(n), rng.standard_normal(n), rng.standard_normal(n)
        sd = np.where(state["inspected"], p["TREAD_SD_INSPECT_MM"], p["TREAD_SD_MM"])
        tread = p["TREAD_NEW_MM"] - state["tread_loss_mm"]
        hot_p = state["p_cold"] * (273.15 + state["tire_temp"]) / 288.15  # gas law: hot pressure at tire temperature
        return {
            "tread_depth_mm": np.maximum(tread + sd * z_tread, 0.0),
            "tire_pressure_psi": np.maximum(hot_p + p["PRESS_SD_PSI"] * z_p, 0.0),
            "tire_temp_c": state["tire_temp"] + p["TEMP_SD_C"] * z_t,
        }

    def health_index(self, state: State) -> np.ndarray:
        return state["hi"]

    def maintain(self, state: State, idx: np.ndarray, action: str, q: float, rng: np.random.Generator) -> None:
        p = PARAMS
        if action not in ("inspect", "repair", "overhaul", "replace"):
            raise ValueError(f"unknown action: {action}")
        if idx.size == 0:
            return
        if action == "inspect":
            state["inspected"][idx] = True
            state["p_cold"][idx] = p["P_SET_PSI"]  # nitrogen top-up to nominal cold pressure
            state["n2_topups"][idx] += 1
        elif action == "repair":
            state["tread_loss_mm"][idx] *= p["REPAIR_TREAD_FRAC"] * q + (1.0 - p["REPAIR_TREAD_FRAC"])  # spot repair of damaged area only
            state["cut_dmg"][idx] *= q
            state["cut_leak"][idx] *= q
            state["age_virtual"][idx] *= q
        elif action == "overhaul":  # retread
            ok = state["retreads"][idx] < p["RETREAD_MAX"]
            good, bad = idx[ok], idx[~ok]
            state["tread_loss_mm"][good] = 0.0
            state["cut_dmg"][good] = 0.0
            state["cut_leak"][good] = 0.0
            state["p_cold"][good] = p["P_SET_PSI"]
            state["retreads"][good] += 1
            state["age_virtual"][good] *= 0.25 * q
            state["condemned"][bad] = True  # carcass beyond retread limit: only replace helps
            state["failed"][bad] = True
        else:
            fresh = _sample(idx.size, rng)
            for key, val in fresh.items():
                state[key][idx] = val
            for key in ("tread_loss_mm", "cut_dmg", "cut_leak", "retreads", "cuts", "n2_topups", "age_cycles", "age_hours", "age_virtual"):
                state[key][idx] = 0.0
            state["p_cold"][idx] = p["P_SET_PSI"]
            state["condemned"][idx] = False
        remaining = p["TREAD_NEW_MM"] - state["tread_loss_mm"][idx]
        flat = state["p_cold"][idx] < p["P_LOW_FRAC"] * p["P_SET_PSI"]
        state["failed"][idx] = (remaining <= state["limit_mm"][idx]) | flat | (state["cut_dmg"][idx] >= 1.0) | state["condemned"][idx]
        state["hi"][idx] = np.where(state["condemned"][idx], 0.0, _hi(state, idx))
