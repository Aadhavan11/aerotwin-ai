"""Simulated-vs-real validation of the v2 engine surrogate (run: ``python -m app.calib.engine --validate``)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from app.calib.engine import load_train
from app.ml.sensors import SENSOR_NAMES

STATIONS = ("ATL", "DFW", "JFK", "ORD", "LAX", "SFO", "DEN", "MIA")


def simulate(n: int, seed: int, variant: str = "fd001", max_cycles: int = 700, stress: float = 1.0) -> dict[str, Any]:
    """Run n engines to failure with v1-profile contexts. Returns per-engine telemetry arrays (n, T, S) and life.

    Validation runs on the raw C-MAPSS time axis (``flights_per_model_cycle`` = 1) so cycles-to-failure is comparable to the data.
    """
    from app.sim.v2.models.engine import PARAMS

    saved = PARAMS["flights_per_model_cycle"]
    PARAMS["flights_per_model_cycle"] = 1.0
    try:
        return _simulate(n, seed, variant, max_cycles, stress)
    finally:
        PARAMS["flights_per_model_cycle"] = saved


def _simulate(n: int, seed: int, variant: str, max_cycles: int, stress: float) -> dict[str, Any]:
    from app.sim.profile import profile_for_cycle
    from app.sim.v2 import Fleet, build_context
    from app.sim.v2.models.engine import EngineModel

    fleet = Fleet(seed, {"engine": n})
    if variant != "fd001":
        EngineModel.set_variant(fleet.state["engine"], np.arange(n), variant, np.random.default_rng(seed))
    tails = [f"E{i}" for i in range(n)]
    prev = None
    sensors = np.full((n, max_cycles, len(SENSOR_NAMES)), np.nan)
    hi = np.full((n, max_cycles), np.nan)
    life = np.full(n, -1)
    for c in range(max_cycles):
        profs = [profile_for_cycle(seed, tails[i], c) for i in range(n)]
        arr = [STATIONS[(i + c) % len(STATIONS)] for i in range(n)]
        ctx = build_context(profs, tails, seed, arr, prev=prev)
        prev = ctx
        fleet.state["engine"]["stress_ext"][:] = stress
        fleet.step_cycle(ctx)
        em = fleet.emit_all(ctx)["engine"]
        alive = life < 0
        for j, s in enumerate(SENSOR_NAMES):
            sensors[alive, c, j] = em[s][alive]
        hi[alive, c] = fleet.state["engine"]["hi"][alive]
        fin = alive & fleet.state["engine"]["failed"]
        life[fin] = c + 1
        if (life > 0).all():
            break
    return {"sensors": sensors, "hi": hi, "life": life}


def _row(tail: str, ts: datetime, cycle: int, vec: np.ndarray) -> Any:
    from app.contracts.models import TelemetryRow

    return TelemetryRow(tail=tail, component_id=f"{tail}/engine_1", ts=ts, cycle=cycle, values={s: float(vec[j]) for j, s in enumerate(SENSOR_NAMES)})


def _wasserstein(a: np.ndarray, b: np.ndarray) -> float:
    from scipy.stats import wasserstein_distance

    return float(wasserstein_distance(a, b))


def run_validation(data_dir: Path | None = None, n: int = 100, seed: int = 7) -> dict[str, Any]:
    from scipy.stats import ks_2samp
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import cross_val_predict
    from sklearn.metrics import roc_auc_score

    from app.ml.health import score_engine

    real = load_train(1, data_dir)
    sim = simulate(n, seed)
    life = sim["life"]
    out: dict[str, Any] = {"n_sim": n, "seed": seed}
    out["cycles_to_failure"] = {"sim_mean": float(life[life > 0].mean()), "sim_sd": float(life[life > 0].std(ddof=1)), "real_mean": float(real.groupby("unit")["life"].first().mean()),
                                "real_sd": float(real.groupby("unit")["life"].first().std(ddof=1)), "censored": int((life < 0).sum())}
    for v in ("fd002", "fd003", "fd004"):
        s = simulate(40, seed + 1, variant=v, max_cycles=900)
        r = load_train(int(v[-1]), data_dir).groupby("unit")["life"].first()
        lf = s["life"][s["life"] > 0]
        out["cycles_to_failure"][v] = {"sim_mean": float(lf.mean()), "sim_sd": float(lf.std(ddof=1)), "real_mean": float(r.mean()), "real_sd": float(r.std(ddof=1))}
    # sim rows with life fraction d and RUL
    rows, d_s, unit_s = [], [], []
    for i in range(n):
        L = int(life[i]) if life[i] > 0 else int(np.isfinite(sim["sensors"][i, :, 0]).sum())
        rows.append(sim["sensors"][i, :L])
        d_s.append(np.arange(1, L + 1) / L)
        unit_s.append(np.full(L, i))
    Xs, ds, us = np.concatenate(rows), np.concatenate(d_s), np.concatenate(unit_s)
    Xr, dr, ur = real[SENSOR_NAMES].to_numpy(float), real["d"].to_numpy(), real["unit"].to_numpy()

    # (1) two-sample classifier on per-row sensors (+ life fraction bin excluded) with unit-grouped CV
    from sklearn.model_selection import GroupKFold

    X = np.vstack([Xr, Xs])
    y = np.r_[np.zeros(len(Xr)), np.ones(len(Xs))]
    g = np.r_[ur, us + 10000]
    aucs = {}
    for name, mask_r, mask_s in (("all_rows", np.ones(len(Xr), bool), np.ones(len(Xs), bool)), ("late_rows_d>0.7", dr > 0.7, ds > 0.7)):
        Xm = np.vstack([Xr[mask_r], Xs[mask_s]])
        ym = np.r_[np.zeros(mask_r.sum()), np.ones(mask_s.sum())]
        gm = np.r_[ur[mask_r], us[mask_s] + 10000]
        clf = RandomForestClassifier(n_estimators=100, min_samples_leaf=5, n_jobs=-1, random_state=0)
        pr = cross_val_predict(clf, Xm, ym, groups=gm, cv=GroupKFold(5), method="predict_proba")[:, 1]
        aucs[name] = float(roc_auc_score(ym, pr))
    del X, y, g
    out["classifier_auc"] = aucs

    # (3) per-sensor marginals at matched life-fraction bins
    bins = [(0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.01)]
    marg: dict[str, Any] = {}
    for lo, hi_ in bins:
        mr, ms = (dr >= lo) & (dr < hi_), (ds >= lo) & (ds < hi_)
        per = {}
        for j, s in enumerate(SENSOR_NAMES):
            a, b = Xr[mr, j], Xs[ms, j]
            sd = a.std() + 1e-12
            per[s] = {"ks": float(ks_2samp(a, b).statistic), "wasserstein_over_sd": _wasserstein(a, b) / sd}
        marg[f"d[{lo:.2f},{min(hi_, 1):.2f})"] = {
            "ks_mean": float(np.mean([p["ks"] for p in per.values()])),
            "w_over_sd_mean": float(np.mean([p["wasserstein_over_sd"] for p in per.values()])),
            "per_sensor": per,
        }
    out["marginals"] = marg

    # (2) repo scorer on simulated windows: RUL error vs the simulator's own latent RUL (clipped at 125 like the scorer)
    errs, mono = [], []
    ts0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(min(n, 40)):
        X_i, L = rows[i], len(rows[i])
        preds, truth = [], []
        for t in range(29, L, 10):
            win = [_row(f"S{i}", ts0, k + 1, X_i[k]) for k in range(max(0, t - 29), t + 1)]
            hs = score_engine(f"S{i}", f"S{i}/engine_1", win)
            preds.append(hs.rul_cycles.p50 if hs.rul_cycles else np.nan)
            truth.append(min(L - (t + 1), 125))
        preds, truth = np.asarray(preds, float), np.asarray(truth, float)
        errs.append(preds - truth)
        if len(preds) > 3:
            from scipy.stats import spearmanr

            mono.append(float(spearmanr(np.arange(len(preds)), preds)[0]))
    e = np.concatenate(errs)
    out["scorer"] = {
        "rul_mae": float(np.nanmean(np.abs(e))),
        "rul_bias": float(np.nanmean(e)),
        "rul_rmse": float(np.sqrt(np.nanmean(e**2))),
        "spearman_pred_vs_age_mean": float(np.nanmean(mono)),
        "n_windows": int(e.size),
    }
    # same scorer on real FD001 train windows, for reference
    errs_r = []
    for u in np.unique(ur)[:40]:
        m = ur == u
        Xu, Lu = Xr[m], int(m.sum())
        for t in range(29, Lu, 10):
            win = [_row("R", ts0, k + 1, Xu[k]) for k in range(t - 29, t + 1)]
            hs = score_engine("R", "R/engine_1", win)
            errs_r.append(hs.rul_cycles.p50 - min(Lu - (t + 1), 125))
    out["scorer_real_reference"] = {"rul_mae": float(np.mean(np.abs(errs_r))), "rul_bias": float(np.mean(errs_r)), "note": "FD001 train engines (scorer was fit on a subset, so optimistic)"}
    return out
