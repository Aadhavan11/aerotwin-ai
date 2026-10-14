"""Turbofan engine surrogate calibrated on NASA C-MAPSS FD001-FD004 (see docs/SIM-V2-ENGINE.md).

A latent wear ``progress`` p in [0, 1] (1 = end of life) advances at 1/life per cycle times context
accelerators (hot OAT, density altitude, low derate, dust, ``stress_ext``); the late-life nonlinearity lives
in the fitted sensor curves f(p), so sensors are on the same time axis as the data. The 14 C-MAPSS
sensors are ``mu_c + sd_c * (poly(p) @ coef_i + sigma * noise)`` with the polynomial, per-engine coefficient
distribution, noise and operating-condition baselines fitted from the data (``app.calib.engine``).
Acute faults (``fault_hpt_blade_erosion``, ``fault_compressor_fouling``) and module deficits sit on top using
prior (non-identified) loadings. Module states, EGT margin and LLP are physics-informed surrogate coordinates.
"""

from __future__ import annotations

import functools
from typing import Any

import numpy as np

from app.calib.engine import basis, load_fit
from app.ml.sensors import SENSOR_NAMES
from app.sim.v2.core import State, register
from app.sim.v2.drivers import OperatingContext

PARAMS = {
    "jitter_sigma": 0.08,  # prior: per-cycle lognormal wear jitter
    "hot_per_c": 0.012,  # prior: +1.2% wear per degC above reference OAT (capped)
    "hot_ref_c": 15.0,
    "hot_cap": 0.4,
    "da_per_kft": 0.015,  # prior: +1.5% wear per 1000 ft density altitude
    "derate_gain": 1.5,  # prior: full-thrust takeoffs wear faster than derated ones
    "derate_ref": 0.075,
    "dust_gain": 0.5,  # prior
    "accel_ref": 1.16,  # mean accelerator under v1 flight profiles; life scale is normalised so mean life matches the fit
    "accel_min": 0.5,
    "accel_max": 3.0,
    "egt_margin_mean_c": 70.0,  # prior: new-engine EGT margin
    "egt_margin_sd_c": 10.0,
    "egt_hot_c_per_c": 0.5,  # prior: margin lost per degC OAT above reference
    "egt_fault_c": 25.0,  # prior: extra margin loss at full HPT erosion severity
    "llp_limit_cycles": 20000.0,  # prior: life-limited part cycle limit
    "llp_initial_frac_max": 0.5,
    "llp_derate_gain": 0.1,  # prior: full-thrust takeoff counts ~10% more LLP cycle
    "fault_progress_hpt": 0.5,  # prior: fraction of life consumed at full erosion severity
    "fault_progress_foul": 0.15,
    "hpc_eff_loss_pct": 4.0,  # prior: HPC efficiency points lost at end of life
    "module_eff_loss_pct": 3.0,  # prior: other modules, efficiency points at full deficit
    "repair_keep": 0.5,  # repair keeps repair_keep + (1 - repair_keep) * q of progress
    "overhaul_keep": 0.6,  # shop visit keeps overhaul_keep * q of progress
}
MODULES = ("fan", "lpc", "hpc", "hpt", "lpt")
EXTRA_CHANNELS = ("egt_margin_c", "llp_cycles_remaining", "hpc_eff_pct")
CHANNELS = (*SENSOR_NAMES, *EXTRA_CHANNELS)
# prior module->sensor masks (not identifiable from C-MAPSS): which sensors a module deficit moves
_MASKS = {
    "hpt": {"T50": 1.0, "T30": 0.3, "phi": 0.8, "W31": 0.8, "htBleed": 0.3, "P30": 0.3, "Ps30": 0.3},
    "foul": {"T30": 1.0, "P30": 1.0, "Ps30": 1.0, "Nc": 0.8, "phi": 1.0, "NRc": 0.8, "htBleed": 0.5, "T50": 0.4},
}
_SHARES = {"fd001": (0.05, 0.05, 0.85, 0.03, 0.02), "fd002": (0.05, 0.05, 0.85, 0.03, 0.02), "fd003": (0.4, 0.05, 0.5, 0.03, 0.02), "fd004": (0.4, 0.05, 0.5, 0.03, 0.02)}
VARIANT_NAMES = ("fd001", "fd002", "fd003", "fd004")


class _Fit:
    def __init__(self, fit: dict[str, Any]) -> None:
        vs = [fit["variants"][v] for v in VARIANT_NAMES]

        def arr(key: str) -> np.ndarray:
            return np.asarray([v[key] for v in vs], dtype=float)

        self.mu, self.sd = arr("mu"), arr("sd")  # (V, 6, S)
        self.coef_mean, self.coef_diag = arr("coef_mean"), arr("coef_diag")  # (V, S, nb)
        self.factors = [np.asarray(v["coef_factors"], dtype=float) for v in vs]  # list of (k, S, nb)
        self.noise = arr("noise_sigma")  # (V, S)
        self.res = arr("resolution")  # (V, S) sensor quantisation step
        self.freq = arr("cond_freq")  # (V, 6)
        self.n_cond = np.asarray([v["n_conditions"] for v in vs])
        self.log_mu, self.log_sigma = arr("life_log_mu"), arr("life_log_sigma")
        self.t50_rise = arr("t50_rise_degR")
        # end-of-life z shift of the fd001 mean curve: reused as prior fault loading directions
        eol = (basis(1.0) - basis(0.0)) @ self.coef_mean[0].T  # (S,)
        self.eol = np.asarray(eol).reshape(-1)
        self.load_hpt = self.eol * np.array([_MASKS["hpt"].get(s, 0.0) for s in SENSOR_NAMES])
        self.load_foul = self.eol * np.array([_MASKS["foul"].get(s, 0.0) for s in SENSOR_NAMES])


