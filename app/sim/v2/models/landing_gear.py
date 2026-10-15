"""Main landing-gear strut model (v2).

Latent state: strut-seal pressure loss (gamma process), Miner fatigue damage from a sink-rate load spectrum and a
latent Paris-law crack length that is only observable through maintain('inspect') with a probability-of-detection
curve vs crack size. A hard-landing flag (effective sink rate above a threshold) makes an inspection required.

On-wing servicing (assumed, no citation): strut-seal pressure loss and hard-landing leak increments are corrected on the
aircraft by recharging the strut (nitrogen / fluid servicing) at the next inspection, so they do NOT cause a removal; the
strut is removed only for a severe finding (fatigue damage, crack growth, an overload landing from the fault library's
``hard_landing_damage`` mode, or a leak so fast that the unit reaches its pressure limit before the next inspection). Before this, a leak-driven
finding was handled as a replacement, giving ~15 unscheduled gear removals per 24 tails per year (one per ~6k FH)
although real gear removals are mostly scheduled overhauls ~10 years apart. Servicing is a physical model feature
(``SERVICE_LEAK_KPA``), not a policy threshold.

Every constant in PARAMS is an assumed prior (no citation): nothing here is calibrated against fleet data.
"""

from __future__ import annotations

import numpy as np

from app.sim.v2.core import State, register
from app.sim.v2.drivers import OperatingContext
from app.sim.v2.faults import fault_add, fault_rate

# All values: assumed (no citation).
PARAMS: dict[str, float] = {
    "P_NOM_KPA": 1800.0,        # nominal servicing strut pressure, kPa (matches v1 healthy); assumed (no citation)
    "P_LIMIT_KPA": 1200.0,      # strut pressure at which the strut is out of limits, kPa (matches v1 limit); assumed (no citation)
    "P_LIMIT_SD_KPA": 30.0,     # per-unit spread of the pressure limit, kPa; assumed (no citation)
    "LEAK_SHAPE": 0.5,          # gamma shape per cycle of the seal-leak increment; assumed (no citation)
    "LEAK_SCALE_KPA": 0.15,     # gamma scale of the leak increment, kPa (mean 0.075 kPa/cycle -> ~8000 cycle life); assumed (no citation)
    "LEAK_SIGMA": 0.3,          # lognormal sigma of per-unit seal quality; assumed (no citation)
    "LEAK_COLD_COEF": 0.01,     # fractional leak growth per C below 15 C (seal stiffening); assumed (no citation)
    "AGE_COEF": 0.5,            # leak growth with virtual age (seal ageing); assumed (no citation)
    "AGE_REF_CYCLES": 8000.0,   # virtual age scale for AGE_COEF, cycles; assumed (no citation)
    "HARD_LEAK_KPA": 0.5,       # extra pressure loss from a hard landing, kPa; assumed (no citation)
    "G_BASE": 1.0,              # static load factor at touchdown; assumed (no citation)
    "G_PER_FPS": 0.17,          # added load factor per ft/s of sink rate at reference weight; assumed (no citation)
    "W_REF": 0.85,              # reference landing weight fraction; assumed (no citation)
    "SINK_NOISE_SIGMA": 0.12,   # lognormal sigma of effective sink rate (gusts, flare technique); assumed (no citation)
    "HARD_SINK_FPS": 4.6,       # hard-landing threshold, ft/s (scaled to the profile generator's 2-4 ft/s range); assumed (no citation)
    "G_REF": 1.5,               # reference load factor of the fatigue spectrum; assumed (no citation)
    "S_N_EXPONENT": 5.0,        # S-N curve slope for Miner damage; assumed (no citation)
    "N_REF": 40000.0,           # cycles to failure at the reference load factor; assumed (no citation)
    "FATIGUE_SIGMA": 0.25,      # lognormal sigma of per-unit fatigue strength; assumed (no citation)
    "CRACK_A0_MM": 0.5,         # median initial flaw size, mm; assumed (no citation)
    "CRACK_A0_SIGMA": 0.3,      # lognormal sigma of initial flaw size; assumed (no citation)
    "CRACK_A_CRIT_MM": 25.0,    # critical crack length, mm; assumed (no citation)
    "PARIS_C": 8.1e-5,          # Paris coefficient in mm/cycle for a^1.5 growth at reference load (m=3 -> ~30000 cycles from a0); assumed (no citation)
    "PARIS_SIGMA": 0.3,         # lognormal sigma of per-unit Paris coefficient (material scatter); assumed (no citation)
    "PARIS_M": 3.0,             # Paris exponent on stress intensity range; assumed (no citation)
    "POD_A50_MM": 3.0,          # crack length detected with 50% probability at inspection, mm; assumed (no citation)
    "POD_SLOPE": 0.4,           # log-logistic POD slope parameter in ln(mm); assumed (no citation)
    "PRESS_SD_KPA": 5.0,        # strut pressure sensor noise, kPa; assumed (no citation)
    "G_SD": 0.03,               # shock load sensor noise, g; assumed (no citation)
    "EXT_NOM_MM": 400.0,        # static strut extension at nominal pressure, mm; assumed (no citation)
    "EXT_PER_KPA": 0.15,        # extension lost per kPa of pressure loss, mm; assumed (no citation)
    "EXT_SD_MM": 1.5,           # extension sensor noise, mm; assumed (no citation)
    "SERVICE_LEAK_KPA": 60.0,   # pressure loss at which an inspection servicing recharges the strut on wing, kPa (~3% of nominal); assumed (no citation)
    "OVERHAUL_FRAC": 0.3,       # fatigue damage fraction kept by overhaul (times q); assumed (no citation)
}


