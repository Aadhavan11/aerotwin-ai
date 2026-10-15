"""Sim-trained health models for the non-engine kinds (brakes, landing_gear, hydraulics, avionics, apu, tires, ecs,
electrical). ALL TRAINING AND TEST DATA ARE SIMULATED (Simulator v2); train and test come from the same simulator,
so the evaluation is circular. See docs/ML-GENERIC.md.

Pipeline
--------
1. ``simulate_run``: a v2 ``World`` per seed (run-to-failure with ``auto_maintain=False``, or with the maintenance
   policy), per-unit external stress, optional shifted regime (hot, heavy, dusty, high utilisation).
2. Per-unit records -> windows: the last 30 cycles of the kind's telemetry channels (``TELEMETRY_KEYS``; exactly what
   the live service emits), features ``mean, slope, last, std, min, max`` per channel plus age (cycles since the unit
   was last restored; at serving time the last row's ``cycle``).
3. Targets: RUL to end of life (failure flag or health index <= 0.15) capped at ``CAP`` (right-censored rows are
   dropped unless the known horizon already exceeds the cap), health index, "fault active" label from ground truth.
4. Models per kind: HistGradientBoosting quantile regressors (p10/p50/p90), a health regressor, a fault classifier.
5. Split-conformal calibration (``app.ml.conformal``) of the p10-p90 band on calibration SEEDS (disjoint units).

Splits are by seed (hence by unit serial), never by row.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
import warnings
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from app.ml import conformal as cf
from app.ml.sensors import GENERIC, TELEMETRY_KEYS

log = logging.getLogger(__name__)

ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
META_NAME = "generic_meta.json"
MODEL_VERSION = "sim-trained (SIMULATED data)"
KINDS = tuple(GENERIC)
WINDOW = 30
CAP = 400.0               # RUL cap in cycles (flights); the target is min(CAP, true remaining cycles)
EOL_HI = 0.15             # end of life: health index at or below this (the v2 condition-based policy threshold), or failed
FAULT_SEV_MIN = 0.1       # a ground-truth fault counts as "active" once its severity reaches this
ALPHA = 0.2               # 80% intervals
STATS = ("mean", "slope", "last", "std", "min", "max")
SEED = 20261014


# --------------------------------------------------------------------------
# Window features (training and serving use this one function)
# --------------------------------------------------------------------------
def feature_names(kind: str) -> list[str]:
    return [f"{ch}.{s}" for ch in TELEMETRY_KEYS[kind] for s in STATS] + ["age"]


def window_features(values: np.ndarray, win: int = WINDOW) -> np.ndarray:
    """Row ``i`` describes rows ``max(0, i-win+1)..i`` of ``values`` (L, C): (L, 6C), NaN-aware.

    Slope is the least-squares slope per row index (rows are consecutive cycles); windows with < 3 valid values get
    slope 0. ``last`` is the last non-missing value up to row ``i`` (NaN before the first valid one).
    """
    v = np.asarray(values, dtype="float64")
    L, C = v.shape
    pad = np.full((win - 1, C), np.nan)
    W = np.lib.stride_tricks.sliding_window_view(np.concatenate([pad, v]), win, axis=0)   # (L, C, win)
    m = ~np.isnan(W)
    n = m.sum(axis=2)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        mean = np.nanmean(W, axis=2)
        std = np.nanstd(W, axis=2)
        mn = np.nanmin(W, axis=2)
        mx = np.nanmax(W, axis=2)
        x = np.arange(win, dtype="float64")
        xm = (m * x).sum(axis=2) / np.maximum(n, 1)
        dx = x - xm[..., None]
        cov = np.nansum(m * dx * (np.nan_to_num(W) - np.nan_to_num(mean)[..., None]), axis=2)
        var = (m * dx * dx).sum(axis=2)
        slope = np.where((n >= 3) & (var > 1e-12), cov / np.maximum(var, 1e-12), 0.0)
        slope = np.where(n == 0, np.nan, slope)
    last = np.full_like(v, np.nan)
    for j in range(C):
        idx = np.where(~np.isnan(v[:, j]), np.arange(L), -1)
        np.maximum.accumulate(idx, out=idx)
        ok = idx >= 0
        last[ok, j] = v[idx[ok], j]
    out = np.stack([mean, slope, last, std, mn, mx], axis=2)                              # (L, C, 6)
    return out.reshape(L, C * len(STATS))


def with_age(F: np.ndarray, age: np.ndarray) -> np.ndarray:
    return np.concatenate([F, np.asarray(age, dtype="float64").reshape(-1, 1)], axis=1)


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class RunSpec:
    seed: int
    maintain: bool = False        # True: the v2 maintenance policy acts (restorations, repairs); False: run to failure
    shifted: bool = False         # held-out stress regime


@dataclass(frozen=True)
class GenConfig:
    train: tuple[RunSpec, ...]
    calib: tuple[RunSpec, ...]
    test: tuple[RunSpec, ...]
    shift: tuple[RunSpec, ...]
    kinds: tuple[str, ...] = KINDS
    n_tails: int = 8
    horizon: int = 3000           # cycles per run-to-failure world
    maintained_horizon: int = 1500
    stride: int = 2               # keep every ``stride``-th cycle as a sample
    stress_range: tuple[float, float] = (1.0, 3.0)      # per-unit external stress (``stress_ext``), in-distribution
    shift_stress_range: tuple[float, float] = (3.5, 5.5)
    max_iter: int = 100
    max_leaf_nodes: int = 15
    seed: int = SEED


def default_config() -> GenConfig:
    def rs(seeds: Sequence[int], shifted: bool = False) -> tuple[RunSpec, ...]:
        # alternate run-to-failure and maintained worlds
        return tuple(RunSpec(s, maintain=bool(i % 2), shifted=shifted) for i, s in enumerate(seeds))
    return GenConfig(train=rs(range(101, 109)), calib=rs((201, 202, 203, 204)), test=rs((301, 302, 303, 304)),
                     shift=rs((401, 402, 403, 404), True))


def tiny_config() -> GenConfig:
    def rs(seeds: Sequence[int], shifted: bool = False) -> tuple[RunSpec, ...]:
        return tuple(RunSpec(s, maintain=bool(i % 2), shifted=shifted) for i, s in enumerate(seeds))
    return GenConfig(train=rs((101, 102, 103, 104)), calib=rs((201, 202, 203)), test=rs((301, 302, 303)), shift=rs((401,), True),
                     kinds=("tires", "hydraulics"), n_tails=5, horizon=800, maintained_horizon=500, stride=2,
                     max_iter=60)


# --------------------------------------------------------------------------
# Simulation
# --------------------------------------------------------------------------
@dataclass
class UnitRecord:
    kind: str
    unit: int
    seed: int
    key: str
    values: np.ndarray             # (T, C) telemetry channels (TELEMETRY_KEYS order), NaN where a value is missing
    hi: np.ndarray                 # (T,) health index at emission time
    eol: np.ndarray                # (T,) bool: failed or hi <= EOL_HI at this cycle
    restore: list[int]             # cycles at whose end the unit was restored (non-inspection action)
    faults: list[tuple[str, int, float]]   # (mode, onset cycle, cleared cycle or inf)
    shifted: bool = False
    maintained: bool = False


_DUSTY = ("DEN", "DFW", "LAX")


def _regime_world(seed: int, tails: list[str], maintain: bool, shifted: bool, util: float, oat_shift: float):
    from app.sim.v2.world import World

    class _W(World):
        def _profile(self, tail: str, cycle: int):
            p, st = super()._profile(tail, cycle)
            if oat_shift or shifted:
                p = replace(p, oat_dep_c=p.oat_dep_c + oat_shift, oat_arr_c=p.oat_arr_c + oat_shift,
                            density_alt_arr_ft=p.density_alt_arr_ft + 150.0 * oat_shift)
            if shifted:
                p = replace(p, payload_frac=min(1.0, p.payload_frac + 0.15), landing_weight_frac=min(1.0, p.landing_weight_frac + 0.1),
                            brake_energy_mj=p.brake_energy_mj * 1.35, sink_rate_fps=p.sink_rate_fps * 1.3, wet_runway=bool(p.wet_runway or cycle % 3 == 0))
                st = _DUSTY[(cycle + sum(map(ord, tail))) % len(_DUSTY)]
            return p, st

    return _W(seed, tails, stochastic=True, auto_maintain=maintain, utilisation=util)


# Accelerated-life factor per kind: the v2 hydraulics / electrical / apu wear rates were slowed (docs/RELIABILITY.md) so a
# nominal-stress unit no longer runs to failure inside the training horizon. The per-unit external stress is multiplied by the
# same factor so the training worlds still span healthy -> failed (stress_ext is a hidden driver, not a feature).
LIFE_COMPRESSION: dict[str, float] = {"hydraulics": 4.5, "electrical": 3.1, "apu": 1.6}


def simulate_run(spec: RunSpec, cfg: GenConfig) -> list[UnitRecord]:
    """One world -> per-unit records for ``cfg.kinds``."""
    rng = np.random.default_rng([cfg.seed, spec.seed])
    tails = [f"G{spec.seed % 1000:03d}-{i:02d}" for i in range(cfg.n_tails)]
    util = float(rng.uniform(1.4, 1.8)) if spec.shifted else float(rng.uniform(0.8, 1.3))
    oat_shift = 18.0 if spec.shifted else float(rng.uniform(-8.0, 8.0))
    w = _regime_world(spec.seed, tails, spec.maintain, spec.shifted, util, oat_shift)
    lo, hi_ = cfg.shift_stress_range if spec.shifted else cfg.stress_range
    for k in cfg.kinds:
        w.fleet.state[k]["stress_ext"][:] = rng.uniform(lo, hi_, size=w.counts[k]) * LIFE_COMPRESSION.get(k, 1.0)
    T = cfg.maintained_horizon if spec.maintain else cfg.horizon
    index = {w.component_id(k, i): (k, i) for k in cfg.kinds for i in range(w.counts[k])}
    chans = {k: TELEMETRY_KEYS[k] for k in cfg.kinds}
    vals = {k: np.full((w.counts[k], T, len(chans[k])), np.nan) for k in cfg.kinds}
    hi = {k: np.zeros((w.counts[k], T)) for k in cfg.kinds}
    failed = {k: np.zeros((w.counts[k], T), dtype=bool) for k in cfg.kinds}
    for c in range(T):
        rows = w.step()
        w.rows.clear()
        for r in rows:
            ki = index.get(r["component_id"])
            if ki is None:
                continue
            k, i = ki
            vals[k][i, c, :] = [r["values"].get(ch, np.nan) for ch in chans[k]]
        h = w.fleet.health_index()
        for k in cfg.kinds:
            hi[k][:, c] = h[k]
            failed[k][:, c] = np.asarray(w.fleet.state[k]["failed"], dtype=bool)
    restore: dict[tuple[str, int], list[int]] = {}
    for ev in w.events:
        k, i = ev["kind"], int(ev["unit"])
        if ev["action"] in ("inspect", "nff_return") or k not in cfg.kinds:   # an NFF return does not restore the unit
            continue
        c = int(ev["cycle"])
        if not 1 <= c <= T:
            continue
        restore.setdefault((k, i), []).append(c)
        hi[k][i, c - 1] = float(ev["hi_before"])        # the row at the action cycle was emitted before the restoration
        if ev["reason"] == "failure":
            failed[k][i, c - 1] = True
    fl: dict[tuple[str, int], list[tuple[str, int, float]]] = {}
    for g in w.ground_truth():
        if g["kind"] in cfg.kinds:
            cl = g.get("cleared_cycle")
            fl.setdefault((g["kind"], int(g["unit"])), []).append((g["mode"], int(g["onset_cycle"]), float("inf") if cl is None else float(cl)))
    out = []
    for k in cfg.kinds:
        for i in range(w.counts[k]):
            out.append(UnitRecord(kind=k, unit=i, seed=spec.seed, key=f"{spec.seed}/{w.component_id(k, i)}", values=vals[k][i],
                                  hi=hi[k][i], eol=failed[k][i] | (hi[k][i] <= EOL_HI), restore=sorted(restore.get((k, i), [])),
                                  faults=fl.get((k, i), []), shifted=spec.shifted, maintained=spec.maintain))
    return out


# --------------------------------------------------------------------------
# Samples
# --------------------------------------------------------------------------
@dataclass
class Samples:
    X: np.ndarray
    rul: np.ndarray
    hi: np.ndarray
    anom: np.ndarray
    unit: np.ndarray               # unit key (string)
    seed: np.ndarray
    ref: np.ndarray                # (n, 2): index into ``raw`` and cycle index within that record
    raw: list[np.ndarray] = field(default_factory=list)   # per record raw (T, C) values, for the rule baseline

    @staticmethod
    def empty(nf: int) -> "Samples":
        return Samples(np.zeros((0, nf)), np.zeros(0), np.zeros(0), np.zeros(0, dtype=int), np.zeros(0, dtype=object),
                       np.zeros(0, dtype=int), np.zeros((0, 2), dtype=int), [])

    def __len__(self) -> int:
        return len(self.rul)


def _fault_active(rec: UnitRecord) -> np.ndarray:
    from app.sim.v2.faults import LIBRARY

    T = len(rec.hi)
    c = np.arange(1, T + 1, dtype="float64")
    act = np.zeros(T, dtype=bool)
    for mode, onset, cleared in rec.faults:
        m = LIBRARY.get(mode)
        if m is None:
            continue
        sev = np.asarray(m.severity(np.maximum(c - onset, 0.0)), dtype="float64")
        act |= (c >= onset) & (c < cleared) & (sev >= FAULT_SEV_MIN)
    return act


def record_samples(rec: UnitRecord, stride: int, offset: int = 0) -> dict[str, np.ndarray]:
    """Valid (uncensored) sample rows of one unit as arrays; see the module docstring for the target definitions."""
    T = len(rec.hi)
    cyc = np.arange(1, T + 1)
    bounds = [0, *rec.restore]
    if bounds[-1] != T:
        bounds.append(T)
    rul = np.full(T, np.nan)
    age = np.zeros(T)
    for a, b in zip(bounds[:-1], bounds[1:]):          # segment covers cycles a+1..b
        if b <= a:
            continue
        seg = slice(a, b)
        e = np.flatnonzero(rec.eol[seg])
        c = cyc[seg]
        age[seg] = c - a
        if len(e):
            eol_c = c[e[0]]
            r = np.minimum(CAP, eol_c - c).astype("float64")
            r[c > eol_c] = np.nan                       # a unit past end of life would have been withdrawn
        else:
            r = np.where((b - c) >= CAP, CAP, np.nan)    # censored: only rows whose known horizon already exceeds the cap
        rul[seg] = r
    ok = ~np.isnan(rul)
    sel = np.zeros(T, dtype=bool)
    sel[offset::stride] = True
    ok &= sel
    # a window needs a few rows to be informative
    ok &= cyc >= 3
    idx = np.flatnonzero(ok)
    F = with_age(window_features(rec.values), age)
    anom = _fault_active(rec)
    return {"X": F[idx], "rul": rul[idx], "hi": rec.hi[idx], "anom": anom[idx].astype(int), "t": idx}


def build_samples(recs: Sequence[UnitRecord], kind: str, stride: int) -> Samples:
    parts: dict[str, list] = {k: [] for k in ("X", "rul", "hi", "anom", "unit", "seed", "ref")}
    raw: list[np.ndarray] = []
    for rec in recs:
        if rec.kind != kind:
            continue
        s = record_samples(rec, stride, offset=int(rec.unit) % stride)
        if len(s["rul"]) == 0:
            continue
        parts["X"].append(s["X"])
        parts["rul"].append(s["rul"])
        parts["hi"].append(s["hi"])
        parts["anom"].append(s["anom"])
        parts["unit"].append(np.full(len(s["rul"]), rec.key, dtype=object))
        parts["seed"].append(np.full(len(s["rul"]), rec.seed))
        parts["ref"].append(np.stack([np.full(len(s["rul"]), len(raw)), s["t"]], axis=1))
        raw.append(rec.values)
    if not raw:
        return Samples.empty(len(feature_names(kind)))
    return Samples(np.concatenate(parts["X"]), np.concatenate(parts["rul"]), np.concatenate(parts["hi"]), np.concatenate(parts["anom"]),
                   np.concatenate(parts["unit"]), np.concatenate(parts["seed"]), np.concatenate(parts["ref"]).astype(int), raw)


def generate(cfg: GenConfig) -> dict[str, list[UnitRecord]]:
    """role -> records; roles: train, calib, test, shift."""
    out: dict[str, list[UnitRecord]] = {}
    for role, specs in (("train", cfg.train), ("calib", cfg.calib), ("test", cfg.test), ("shift", cfg.shift)):
        recs: list[UnitRecord] = []
        for sp in specs:
            recs.extend(simulate_run(sp, cfg))
        out[role] = recs
    return out


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
def _hgb(cfg: GenConfig, **kw: Any):
    from sklearn.ensemble import HistGradientBoostingRegressor

    return HistGradientBoostingRegressor(max_iter=cfg.max_iter, max_leaf_nodes=cfg.max_leaf_nodes, learning_rate=0.1,
                                         min_samples_leaf=40, l2_regularization=1.0, early_stopping=False,
                                         random_state=cfg.seed, **kw)


def _pick_threshold(p: np.ndarray, y: np.ndarray) -> float:
    if y.sum() == 0 or y.sum() == len(y):
        return 0.5
    best, thr = -1.0, 0.5
    for t in np.unique(np.round(np.quantile(p, np.linspace(0.05, 0.99, 60)), 4)):
        pred = p >= t
        tp = float((pred & (y == 1)).sum())
        f1 = 2 * tp / max(2 * tp + float((pred & (y == 0)).sum()) + float((~pred & (y == 1)).sum()), 1.0)
        if f1 > best:
            best, thr = f1, float(t)
    return thr


def _clip_band(lo: np.ndarray, mid: np.ndarray, hi: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mid = np.clip(mid, 0.0, CAP)
    lo = np.clip(np.minimum(lo, mid), 0.0, CAP)
    hi = np.clip(np.maximum(hi, mid), 0.0, CAP)
    return lo, mid, hi


def raw_predict(models: dict, X: np.ndarray) -> dict[str, np.ndarray]:
    q10, q50, q90 = (models[k].predict(X) for k in ("q10", "q50", "q90"))
    lo, mid, hi = _clip_band(*np.sort(np.stack([q10, q50, q90]), axis=0))
    return {"q10": lo, "q50": mid, "q90": hi}


def fit_kind(kind: str, train: Samples, calib: Samples, cfg: GenConfig) -> dict:
    from sklearn.ensemble import HistGradientBoostingClassifier

    models = {
        "q10": _hgb(cfg, loss="quantile", quantile=0.1).fit(train.X, train.rul),
        "q50": _hgb(cfg, loss="quantile", quantile=0.5).fit(train.X, train.rul),
        "q90": _hgb(cfg, loss="quantile", quantile=0.9).fit(train.X, train.rul),
        "hi": _hgb(cfg).fit(train.X, train.hi),
    }
    pos = int(train.anom.sum())
    if 0 < pos < len(train):
        models["anom"] = HistGradientBoostingClassifier(max_iter=cfg.max_iter, max_leaf_nodes=cfg.max_leaf_nodes, learning_rate=0.1,
                                                        min_samples_leaf=40, l2_regularization=1.0, early_stopping=False,
                                                        random_state=cfg.seed).fit(train.X, train.anom)
    rp = raw_predict(models, calib.X)
    spread = (rp["q90"] - rp["q10"]) / 2.0
    cal = {
        "cqr_q": cf.CQR(ALPHA).fit(calib.rul, rp["q10"], rp["q90"]).q,
        "abs_q": cf.AbsoluteConformal(ALPHA).fit(calib.rul, rp["q50"]).q,
        "norm_q": cf.NormalizedConformal(ALPHA).fit(calib.rul, rp["q50"], spread).q,
        "n_calib_rows": int(len(calib)),
        "n_calib_units": int(len(set(calib.unit))),
    }
    thr = 0.5
    if "anom" in models:
        pa = models["anom"].predict_proba(calib.X)[:, 1]
        thr = _pick_threshold(pa, calib.anom)
    last = [i for i, n in enumerate(feature_names(kind)) if n.endswith(".last")]
    ranges = {ch: [float(np.nanpercentile(train.X[:, j], 0.5)), float(np.nanpercentile(train.X[:, j], 99.5))]
              for ch, j in zip(TELEMETRY_KEYS[kind], last)}
    return {
        "kind": kind, "models": models, "calibration": cal, "anomaly_threshold": float(thr), "feature_names": feature_names(kind),
        "baseline": np.nanmedian(train.X, axis=0), "ranges": ranges, "channels": list(TELEMETRY_KEYS[kind]),
        "n_train_rows": int(len(train)), "n_train_units": int(len(set(train.unit))),
    }


# --------------------------------------------------------------------------
# Prediction (shared by serving and evaluation)
# --------------------------------------------------------------------------
def predict(bundle: dict, X: np.ndarray, method: str = "cqr") -> dict[str, np.ndarray]:
    """Calibrated p10/p50/p90 RUL, health index, anomaly probability for feature rows ``X``."""
    X = np.atleast_2d(np.asarray(X, dtype="float64"))
    rp = raw_predict(bundle["models"], X)
    cal = bundle["calibration"]
    lo, mid, hi = rp["q10"], rp["q50"], rp["q90"]
    if method == "cqr":
        lw, h = lo - cal["cqr_q"], hi + cal["cqr_q"]
    elif method == "absolute":
        lw, h = mid - cal["abs_q"], mid + cal["abs_q"]
    elif method == "normalized":
        w = cal["norm_q"] * np.maximum((hi - lo) / 2.0, 1.0)
        lw, h = mid - w, mid + w
    elif method == "raw":
        lw, h = lo, hi
    else:
        raise ValueError(method)
    lw, m, h = _clip_band(lw, mid, h)
    anom = bundle["models"].get("anom")
    p = anom.predict_proba(X)[:, 1] if anom is not None else np.zeros(len(X))
    return {"p10": lw, "p50": m, "p90": h, "health": np.clip(bundle["models"]["hi"].predict(X), 0.0, 1.0), "anomaly": p}


def explain(bundle: dict, x: np.ndarray, top: int = 3) -> list[tuple[str, float]]:
    """Occlusion attribution: for each feature, p50 RUL(x) minus p50 RUL with that feature set to its training median."""
    x = np.asarray(x, dtype="float64").reshape(1, -1)
    base = bundle["baseline"]
    V = np.repeat(x, x.shape[1], axis=0)
    for j in range(x.shape[1]):
        V[j, j] = base[j]
    q50 = bundle["models"]["q50"]
    d = float(q50.predict(x)[0]) - q50.predict(V)
    order = np.argsort(-np.abs(d))[:top]
    return [(bundle["feature_names"][j], float(d[j])) for j in order if abs(d[j]) > 1e-9]


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------
def _reg_metrics(y: np.ndarray, p50: np.ndarray) -> dict:
    e = p50 - y
    near = y < CAP
    return {"mae": float(np.mean(np.abs(e))), "rmse": float(np.sqrt(np.mean(e ** 2))),
            "mae_below_cap": float(np.mean(np.abs(e[near]))) if near.any() else None, "n_below_cap": int(near.sum())}


def _rule_predict(kind: str, s: Samples, max_rows: int = 300) -> Optional[dict]:
    """The rule-based demo model on the same windows (subsampled evenly): p10/p50/p90 (clipped to CAP), health, anomaly."""
    from datetime import datetime, timedelta, timezone
    from app.contracts.models import TelemetryRow
    from app.ml import health

    if len(s) == 0:
        return None
    idx = np.unique(np.linspace(0, len(s) - 1, min(max_rows, len(s))).astype(int))
    t0 = datetime(2028, 1, 1, tzinfo=timezone.utc)
    chans = TELEMETRY_KEYS[kind]
    out = {k: [] for k in ("p10", "p50", "p90", "health", "anomaly")}
    kept: list[int] = []
    for n in idx:
        ri, t = int(s.ref[n, 0]), int(s.ref[n, 1])
        raw = s.raw[ri]
        a = max(0, t - WINDOW + 1)
        rows = []
        for c in range(a, t + 1):
            v = {ch: float(raw[c, j]) for j, ch in enumerate(chans) if not np.isnan(raw[c, j])}
            rows.append(TelemetryRow(tail="X", component_id=f"X/{kind}", ts=t0 + timedelta(hours=c), cycle=c + 1, values=v))
        sc = health._score_generic_rule(kind, "X", f"X/{kind}", rows)
        r = sc.rul_cycles
        if r is None:                    # the rule model could not score this window (e.g. wear signal missing)
            continue
        kept.append(int(n))
        out["p10"].append(r.p10)
        out["p50"].append(r.p50)
        out["p90"].append(r.p90)
        out["health"].append(sc.health)
        out["anomaly"].append(sc.anomaly_score)
    if not kept:
        return None
    res = {k: np.asarray(v, dtype="float64") for k, v in out.items()}
    res["idx"] = np.asarray(kept)
    res["n_unscorable"] = len(idx) - len(kept)
    for k in ("p10", "p50", "p90"):
        res[k] = np.minimum(res[k], CAP)
    return res


def _auc(y: np.ndarray, score: np.ndarray) -> Optional[float]:
    from sklearn.metrics import roc_auc_score

    if len(np.unique(y)) < 2:
        return None
    return float(roc_auc_score(y, score))


def evaluate_set(bundle: dict, s: Samples, kind: str, with_rule: bool = True) -> dict:
    if len(s) == 0:
        return {"n_rows": 0}
    out: dict[str, Any] = {"n_rows": int(len(s)), "n_units": int(len(set(s.unit))), "n_seeds": int(len(set(s.seed))),
                           "frac_at_cap": float(np.mean(s.rul >= CAP)), "anomaly_prevalence": float(np.mean(s.anom))}
    preds = {m: predict(bundle, s.X, m) for m in ("raw", "cqr", "absolute", "normalized")}
    out["rul"] = _reg_metrics(s.rul, preds["cqr"]["p50"])
    out["coverage_80"] = {m: cf.coverage(s.rul, p["p10"], p["p90"]) for m, p in preds.items()}
    out["mean_width"] = {m: cf.mean_width(p["p10"], p["p90"]) for m, p in preds.items()}
    nc = s.rul < CAP
    out["coverage_80_below_cap"] = ({m: cf.coverage(s.rul[nc], p["p10"][nc], p["p90"][nc]) for m, p in preds.items()} if nc.any() else None)
    out["health_mae"] = float(np.mean(np.abs(preds["cqr"]["health"] - s.hi)))
    has_clf = "anom" in bundle["models"]
    out["anomaly_auc"] = _auc(s.anom, preds["cqr"]["anomaly"]) if has_clf else None
    thr = bundle["anomaly_threshold"]
    flag = preds["cqr"]["anomaly"] >= thr
    pos = s.anom == 1
    out["anomaly_recall"] = float(flag[pos].mean()) if pos.any() else None
    out["anomaly_fpr"] = float(flag[~pos].mean()) if (~pos).any() else None
    if with_rule:
        r = _rule_predict(kind, s)
        if r is not None:
            i = r["idx"]
            y = s.rul[i]
            out["rule_model"] = {
                "n_rows": int(len(i)), "n_unscorable": int(r["n_unscorable"]), "rul": _reg_metrics(y, r["p50"]), "coverage_80": cf.coverage(y, r["p10"], r["p90"]),
                "health_mae": float(np.mean(np.abs(r["health"] - s.hi[i]))), "anomaly_auc": _auc(s.anom[i], r["anomaly"]),
                "simtrained_same_rows": {"rul": _reg_metrics(y, preds["cqr"]["p50"][i]),
                                         "coverage_80": cf.coverage(y, preds["cqr"]["p10"][i], preds["cqr"]["p90"][i]),
                                         "health_mae": float(np.mean(np.abs(preds["cqr"]["health"][i] - s.hi[i]))),
                                         "anomaly_auc": _auc(s.anom[i], preds["cqr"]["anomaly"][i]) if has_clf else None},
            }
    return out


# --------------------------------------------------------------------------
# End to end
# --------------------------------------------------------------------------
def train_all(cfg: Optional[GenConfig] = None, log_fn=None) -> tuple[dict[str, dict], dict]:
    """Simulate, fit, calibrate, evaluate. Returns (bundles by kind, meta dict). Deterministic given ``cfg``."""
    cfg = cfg or default_config()
    t0 = time.time()
    data = generate(cfg)
    t_gen = time.time() - t0
    bundles: dict[str, dict] = {}
    ev: dict[str, Any] = {}
    for kind in cfg.kinds:
        tr, ca, te, sh = (build_samples(data[r], kind, cfg.stride) for r in ("train", "calib", "test", "shift"))
        b = fit_kind(kind, tr, ca, cfg)
        bundles[kind] = b
        ev[kind] = {"test_in_distribution": evaluate_set(b, te, kind), "test_shifted_regime": evaluate_set(b, sh, kind),
                    "calibration_rows": int(len(ca)), "train_rows": int(len(tr))}
        if log_fn:
            log_fn(f"{kind}: train {len(tr)} calib {len(ca)} test {len(te)} shift {len(sh)}")
    import sklearn

    meta = {
        "model_version": MODEL_VERSION, "data": "SIMULATED (app.sim.v2 World); train and test come from the same simulator",
        "sklearn": sklearn.__version__, "window": WINDOW, "rul_cap": CAP, "eol_health_index": EOL_HI, "alpha": ALPHA,
        "interval_method": "cqr", "fault_severity_min": FAULT_SEV_MIN,
        "config": {"train_seeds": [s.seed for s in cfg.train], "calib_seeds": [s.seed for s in cfg.calib],
                   "test_seeds": [s.seed for s in cfg.test], "shift_seeds": [s.seed for s in cfg.shift],
                   "maintained_seeds": sorted({s.seed for s in (*cfg.train, *cfg.calib, *cfg.test, *cfg.shift) if s.maintain}),
                   "n_tails": cfg.n_tails, "horizon": cfg.horizon, "maintained_horizon": cfg.maintained_horizon, "stride": cfg.stride,
                   "stress_range": list(cfg.stress_range), "shift_stress_range": list(cfg.shift_stress_range),
                   "max_iter": cfg.max_iter, "max_leaf_nodes": cfg.max_leaf_nodes, "seed": cfg.seed},
        "generation_seconds": round(t_gen, 1), "kinds": {k: {"calibration": bundles[k]["calibration"], "anomaly_threshold": bundles[k]["anomaly_threshold"],
                                                            "ranges": bundles[k]["ranges"], "channels": bundles[k]["channels"],
                                                            "n_train_rows": bundles[k]["n_train_rows"], "n_train_units": bundles[k]["n_train_units"]}
                                                        for k in cfg.kinds},
        "evaluation": ev,
    }
    return bundles, meta


def save(bundles: dict[str, dict], meta: dict, out_dir: Path = ARTIFACT_DIR) -> dict[str, int]:
    import joblib

    out_dir.mkdir(parents=True, exist_ok=True)
    sizes = {}
    for k, b in bundles.items():
        p = out_dir / f"generic_{k}.joblib"
        joblib.dump(b, p, compress=("lzma", 6))
        sizes[p.name] = p.stat().st_size
    (out_dir / META_NAME).write_text(json.dumps(meta, indent=1, sort_keys=True, default=float))
    return sizes


# --------------------------------------------------------------------------
# Loading for serving
# --------------------------------------------------------------------------
@lru_cache(maxsize=None)
def load(kind: str, directory: Optional[str] = None) -> Optional[dict]:
    """The fitted bundle for ``kind`` or None (missing/incompatible artifacts)."""
    try:
        import joblib

        return joblib.load(Path(directory or ARTIFACT_DIR) / f"generic_{kind}.joblib")
    except Exception as exc:
        log.warning("sim-trained model for %s unavailable (%s)", kind, exc)
        return None


def clear_cache() -> None:
    load.cache_clear()


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tiny", action="store_true", help="small configuration (tests)")
    ap.add_argument("--out", type=Path, default=ARTIFACT_DIR)
    a = ap.parse_args(argv)
    cfg = tiny_config() if a.tiny else default_config()
    bundles, meta = train_all(cfg, log_fn=print)
    sizes = save(bundles, meta, a.out)
    print(json.dumps({"artifact_bytes": sizes, "total": sum(sizes.values())}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