@functools.lru_cache(maxsize=1)
def _fit() -> _Fit:
    return _Fit(load_fit())


def _sample(f: _Fit, variant: np.ndarray, rng: np.random.Generator) -> dict[str, np.ndarray]:
    n = variant.size
    life = np.exp(f.log_mu[variant] + f.log_sigma[variant] * rng.standard_normal(n))
    s, nb = f.coef_mean.shape[1:]
    coef = np.empty((n, s, nb))
    for v in np.unique(variant):
        m = variant == v
        k = int(m.sum())
        g = rng.standard_normal((k, f.factors[v].shape[0]))
        e = rng.standard_normal((k, s, nb))
        coef[m] = f.coef_mean[v] + np.einsum("nk,ksb->nsb", g, f.factors[v]) + f.coef_diag[v] * e
    shares = np.stack([np.asarray(_SHARES[VARIANT_NAMES[v]]) for v in variant])
    shares = shares * np.exp(0.2 * rng.standard_normal(shares.shape))
    shares = shares / shares.sum(1, keepdims=True)
    return {
        "life_scale": life,
        "coef": coef,
        "shares": shares,
        "egt_margin0": np.maximum(PARAMS["egt_margin_mean_c"] + PARAMS["egt_margin_sd_c"] * rng.standard_normal(n), 15.0),
    }


