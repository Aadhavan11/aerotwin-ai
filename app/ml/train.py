"""Train and evaluate the engine health models.

    python -m app.ml.train              # fit, calibrate, write artifacts + eval.json
    python -m app.ml.train --eval-only  # re-evaluate the committed artifacts

Splits (all by engine, never by row; seed 1234):
    train file -> fit units (85) and calibration units (15)
    test file  -> evaluation only; nothing is fitted or tuned on it
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Optional

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import IsolationForest, RandomForestRegressor

from app.ml import cmapss
from app.ml.health import (
    ARTIFACT_DIR, MODEL_VERSION, RUL_CAP, SEED, WINDOW, _bundle, _Bundle, anomaly_columns, predict_windows,
    reload_artifacts, window_features,
)
from app.ml.sensors import SENSOR_NAMES

N_TREES = 100
MIN_LEAF = 5
HEALTHY_FRACTION = 0.6
LATE_LIFE = 30                     # label: "true RUL <= 30 cycles" counts as abnormal
CALIB_EDGES = [0.0, 30.0, 60.0, 100.0, 1e9]


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
def _to_frame(traj: dict[int, list[dict]]) -> pd.DataFrame:
    rows = []
    for unit, rs in traj.items():
        for r in rs:
            rows.append({"unit": unit, "cycle": r["cycle"], **r["values"], "rul": r["true_rul"]})
    df = pd.DataFrame(rows)
    df["rul_clipped"] = df["rul"].clip(upper=RUL_CAP)
    return df


def load_frames(seed: int = SEED) -> tuple[pd.DataFrame, pd.DataFrame, list[int], list[int], str]:
    """(train frame, test frame, fit units, calibration units, data_source)."""
    if cmapss.data_available():
        train, test = cmapss.load_train(), cmapss.load_test()
        fit, calib = cmapss.split_train_units(seed)
        return train, test, fit, calib, "nasa-cmapss-fd001"
    train = _to_frame(cmapss.synthetic_trajectories(seed))
    full = _to_frame(cmapss.synthetic_trajectories(seed + 1))
    rng = np.random.default_rng(seed + 2)
    keep = []
    for unit, g in full.groupby("unit"):
        cut = int(rng.integers(WINDOW + 5, max(WINDOW + 6, len(g) - 1)))
        keep.append(g[g.cycle <= cut])
    test = pd.concat(keep, ignore_index=True)
    units = sorted(train.unit.unique())
    rng2 = np.random.default_rng(seed)
    calib = sorted(int(u) for u in rng2.permutation(units)[:15])
    fit = [int(u) for u in units if u not in set(calib)]
    return train, test, fit, calib, "synthetic-cmapss-shaped"


def featurize(df: pd.DataFrame) -> tuple[np.ndarray, pd.DataFrame]:
    """Window features for every row, per unit. Returns (X, index frame with unit/cycle/rul/rul_clipped)."""
    xs, idx = [], []
    for _, g in df.sort_values(["unit", "cycle"]).groupby("unit", sort=True):
        xs.append(window_features(g[SENSOR_NAMES].to_numpy(), g["cycle"].to_numpy()))
        idx.append(g[["unit", "cycle", "rul", "rul_clipped"]])
    return np.vstack(xs), pd.concat(idx, ignore_index=True)


# --------------------------------------------------------------------------
# Training
# --------------------------------------------------------------------------
def _pick_threshold(score: np.ndarray, label: np.ndarray) -> float:
    best_t, best_f = 0.5, -1.0
    for t in np.linspace(0.05, 0.95, 91):
        pred = score >= t
        tp = float((pred & label).sum())
        fp = float((pred & ~label).sum())
        fn = float((~pred & label).sum())
        f = 2 * tp / max(2 * tp + fp + fn, 1.0)
        if f > best_f:
            best_t, best_f = float(t), f
    return round(best_t, 2)


def train(seed: int = SEED, n_estimators: int = N_TREES, min_leaf: int = MIN_LEAF) -> dict:
    t0 = time.time()
    train_df, _test, fit_units, calib_units, source = load_frames(seed)
    fit_df = train_df[train_df.unit.isin(fit_units)]
    calib_df = train_df[train_df.unit.isin(calib_units)]
    Xf, idxf = featurize(fit_df)
    Xc, idxc = featurize(calib_df)

    rf = RandomForestRegressor(
        n_estimators=n_estimators, min_samples_leaf=min_leaf, n_jobs=-1, random_state=seed
    ).fit(Xf.astype("float32"), idxf["rul_clipped"].to_numpy())

    # Anomaly model: healthy phase only (first 60% of each fit engine's life).
    life = idxf.groupby("unit")["cycle"].transform("max")
    healthy = (idxf["cycle"] <= HEALTHY_FRACTION * life).to_numpy()
    cols = anomaly_columns()
    iforest = IsolationForest(n_estimators=200, contamination="auto", random_state=seed).fit(Xf[healthy][:, cols])

    # Population baselines for z-scores and range checks.
    healthy_rows = fit_df[(fit_df.cycle <= HEALTHY_FRACTION * fit_df.groupby("unit").cycle.transform("max"))]
    allrows = train_df
    meta = {
        "healthy_mean": {s: float(healthy_rows[s].mean()) for s in SENSOR_NAMES},
        "healthy_std": {s: float(healthy_rows[s].std()) for s in SENSOR_NAMES},
        "ranges": {s: [float(allrows[s].min()), float(allrows[s].max())] for s in SENSOR_NAMES},
    }
    # Flat-sensor check: only sensors that are essentially never flat over a window in healthy data.
    flat_rate = {s: 0.0 for s in SENSOR_NAMES}
    n_win = 0
    for _, g in fit_df.groupby("unit"):
        v = g[SENSOR_NAMES].to_numpy()
        for i in range(WINDOW, len(v) + 1):
            w = v[i - WINDOW:i]
            flat = w.std(axis=0) == 0
            n_win += 1
            for j, s in enumerate(SENSOR_NAMES):
                flat_rate[s] += float(flat[j])
    meta["flat_check_sensors"] = [s for s in SENSOR_NAMES if flat_rate[s] / max(n_win, 1) < 0.01]

    # Calibration on held-out train engines (full windows only).
    full = (idxc["cycle"] >= WINDOW).to_numpy()
    Xcal = Xc[full]
    truth = idxc["rul_clipped"].to_numpy()[full]
    rul_true = idxc["rul"].to_numpy()[full]
    tp = np.stack([t.predict(Xcal.astype("float32")) for t in rf.estimators_], axis=0)
    p50 = np.median(tp, axis=0)
    resid = truth - p50
    edges = CALIB_EDGES
    g10, g90 = float(np.percentile(resid, 10)), float(np.percentile(resid, 90))
    q10, q90, counts = [], [], []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p50 >= lo) & (p50 < hi)
        counts.append(int(m.sum()))
        if m.sum() >= 50:
            q10.append(float(np.percentile(resid[m], 10)))
            q90.append(float(np.percentile(resid[m], 90)))
        else:
            q10.append(g10)
            q90.append(g90)
    meta["calibration"] = {"edges": edges, "q10": q10, "q90": q90, "n_per_band": counts,
                           "note": "residual = true RUL (clipped at 125) minus tree median, on held-out train engines"}

    raw = -iforest.score_samples(Xcal[:, cols])
    lo, hi = float(np.percentile(raw, 1)), float(np.percentile(raw, 99))
    norm = np.clip((raw - lo) / max(hi - lo, 1e-9), 0.0, 1.0)
    label = rul_true <= LATE_LIFE
    meta["anomaly_norm"] = {"lo": lo, "hi": hi}
    meta["anomaly_threshold"] = _pick_threshold(norm, label)
    meta["model_version"] = MODEL_VERSION
    meta["seed"] = seed
    meta["fit_units"] = fit_units
    meta["calib_units"] = calib_units
    meta["data_source"] = source
    meta["sklearn_version"] = sklearn.__version__
    meta["n_estimators"] = n_estimators
    meta["min_samples_leaf"] = min_leaf
    meta["window"] = WINDOW

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(rf, ARTIFACT_DIR / "rul_rf.joblib", compress=3)
    joblib.dump(iforest, ARTIFACT_DIR / "anomaly_if.joblib", compress=3)
    (ARTIFACT_DIR / "health_meta.json").write_text(json.dumps(meta, indent=1, sort_keys=True))
    reload_artifacts()
    print(f"trained in {time.time() - t0:.1f}s on {len(fit_units)} units, calibrated on {len(calib_units)}", flush=True)
    return evaluate_and_write()


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------
def _prf(pred: np.ndarray, label: np.ndarray) -> dict:
    tp = float((pred & label).sum())
    fp = float((pred & ~label).sum())
    fn = float((~pred & label).sum())
    tn = float((~pred & ~label).sum())
    return {
        "precision": round(tp / max(tp + fp, 1.0), 4),
        "recall": round(tp / max(tp + fn, 1.0), 4),
        "fpr": round(fp / max(fp + tn, 1.0), 4),
        "n_positive": int(tp + fn), "n_negative": int(fp + tn),
    }


def nasa_score(pred: np.ndarray, true: np.ndarray) -> float:
    d = pred - true
    return float(np.where(d < 0, np.exp(-d / 13.0) - 1.0, np.exp(d / 10.0) - 1.0).sum())


def evaluate_and_write(seed: int = SEED) -> dict:
    bundle: Optional[_Bundle] = _bundle()
    if bundle is None:
        raise RuntimeError("no trained artifacts; run: python -m app.ml.train")
    _train, test, _fit, _calib, source = load_frames(seed)
    X, idx = featurize(test)
    pred = predict_windows(bundle, X)

    last_pos = idx.groupby("unit")["cycle"].transform("max").eq(idx["cycle"]).to_numpy()
    true = idx["rul"].to_numpy().astype("float64")
    true_c = idx["rul_clipped"].to_numpy().astype("float64")
    p50 = pred["p50"]
    ll = last_pos
    err = p50[ll] - true[ll]
    err_c = p50[ll] - true_c[ll]
    inside_tree = (true_c[ll] >= pred["p10_tree"][ll]) & (true_c[ll] <= pred["p90_tree"][ll])
    inside_cal = (true_c[ll] >= pred["p10"][ll]) & (true_c[ll] <= pred["p90"][ll])

    full = (idx["cycle"] >= WINDOW).to_numpy()
    label = true <= LATE_LIFE
    anom_all = _prf(pred["is_anomaly"][full], label[full])
    anom_last = _prf(pred["is_anomaly"][ll], label[ll])
    late = full & label
    in_tree_all = (true_c[full] >= pred["p10_tree"][full]) & (true_c[full] <= pred["p90_tree"][full])
    in_cal_all = (true_c[full] >= pred["p10"][full]) & (true_c[full] <= pred["p90"][full])

    ev = {
        "model_version": MODEL_VERSION,
        "data_source": source,
        "seed": seed,
        "n_test_engines": int(idx["unit"].nunique()),
        "n_test_windows": int(full.sum()),
        "n_fit_units": len(bundle.meta["fit_units"]),
        "n_calib_units": len(bundle.meta["calib_units"]),
        "n_estimators": bundle.meta["n_estimators"],
        "sklearn_version": bundle.meta["sklearn_version"],
        # RUL, last window of every test engine, against the true RUL file (unclipped)
        "rul_mae": round(float(np.abs(err).mean()), 2),
        "rul_rmse": round(float(np.sqrt((err ** 2).mean())), 2),
        "rul_bias": round(float(err.mean()), 2),
        "nasa_score": round(nasa_score(p50[ll], true[ll]), 1),
        # Same, against the truth clipped at 125 (the training target)
        "rul_mae_vs_clipped_truth": round(float(np.abs(err_c).mean()), 2),
        "rul_rmse_vs_clipped_truth": round(float(np.sqrt((err_c ** 2).mean())), 2),
        # Late-life windows (true RUL <= 30), every full window of every test engine
        "rul_mae_late_life": round(float(np.abs(p50[late] - true[late]).mean()), 2) if late.any() else None,
        "n_late_life_windows": int(late.sum()),
        # Does [p10, p90] contain the truth? Nominal 80%.
        "rul_interval_coverage_tree_last": round(float(inside_tree.mean()), 3),
        "rul_interval_coverage_calibrated_last": round(float(inside_cal.mean()), 3),
        "rul_interval_coverage_tree": round(float(in_tree_all.mean()), 3),
        "rul_interval_coverage_calibrated": round(float(in_cal_all.mean()), 3),
        "rul_interval_width_tree": round(float((pred["p90_tree"] - pred["p10_tree"])[full].mean()), 1),
        "rul_interval_width_calibrated": round(float((pred["p90"] - pred["p10"])[full].mean()), 1),
        # Anomaly vs the label "true RUL <= 30", every full test window
        "anomaly_precision": anom_all["precision"],
        "anomaly_recall": anom_all["recall"],
        "anomaly_fpr": anom_all["fpr"],
        "anomaly_n_positive_windows": anom_all["n_positive"],
        "anomaly_n_negative_windows": anom_all["n_negative"],
        "anomaly_threshold": bundle.meta["anomaly_threshold"],
        # Same, last window only
        "anomaly_precision_last_window": anom_last["precision"],
        "anomaly_recall_last_window": anom_last["recall"],
        "anomaly_fpr_last_window": anom_last["fpr"],
        "label": "abnormal = true RUL <= 30 cycles",
        "notes": [
            "Metrics are on held-out test engines that no model was fitted or calibrated on.",
            "RUL bands are tree spread widened by residuals from held-out calibration engines; "
            "coverage above is measured, not assumed.",
            "Not validated on airline data.",
        ],
    }
    (ARTIFACT_DIR / "eval.json").write_text(json.dumps(ev, indent=1))
    return ev


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--eval-only", action="store_true")
    ap.add_argument("--trees", type=int, default=N_TREES)
    ap.add_argument("--min-leaf", type=int, default=MIN_LEAF)
    a = ap.parse_args()
    out = evaluate_and_write() if a.eval_only else train(n_estimators=a.trees, min_leaf=a.min_leaf)
    print(json.dumps(out, indent=1))
    sys.exit(0)
