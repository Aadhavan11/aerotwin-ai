"""Predictive health intelligence (feature 3.1).

Public interface (CONTRACTS.md section 5):

    score_engine(tail, component_id, window)            -> HealthScore
    score_generic(kind, tail, component_id, window)     -> HealthScore
    score_component(kind, tail, component_id, window)   -> HealthScore   (dispatch)
    evaluate(force=False)                               -> dict          (eval.json)

Models (trained by ``app.ml.train``, artifacts committed under ``artifacts/``):

* RUL: RandomForestRegressor on the last-30-cycle window features, target RUL
  clipped at 125. p10/p50/p90 are the 10th/50th/90th percentile of the
  per-tree predictions, then widened by the empirical residuals measured on
  held-out calibration engines (see ``health_meta.json``). Neither is a
  guaranteed interval.
* Anomaly: IsolationForest fitted on healthy-phase windows (first 60% of each
  engine's life); raw score mapped to 0..1 with percentiles from the
  calibration engines; threshold chosen there too.
* Non-engine components: a rule on one wear signal against a limit plus a
  small IsolationForest on (level, slope). Rule-based demo models.

If the artifacts are missing the engine scorer falls back to ``rule-v1``
(z-scores of T50, Nc, Ps30) and says so in ``data_quality``.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np

from app.contracts.models import DataQuality, HealthScore, RulEstimate, TelemetryRow
from app.ml.sensors import GENERIC, SENSOR_NAMES, kind_from_component_id

log = logging.getLogger(__name__)

SEED = 1234
WINDOW = 30
MIN_SLOPE_ROWS = 10      # a slope from fewer rows is noise; use 0 instead
RUL_CAP = 125
ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts"
MODEL_VERSION = "rf-iforest-v1"
RULE_VERSION = "rule-v1"
GENERIC_VERSION = "rule-demo-v1"
N_SENS = len(SENSOR_NAMES)


# --------------------------------------------------------------------------
# Window features (one implementation for training and serving)
# --------------------------------------------------------------------------
def window_features(values: np.ndarray, cycles: np.ndarray, win: int = WINDOW) -> np.ndarray:
    """Features for every prefix position of one engine's series.

    ``values`` is (L, S), ``cycles`` is (L,). Row ``i`` of the result describes
    the window of the last ``win`` rows ending at ``i`` (fewer at the start):
    ``[mean_s..., slope_s..., last_s..., cycle]``. Slope is the least-squares
    slope per cycle; windows with one row get slope 0.
    """
    values = np.asarray(values, dtype="float64")
    t = np.asarray(cycles, dtype="float64")
    L, S = values.shape
    v0 = values[0].copy()
    t0 = t[0]
    vc = values - v0                 # centre before summing: avoids cancellation
    tc = t - t0

    def prefix(a: np.ndarray) -> np.ndarray:
        return np.concatenate([np.zeros((1,) + a.shape[1:]), np.cumsum(a, axis=0)])

    Sx, St = prefix(vc), prefix(tc)
    Stx, Stt = prefix(vc * tc[:, None]), prefix(tc * tc)
    end = np.arange(1, L + 1)
    start = np.maximum(0, end - win)
    n = (end - start).astype("float64")
    sx, st = Sx[end] - Sx[start], St[end] - St[start]
    stx, stt = Stx[end] - Stx[start], Stt[end] - Stt[start]
    mean = sx / n[:, None] + v0
    denom = n * stt - st * st
    ok = (denom > 1e-9) & (n >= MIN_SLOPE_ROWS)
    safe = np.where(ok, denom, 1.0)
    slope = np.where(ok[:, None], (n[:, None] * stx - st[:, None] * sx) / safe[:, None], 0.0)
    return np.concatenate([mean, slope, values, t[:, None]], axis=1)


def feature_names() -> list[str]:
    return ([f"{s}_mean" for s in SENSOR_NAMES] + [f"{s}_slope" for s in SENSOR_NAMES]
            + [f"{s}_last" for s in SENSOR_NAMES] + ["cycle"])


def anomaly_columns() -> np.ndarray:
    """Feature columns the anomaly model sees: everything except the cycle count."""
    return np.arange(3 * N_SENS)


# --------------------------------------------------------------------------
# Artifact loading
# --------------------------------------------------------------------------
class _Bundle:
    def __init__(self, rf, iforest, meta: dict):
        self.rf, self.iforest, self.meta = rf, iforest, meta
        self.importance = np.asarray(rf.feature_importances_, dtype="float64")
        self.sensor_importance = np.array(
            [self.importance[[i, N_SENS + i, 2 * N_SENS + i]].sum() for i in range(N_SENS)]
        )
        self.healthy_mean = np.array([meta["healthy_mean"][s] for s in SENSOR_NAMES])
        self.healthy_std = np.array([meta["healthy_std"][s] for s in SENSOR_NAMES])


@lru_cache(maxsize=1)
def _bundle() -> Optional[_Bundle]:
    try:
        import joblib

        meta = json.loads((ARTIFACT_DIR / "health_meta.json").read_text())
        rf = joblib.load(ARTIFACT_DIR / "rul_rf.joblib")
        iforest = joblib.load(ARTIFACT_DIR / "anomaly_if.joblib")
        return _Bundle(rf, iforest, meta)
    except Exception as exc:  # missing or incompatible artifacts: fall back to the rule
        log.warning("ML artifacts unavailable (%s); using %s", exc, RULE_VERSION)
        return None


def reload_artifacts() -> None:
    _bundle.cache_clear()
    _generic_models.cache_clear()


def model_available() -> bool:
    return _bundle() is not None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _last_rows(window: Sequence[TelemetryRow]) -> list[TelemetryRow]:
    rows = sorted(window, key=lambda r: (r.cycle, r.ts))
    return rows[-WINDOW:]


def _matrix(rows: Sequence[TelemetryRow], keys: Sequence[str]) -> np.ndarray:
    out = np.full((len(rows), len(keys)), np.nan)
    for i, r in enumerate(rows):
        for j, k in enumerate(keys):
            v = r.values.get(k)
            if v is not None:
                try:
                    out[i, j] = float(v)
                except (TypeError, ValueError):
                    pass
    return out


def _clip01(x: float) -> float:
    return float(min(1.0, max(0.0, x)))


def _pct(a: np.ndarray, q: float) -> float:
    return float(np.percentile(a, q))


def _combine_health(rul_p50: float, anomaly: float) -> float:
    """health = clip(0.6 * clip(rul_p50 / 125, 0, 1) + 0.4 * (1 - anomaly_score), 0, 1)."""
    return _clip01(0.6 * _clip01(rul_p50 / RUL_CAP) + 0.4 * (1.0 - anomaly))


def _empty_score(tail: str, component_id: str, reasons: list[str], version: str) -> HealthScore:
    """Used when a window cannot be scored at all (status 'bad')."""
    return HealthScore(
        tail=tail, component_id=component_id, health=0.5, anomaly_score=0.0, is_anomaly=False,
        rul_cycles=None, data_quality=DataQuality(status="bad", reasons=reasons),
        model_version=version, top_signals=[],
    )


# --------------------------------------------------------------------------
# Engine scoring
# --------------------------------------------------------------------------
def _engine_quality(rows: Sequence[TelemetryRow], mat: np.ndarray, meta: Optional[dict]) -> tuple[DataQuality, np.ndarray]:
    """Returns (quality, matrix with missing values imputed)."""
    reasons: list[str] = []
    status = "ok"
    n = len(rows)
    missing = np.isnan(mat)
    # A sensor is "missing" if it is absent/NaN in the latest row.
    latest_missing = missing[-1]
    if latest_missing.mean() > 0.5:
        return DataQuality(status="bad", reasons=[f"{int(latest_missing.sum())} of {N_SENS} sensors missing"]), mat
    if n < WINDOW:
        reasons.append(f"window has {n} of {WINDOW} rows")
        status = "degraded"
    if latest_missing.any():
        names = [SENSOR_NAMES[i] for i in np.where(latest_missing)[0]]
        reasons.append("missing " + ", ".join(names) + " (imputed)")
        status = "degraded"
    # Impute: column mean of the window, else the healthy population mean.
    filled = mat.copy()
    base = (np.array([meta["healthy_mean"][s] for s in SENSOR_NAMES]) if meta
            else np.array([_RULE_BASE[s][0] for s in SENSOR_NAMES]))
    for j in range(N_SENS):
        col = filled[:, j]
        if np.isnan(col).any():
            fill = np.nanmean(col) if (~np.isnan(col)).any() else base[j]
            col[np.isnan(col)] = fill
    # Flat sensors over a full window.
    if n >= 10:
        check = meta["flat_check_sensors"] if meta else SENSOR_NAMES
        flat = [s for s in check if np.nanstd(mat[:, SENSOR_NAMES.index(s)]) == 0 and not missing[:, SENSOR_NAMES.index(s)].any()]
        if flat:
            reasons.append("sensor " + ", ".join(flat) + f" flat for {n} cycles")
            status = "degraded"
    # Out of training range (10% margin).
    if meta:
        out = []
        for j, s in enumerate(SENSOR_NAMES):
            lo, hi = meta["ranges"][s]
            m = 0.1 * (hi - lo)
            v = filled[-1, j]
            if v < lo - m or v > hi + m:
                out.append(s)
        if out:
            reasons.append("value out of training range: " + ", ".join(out))
            status = "degraded"
    return DataQuality(status=status, reasons=reasons), filled


_RULE_BASE = {  # healthy mean, spread: measured on train_FD001 (see ml.cmapss)
    "T24": (642.374, 0.2518), "T30": (1586.8, 2.542), "T50": (1402.65, 4.483), "P30": (553.958, 0.5032),
    "Nf": (2388.06, 0.04667), "Nc": (9055.99, 7.495), "Ps30": (47.35, 0.1484), "phi": (521.921, 0.4333),
    "NRf": (2388.06, 0.04729), "NRc": (8137.33, 7.451), "BPR": (8.41804, 0.01848), "htBleed": (392.253, 0.7497),
    "W31": (38.9355, 0.09007), "W32": (23.3621, 0.05346),
}
# Direction the sensor moves toward failure in FD001 (mean shift at end of life, sign only).
_RULE_SIGN = {"T24": 1, "T30": 1, "T50": 1, "P30": -1, "Nf": 1, "Nc": 1, "Ps30": 1, "phi": -1,
              "NRf": 1, "NRc": 1, "BPR": 1, "htBleed": 1, "W31": -1, "W32": -1}


def _rule_engine(tail: str, component_id: str, rows: Sequence[TelemetryRow], mat: np.ndarray) -> HealthScore:
    """rule-v1: z-scores of T50, Nc, Ps30 against the healthy population. RUL is a rough linear map."""
    quality, filled = _engine_quality(rows, mat, None)
    if quality.status == "bad":
        return _empty_score(tail, component_id, quality.reasons, RULE_VERSION)
    keys = ["T50", "Nc", "Ps30"]
    zs = []
    for k in keys:
        mu, sd = _RULE_BASE[k]
        zs.append(_RULE_SIGN[k] * (filled[-WINDOW:, SENSOR_NAMES.index(k)].mean() - mu) / max(sd, 1e-9))
    z = float(np.mean(zs))
    # Healthy engines sit around z=0..2 (engine-to-engine spread); end of life is around z=5.
    wear = _clip01(z / 5.5)
    anomaly = _clip01((z - 2.0) / 3.0)
    health = _clip01(1.0 - wear)
    p50 = RUL_CAP * health
    rul = RulEstimate(p10=round(0.6 * p50, 1), p50=round(p50, 1), p90=round(min(RUL_CAP * 1.0, 1.4 * p50), 1))
    quality = DataQuality(status="degraded", reasons=[*quality.reasons, "rule-based fallback model (artifacts missing)"])
    zabs = sorted(zip(keys, np.abs(zs)), key=lambda kv: -kv[1])
    return HealthScore(
        tail=tail, component_id=component_id, health=round(health, 4), anomaly_score=round(anomaly, 4),
        is_anomaly=bool(anomaly >= 0.5), rul_cycles=rul, data_quality=quality, model_version=RULE_VERSION,
        top_signals=[k for k, _ in zabs[:3]],
    )


def _tree_preds(rf, X: np.ndarray) -> np.ndarray:
    """(n_trees, n_samples) matrix of individual tree predictions."""
    X32 = np.asarray(X, dtype="float32")
    return np.stack([t.predict(X32) for t in rf.estimators_], axis=0)


def widen(p10: np.ndarray, p50: np.ndarray, p90: np.ndarray, calib: dict) -> tuple[np.ndarray, np.ndarray]:
    """Widen the tree-spread band with residual quantiles measured on calibration engines.

    Residuals (true clipped RUL minus p50) are summarised per band of predicted
    RUL, because error grows with remaining life. A band is only ever widened.
    """
    edges = calib["edges"]
    idx = np.clip(np.searchsorted(edges, p50, side="right") - 1, 0, len(edges) - 2)
    q10 = np.asarray(calib["q10"])[idx]
    q90 = np.asarray(calib["q90"])[idx]
    lo = np.minimum(p10, p50 + q10)
    hi = np.maximum(p90, p50 + q90)
    return np.maximum(lo, 0.0), np.minimum(np.maximum(hi, p50), float(RUL_CAP))


def predict_windows(bundle: _Bundle, X: np.ndarray) -> dict:
    """Batch prediction on feature rows. Used by serving, training and evaluation."""
    meta = bundle.meta
    tp = _tree_preds(bundle.rf, X)
    p10_t, p50, p90_t = np.percentile(tp, [10, 50, 90], axis=0)
    p10, p90 = widen(p10_t, p50, p90_t, meta["calibration"])
    raw = -bundle.iforest.score_samples(np.asarray(X)[:, anomaly_columns()])
    lo, hi = meta["anomaly_norm"]["lo"], meta["anomaly_norm"]["hi"]
    anomaly = np.clip((raw - lo) / max(hi - lo, 1e-9), 0.0, 1.0)
    return {
        "p10_tree": p10_t, "p50": p50, "p90_tree": p90_t, "p10": p10, "p90": p90,
        "anomaly": anomaly, "is_anomaly": anomaly >= meta["anomaly_threshold"],
    }


def score_engine(tail: str, component_id: str, window: Sequence[TelemetryRow]) -> HealthScore:
    """Score one engine from its last <= 30 telemetry rows."""
    rows = _last_rows(window)
    if not rows:
        return _empty_score(tail, component_id, ["no telemetry rows"], MODEL_VERSION)
    mat = _matrix(rows, SENSOR_NAMES)
    bundle = _bundle()
    if bundle is None:
        return _rule_engine(tail, component_id, rows, mat)
    quality, filled = _engine_quality(rows, mat, bundle.meta)
    if quality.status == "bad":
        return _empty_score(tail, component_id, quality.reasons, MODEL_VERSION)
    cycles = np.array([r.cycle for r in rows], dtype="float64")
    X = window_features(filled, cycles)[-1:]
    pred = predict_windows(bundle, X)
    p10, p50, p90 = float(pred["p10"][0]), float(pred["p50"][0]), float(pred["p90"][0])
    p10, p90 = min(p10, p50), max(p90, p50)
    anomaly = float(pred["anomaly"][0])
    # Top signals: |z| of the window mean against the healthy population, weighted by RF importance.
    z = np.abs((X[0, :N_SENS] - bundle.healthy_mean) / np.maximum(bundle.healthy_std, 1e-9))
    contrib = z * bundle.sensor_importance
    order = np.argsort(-contrib)[:3]
    return HealthScore(
        tail=tail, component_id=component_id,
        health=round(_combine_health(p50, anomaly), 4), anomaly_score=round(anomaly, 4),
        is_anomaly=bool(pred["is_anomaly"][0]),
        rul_cycles=RulEstimate(p10=round(p10, 1), p50=round(p50, 1), p90=round(p90, 1)),
        data_quality=quality, model_version=MODEL_VERSION, top_signals=[SENSOR_NAMES[i] for i in order],
    )


# --------------------------------------------------------------------------
# Non-engine components (rule-based demo models)
# --------------------------------------------------------------------------
@lru_cache(maxsize=None)
def _generic_models(kind: str):
    """A small IsolationForest per kind, fitted on synthetic healthy windows. Deterministic."""
    from sklearn.ensemble import IsolationForest

    spec = GENERIC[kind]
    rng = np.random.default_rng(SEED + sum(map(ord, kind)))
    span = abs(spec.limit - spec.healthy)
    sign = 1.0 if spec.worsens == "up" else -1.0
    rows = []
    for _ in range(400):
        level = spec.healthy + sign * rng.uniform(0.0, 0.35) * span          # healthy range: first third of life
        slope = sign * abs(rng.normal(0.0, 0.0006 * span))
        rows.append([level, slope])
    iforest = IsolationForest(n_estimators=100, random_state=SEED).fit(np.array(rows))
    raw = -iforest.score_samples(np.array(rows))
    return iforest, float(np.percentile(raw, 5)), float(np.percentile(raw, 99.5) + 0.25)


def score_generic(kind: str, tail: str, component_id: str, window: Sequence[TelemetryRow]) -> HealthScore:
    """Rule-based demo model for brakes, landing gear, hydraulics and avionics.

    health: the wear signal mapped linearly from ``healthy`` (1.0) to ``limit`` (0.0).
    RUL: remaining distance to the limit divided by the least-squares slope of
    the signal over the window, with +/- 1.28 standard errors for p10/p90.
    Anomaly: IsolationForest on (level, slope).
    """
    spec = GENERIC.get(kind)
    rows = _last_rows(window)
    if spec is None:
        return _empty_score(tail, component_id, [f"no model for kind {kind!r}"], GENERIC_VERSION)
    if not rows:
        return _empty_score(tail, component_id, ["no telemetry rows"], GENERIC_VERSION)
    keys = [spec.wear_signal, *spec.extras]
    mat = _matrix(rows, keys)
    reasons: list[str] = []
    status = "ok"
    if np.isnan(mat[-1, 0]):
        return _empty_score(tail, component_id, [f"{spec.wear_signal} missing"], GENERIC_VERSION)
    if len(rows) < WINDOW:
        reasons.append(f"window has {len(rows)} of {WINDOW} rows")
        status = "degraded"
    x = mat[:, 0]
    valid = ~np.isnan(x)
    if valid.sum() < len(x):
        reasons.append(f"{int((~valid).sum())} missing {spec.wear_signal} values skipped")
        status = "degraded"
    t = np.array([r.cycle for r in rows], dtype="float64")[valid]
    xv = x[valid]
    if len(xv) >= 10 and np.std(xv) == 0:
        reasons.append(f"sensor {spec.wear_signal} flat for {len(xv)} cycles")
        status = "degraded"
    span = spec.limit - spec.healthy
    level = float(xv[-1])
    health = _clip01(1.0 - (level - spec.healthy) / span)
    if len(xv) >= 3 and np.ptp(t) > 0:
        tc = t - t.mean()
        slope = float((tc * (xv - xv.mean())).sum() / (tc * tc).sum())
        resid = xv - (xv.mean() + slope * tc)
        se = float(math.sqrt((resid ** 2).sum() / max(len(xv) - 2, 1) / (tc * tc).sum()))
    else:
        slope, se = 0.0, 0.0
    toward = 1.0 if span > 0 else -1.0                    # sign of "toward the limit"
    remaining = max((spec.limit - level) * toward, 0.0)
    def cycles_left(rate: float) -> float:
        r = rate * toward
        return float(RUL_CAP * 4) if r <= 1e-12 else min(remaining / r, RUL_CAP * 4.0)
    p50 = cycles_left(slope)
    p10 = cycles_left(slope + 1.2816 * se * toward)       # faster wear -> fewer cycles
    p90 = cycles_left(slope - 1.2816 * se * toward)
    p10, p90 = min(p10, p50), max(p90, p50)
    iforest, lo, hi = _generic_models(kind)
    raw = float(-iforest.score_samples(np.array([[level, slope]]))[0])
    anomaly = _clip01((raw - lo) / max(hi - lo, 1e-9))
    return HealthScore(
        tail=tail, component_id=component_id, health=round(health, 4), anomaly_score=round(anomaly, 4),
        is_anomaly=bool(anomaly >= 0.75), rul_cycles=RulEstimate(p10=round(p10, 1), p50=round(p50, 1), p90=round(p90, 1)),
        data_quality=DataQuality(status=status, reasons=reasons), model_version=GENERIC_VERSION,
        top_signals=[spec.wear_signal],
    )


def score_component(kind: Optional[str], tail: str, component_id: str, window: Sequence[TelemetryRow]) -> HealthScore:
    kind = kind or kind_from_component_id(component_id)
    if kind == "engine":
        return score_engine(tail, component_id, window)
    return score_generic(kind, tail, component_id, window)


# --------------------------------------------------------------------------
# Evaluation and the /metrics ML block
# --------------------------------------------------------------------------
def evaluate(force: bool = False) -> dict:
    """Held-out metrics, cached to ``artifacts/eval.json``. Run ``python -m app.ml.train`` to create it."""
    path = ARTIFACT_DIR / "eval.json"
    if path.is_file() and not force:
        return json.loads(path.read_text())
    from app.ml import train

    return train.evaluate_and_write()


def ml_metrics_block() -> dict:
    """The ``ml`` block of GET /metrics (keys fixed by CONTRACTS.md)."""
    try:
        e = evaluate()
    except Exception as exc:
        log.warning("ml metrics unavailable: %s", exc)
        return {"status": "in progress", "reason": str(exc)}
    return {
        "rul_mae": e["rul_mae"], "rul_rmse": e["rul_rmse"],
        "anomaly_precision": e["anomaly_precision"], "anomaly_recall": e["anomaly_recall"],
        "anomaly_fpr": e["anomaly_fpr"], "n_test": e["n_test_engines"], "data_source": e["data_source"],
        "model_version": e["model_version"],
        "rul_interval_coverage": e.get("rul_interval_coverage_calibrated"),
    }