def _sample(n: int, rng: np.random.Generator) -> dict[str, np.ndarray]:
    p = PARAMS
    return {
        "leak_scale": p["LEAK_SCALE_KPA"] * rng.lognormal(0.0, p["LEAK_SIGMA"], n),
        "limit_kpa": p["P_LIMIT_KPA"] + p["P_LIMIT_SD_KPA"] * rng.standard_normal(n),
        "fat_strength": rng.lognormal(0.0, p["FATIGUE_SIGMA"], n),
        "paris_c": p["PARIS_C"] * rng.lognormal(0.0, p["PARIS_SIGMA"], n),
        "crack_a0": p["CRACK_A0_MM"] * rng.lognormal(0.0, p["CRACK_A0_SIGMA"], n),
    }


def _hi(state: State, idx: np.ndarray | slice) -> np.ndarray:
    p = PARAMS
    h_leak = (p["P_NOM_KPA"] - state["leak_kpa"][idx] - state["limit_kpa"][idx]) / (p["P_NOM_KPA"] - state["limit_kpa"][idx])
    h_fat = 1.0 - state["fatigue"][idx] - fault_add(state, "hard_landing_damage", idx)
    h_crack = 1.0 - state["crack_mm"][idx] / p["CRACK_A_CRIT_MM"]
    return np.clip(np.minimum(np.minimum(h_leak, h_fat), h_crack), 0.0, 1.0)