@register("engine")
class EngineModel:
    kind = "engine"
    channels = CHANNELS

    def init(self, n: int, rng: np.random.Generator, variant: str = "fd001") -> State:
        f = _fit()
        var = np.full(n, VARIANT_NAMES.index(variant), dtype=np.int64)
        st: State = _sample(f, var, rng)
        lim = PARAMS["llp_limit_cycles"]
        st.update(
            variant=var,
            progress=np.zeros(n),
            fault_peak=np.zeros(n),
            fault_hpt_peak=np.zeros(n),
            fault_foul_peak=np.zeros(n),
            llp_limit=np.full(n, lim),
            llp_used=rng.uniform(0.0, PARAMS["llp_initial_frac_max"], n) * lim,
            stress_ext=np.ones(n),
            inspected=np.zeros(n, dtype=bool),
            age_cycles=np.zeros(n),
            age_hours=np.zeros(n),
            age_virtual=np.zeros(n),
            hi=np.ones(n),
            failed=np.zeros(n, dtype=bool),
        )
        for m in MODULES:
            st[f"def_{m}"] = np.zeros(n)
        return st

    @staticmethod
    def set_variant(state: State, idx: Any, variant: str, rng: np.random.Generator) -> None:
        """Switch units to a fit variant (fd001..fd004) and resample their latent parameters."""
        idx = np.atleast_1d(np.asarray(idx, dtype=np.int64))
        var = np.full(idx.size, VARIANT_NAMES.index(variant), dtype=np.int64)
        new = _sample(_fit(), var, rng)
        state["variant"][idx] = var
        for k, v in new.items():
            state[k][idx] = v

    # ---- helpers ----
    @staticmethod
    def _fault(state: State, mode: str, n: int) -> np.ndarray:
        a = state.get(f"fault_{mode}")
        return np.clip(np.asarray(a, dtype=float), 0.0, 1.0) if a is not None else np.zeros(n)

    @staticmethod
    def _update(state: State) -> None:
        p = np.clip(state["progress"], 0.0, 1.0)
        total = np.clip(p + state["fault_peak"], 0.0, 1.0)
        llp_out = state["llp_used"] >= state["llp_limit"]
        state["hi"] = np.where(llp_out, 0.0, 1.0 - total)
        state["failed"] = state["failed"] | (total >= 1.0) | llp_out
        sh = state["shares"]
        for i, m in enumerate(MODULES):
            extra = state["fault_hpt_peak"] if m == "hpt" else state["fault_foul_peak"] if m == "hpc" else 0.0
            state[f"def_{m}"] = np.clip(sh[:, i] * p * 1.0 + extra, 0.0, 1.0)

    def step(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> None:
        n = ctx.n
        z = rng.standard_normal(n)
        hot = np.clip(PARAMS["hot_per_c"] * (ctx.oat_c - PARAMS["hot_ref_c"]), -0.1, PARAMS["hot_cap"])
        da = PARAMS["da_per_kft"] * np.maximum(ctx.density_alt_ft, 0.0) / 1000.0
        derate = PARAMS["derate_gain"] * (PARAMS["derate_ref"] - ctx.thrust_derate)
        accel = np.clip(1.0 + hot + da + derate + PARAMS["dust_gain"] * ctx.dust_index, PARAMS["accel_min"], PARAMS["accel_max"])
        sj = PARAMS["jitter_sigma"]
        jitter = np.exp(sj * z - 0.5 * sj * sj)
        dp = accel / PARAMS["accel_ref"] * np.maximum(state["stress_ext"], 0.0) * jitter / state["life_scale"]
        state["progress"] = np.minimum(state["progress"] + dp, 1.0)
        # acute faults: running peak so hi never recovers without maintenance
        sev_h = self._fault(state, "hpt_blade_erosion", n)
        sev_f = self._fault(state, "compressor_fouling", n)
        state["fault_hpt_peak"] = np.maximum(state["fault_hpt_peak"], sev_h)
        state["fault_foul_peak"] = np.maximum(state["fault_foul_peak"], sev_f)
        state["fault_peak"] = PARAMS["fault_progress_hpt"] * state["fault_hpt_peak"] + PARAMS["fault_progress_foul"] * state["fault_foul_peak"]
        state["llp_used"] = state["llp_used"] + (1.0 + PARAMS["llp_derate_gain"] * (PARAMS["derate_ref"] - ctx.thrust_derate) / PARAMS["derate_ref"])
        state["age_cycles"] += 1
        state["age_virtual"] += 1
        state["age_hours"] += ctx.flight_hours
        self._update(state)

    def emit(self, state: State, ctx: OperatingContext, rng: np.random.Generator) -> dict[str, np.ndarray]:
        f = _fit()
        n = ctx.n
        v = state["variant"]
        u = rng.random(n)
        eps = rng.standard_normal((n, len(SENSOR_NAMES)))
        cdf = np.cumsum(f.freq[v], axis=1)
        cond = np.minimum((u[:, None] > cdf).sum(1), f.n_cond[v] - 1)
        cond = np.where(f.n_cond[v] > 1, cond, 0)
        p = np.clip(state["progress"], 0.0, 1.0)
        z = np.einsum("nsb,nb->ns", state["coef"], basis(p)) + f.noise[v] * eps
        z = z + state["fault_hpt_peak"][:, None] * f.load_hpt + state["fault_foul_peak"][:, None] * f.load_foul
        raw = f.mu[v, cond] + f.sd[v, cond] * z
        res = f.res[v]
        raw = np.where(res > 0, np.round(raw / np.where(res > 0, res, 1.0)) * res, raw)
        out = {name: raw[:, i] for i, name in enumerate(SENSOR_NAMES)}
        # extras (noise-light derived channels)
        i50 = SENSOR_NAMES.index("T50")
        t50_rise_k = np.einsum("nb,nb->n", state["coef"][:, i50], basis(p) - basis(0.0)) * f.sd[v, 0, i50] / 1.8
        margin = state["egt_margin0"] - np.maximum(t50_rise_k, 0.0) - PARAMS["egt_fault_c"] * state["fault_hpt_peak"]
        margin = margin - PARAMS["egt_hot_c_per_c"] * np.maximum(ctx.oat_c - PARAMS["hot_ref_c"], 0.0)
        out["egt_margin_c"] = margin + 0.5 * rng.standard_normal(n)
        out["llp_cycles_remaining"] = np.maximum(state["llp_limit"] - state["llp_used"], 0.0)
        out["hpc_eff_pct"] = 100.0 - PARAMS["hpc_eff_loss_pct"] * state["def_hpc"] + 0.05 * rng.standard_normal(n)
        return out

    def health_index(self, state: State) -> np.ndarray:
        return state["hi"]

    def maintain(self, state: State, idx: np.ndarray, action: str, q: float, rng: np.random.Generator) -> None:
        if action not in ("inspect", "repair", "overhaul", "replace"):
            raise ValueError(f"unknown action: {action}")
        if idx.size == 0:
            return
        q = float(np.clip(q, 0.0, 1.0))
        if action == "inspect":
            state["inspected"][idx] = True  # borescope: no physical change
            return
        if action in ("repair", "overhaul"):
            keep = PARAMS["repair_keep"] + (1.0 - PARAMS["repair_keep"]) * q if action == "repair" else PARAMS["overhaul_keep"] * q
            state["progress"][idx] *= keep
            for k in ("fault_peak", "fault_hpt_peak", "fault_foul_peak"):
                state[k][idx] *= 0.2 if action == "overhaul" else 0.5
            state["age_virtual"][idx] *= keep
        else:
            new = _sample(_fit(), state["variant"][idx], rng)
            for k, vals in new.items():
                state[k][idx] = vals
            for k in ("progress", "fault_peak", "fault_hpt_peak", "fault_foul_peak", "llp_used", "age_cycles", "age_hours", "age_virtual"):
                state[k][idx] = 0.0
            state["failed"][idx] = False
        state["inspected"][idx] = False
        self._update(state)