@register("landing_gear")
class LandingGearModel:
    kind = "landing_gear"
    channels = ("strut_pressure_kpa", "shock_load_g", "strut_extension_mm", "hard_landing_flag")

    def init(self, n: int, rng: np.random.Generator) -> State:
        st: State = _sample(n, rng)
        st["crack_mm"] = st["crack_a0"].copy()
        st.update(
            leak_kpa=np.zeros(n), fatigue=np.zeros(n), hard_count=np.zeros(n), n_serviced=np.zeros(n), inspection_due=np.zeros(n, dtype=bool),
            crack_detected=np.zeros(n, dtype=bool), last_g=np.full(n, PARAMS["G_BASE"]), last_hard=np.zeros(n, dtype=bool),
            stress_ext=np.ones(n),
            age_cycles=np.zeros(n), age_hours=np.zeros(n), age_virtual=np.zeros(n), hi=np.ones(n), failed=np.zeros(n, dtype=bool),
        )
        return st

    def step(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> None:
        p = PARAMS
        n = ctx.n
        z_sink = rng.standard_normal(n)
        gam = rng.gamma(p["LEAK_SHAPE"], 1.0, n)
        sink = ctx.sink_rate_fps * np.exp(p["SINK_NOISE_SIGMA"] * z_sink)
        hard = sink > p["HARD_SINK_FPS"]
        g = p["G_BASE"] + p["G_PER_FPS"] * sink * np.sqrt(ctx.landing_weight_frac / p["W_REF"])
        # Strut-seal leak: gamma process, faster when cold and with seal age.
        age_f = 1.0 + p["AGE_COEF"] * state["age_virtual"] / p["AGE_REF_CYCLES"]
        cold_f = 1.0 + p["LEAK_COLD_COEF"] * np.maximum(0.0, 15.0 - ctx.oat_c)
        state["leak_kpa"] += gam * state["leak_scale"] * age_f * cold_f * state["stress_ext"] * fault_rate(state, "strut_seal_leak") + np.where(hard, p["HARD_LEAK_KPA"], 0.0)
        # Miner damage from the load spectrum (S-N power law).
        state["fatigue"] += (g / p["G_REF"]) ** p["S_N_EXPONENT"] / (p["N_REF"] * state["fat_strength"]) * state["stress_ext"]
        # Paris-law growth (m = PARIS_M) with load-dependent stress range.
        a = state["crack_mm"]
        grown = a + state["paris_c"] * (g / p["G_REF"]) ** p["PARIS_M"] * a ** (p["PARIS_M"] / 2.0) * state["stress_ext"]
        state["crack_mm"] = np.minimum(grown, 2.0 * p["CRACK_A_CRIT_MM"])  # fractured part: no need to integrate further
        state["hard_count"] += hard
        state["inspection_due"] = state["inspection_due"] | hard
        state["last_g"] = g
        state["last_hard"] = hard
        state["age_cycles"] += 1
        state["age_virtual"] += 1
        state["age_hours"] += ctx.flight_hours
        state["hi"] = np.minimum(state["hi"], _hi(state, slice(None)))
        out = (p["P_NOM_KPA"] - state["leak_kpa"] <= state["limit_kpa"]) | (state["fatigue"] >= 1.0) | (state["crack_mm"] >= p["CRACK_A_CRIT_MM"])
        state["failed"] = state["failed"] | out

    def emit(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> dict[str, np.ndarray]:
        p = PARAMS
        n = ctx.n
        z_p, z_g, z_e = rng.standard_normal(n), rng.standard_normal(n), rng.standard_normal(n)
        pressure = p["P_NOM_KPA"] - state["leak_kpa"]
        ext = p["EXT_NOM_MM"] - p["EXT_PER_KPA"] * state["leak_kpa"] - 30.0 * (ctx.landing_weight_frac - p["W_REF"])
        return {
            "strut_pressure_kpa": pressure + p["PRESS_SD_KPA"] * z_p,
            "shock_load_g": np.maximum(state["last_g"] + p["G_SD"] * z_g, 0.0),
            "strut_extension_mm": ext + p["EXT_SD_MM"] * z_e,
            "hard_landing_flag": state["last_hard"].astype(float),
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
            # POD curve vs latent crack size: log-logistic in ln(a).
            u = rng.random(idx.size)
            a = np.maximum(state["crack_mm"][idx], 1e-6)
            pod = 1.0 / (1.0 + np.exp(-(np.log(a) - np.log(p["POD_A50_MM"])) / p["POD_SLOPE"]))
            state["crack_detected"][idx] = state["crack_detected"][idx] | (u < pod)
            state["inspection_due"][idx] = False
            # on-wing servicing: recharge a strut that has lost pressure (seal wear itself, age_virtual, is not reset)
            svc = idx[state["leak_kpa"][idx] >= p["SERVICE_LEAK_KPA"]]
            if svc.size and not state["failed"][svc].all():
                svc = svc[~state["failed"][svc]]   # an out-of-limit strut has already failed: it is removed, not serviced
                state["leak_kpa"][svc] = 0.0
                state["n_serviced"][svc] += 1.0
                state["hi"][svc] = _hi(state, svc)
            return
        if action == "repair":
            state["leak_kpa"][idx] *= q
            state["fatigue"][idx] *= 0.5 + 0.5 * q
            a0 = state["crack_a0"][idx]
            state["crack_mm"][idx] = a0 + (state["crack_mm"][idx] - a0) * q  # blend-out of detected crack
            state["age_virtual"][idx] *= q
            state["crack_detected"][idx] = state["crack_detected"][idx] & (state["crack_mm"][idx] > 2.0 * a0)
        elif action == "overhaul":
            f = p["OVERHAUL_FRAC"] * q
            state["leak_kpa"][idx] = 0.0  # new seal pack
            state["fatigue"][idx] *= f
            state["crack_mm"][idx] = state["crack_a0"][idx]
            state["age_virtual"][idx] *= f
            state["crack_detected"][idx] = False
        else:
            fresh = _sample(idx.size, rng)
            for key, val in fresh.items():
                state[key][idx] = val
            state["crack_mm"][idx] = fresh["crack_a0"]
            for key in ("leak_kpa", "fatigue", "hard_count", "n_serviced", "age_cycles", "age_hours", "age_virtual"):
                state[key][idx] = 0.0
            state["crack_detected"][idx] = False
            state["inspection_due"][idx] = False
        out = (p["P_NOM_KPA"] - state["leak_kpa"][idx] <= state["limit_kpa"][idx]) | (state["fatigue"][idx] >= 1.0) | (state["crack_mm"][idx] >= p["CRACK_A_CRIT_MM"])
        state["failed"][idx] = out
        state["hi"][idx] = _hi(state, idx)
