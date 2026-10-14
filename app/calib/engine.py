"""Fit the v2 engine surrogate to NASA C-MAPSS FD001-FD004 training data (``python -m app.calib.engine``).

Method (see docs/SIM-V2-ENGINE.md): per operating condition, sensors are normalised (z = (raw - mu_c) / sd_c, as is
standard for FD002/FD004). Each training engine is regressed on a small polynomial basis of its life fraction
d = cycle / life; the per-engine coefficient vectors give a mean curve, a low-rank engine-to-engine covariance
(PCA) and a diagonal remainder; pooled residuals give the per-sensor noise sigma. Cycles-to-failure is fitted as
a lognormal. The result is a small JSON file committed next to the model.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import numpy as np

from app.ml.sensors import COLUMN_TO_NAME, RAW_COLUMNS, SENSOR_NAMES

FIT_PATH = Path(__file__).resolve().parents[1] / "sim" / "v2" / "calibration" / "engine_fit.json"
DATA_DIR = Path(os.environ.get("AEROTWIN_CMAPSS_DIR") or Path(__file__).resolve().parents[2] / "data" / "external" / "nasa_cmapss" / "extracted")
VARIANTS = {"fd001": (1, False), "fd002": (2, True), "fd003": (3, False), "fd004": (4, True)}
N_COND = 6
BASIS_POWERS = (0, 1, 2, 4)  # basis(d) = [d**k for k in BASIS_POWERS]
N_FACTORS = 4
HEALTHY_FRAC = 0.15


def basis(d: np.ndarray | float) -> np.ndarray:
    d = np.asarray(d, dtype=float)
    return np.stack([d**k for k in BASIS_POWERS], axis=-1)


def load_fit(path: Path | None = None) -> dict[str, Any]:
    """Load the committed fit (no data needed)."""
    return json.loads(Path(path or FIT_PATH).read_text())


def load_train(fd: int, data_dir: Path | None = None) -> Any:
    import pandas as pd

    path = Path(data_dir or DATA_DIR) / f"train_FD00{fd}.txt"
    if not path.is_file():
        raise FileNotFoundError(path)
    df = pd.read_csv(path, sep=r"\s+", header=None, names=RAW_COLUMNS).rename(columns=COLUMN_TO_NAME)
    df = df[["unit", "cycle", "op1", "op2", "op3", *SENSOR_NAMES]].sort_values(["unit", "cycle"]).reset_index(drop=True)
    df["life"] = df.groupby("unit")["cycle"].transform("max")
    df["d"] = df["cycle"] / df["life"]
    df["rul"] = df["life"] - df["cycle"]
    return df


def assign_conditions(df: Any, centers: np.ndarray | None, scale: np.ndarray | None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cluster op settings into N_COND conditions (or reuse centers). Returns (cond, centers, scale)."""
    ops = df[["op1", "op2", "op3"]].to_numpy(float)
    if centers is None:
        from sklearn.cluster import KMeans

        scale = ops.std(axis=0) + 1e-9
        km = KMeans(n_clusters=N_COND, n_init=10, random_state=0).fit(ops / scale)
        order = np.argsort(km.cluster_centers_[:, 0])
        centers = km.cluster_centers_[order] * scale
    cond = np.argmin((((ops / scale)[:, None, :] - (centers / scale)[None]) ** 2).sum(-1), axis=1)
    return cond, centers, scale


def fit_variant(df: Any, multi: bool) -> dict[str, Any]:
    n_sens = len(SENSOR_NAMES)
    X = df[SENSOR_NAMES].to_numpy(float)
    if multi:
        cond, centers, scale = assign_conditions(df, None, None)
    else:
        cond, centers, scale = np.zeros(len(df), dtype=int), df[["op1", "op2", "op3"]].to_numpy(float).mean(0, keepdims=True), np.ones(3)
    ncond = N_COND if multi else 1
    mu = np.zeros((N_COND, n_sens))
    sd = np.ones((N_COND, n_sens))
    healthy = df["d"].to_numpy() <= HEALTHY_FRAC
    for c in range(ncond):
        m = cond == c
        mu[c] = X[m & healthy].mean(0)
        sd[c] = np.maximum(X[m].std(0), 1e-9)
    for c in range(ncond, N_COND):
        mu[c], sd[c] = mu[0], sd[0]
    Z = (X - mu[cond]) / sd[cond]
    units = df["unit"].to_numpy()
    d = df["d"].to_numpy()
    B = basis(d)
    coefs, resid_ss, dof, xtx_inv = [], np.zeros(n_sens), 0, []
    for u in np.unique(units):
        m = units == u
        beta, *_ = np.linalg.lstsq(B[m], Z[m], rcond=None)  # (nb, S)
        coefs.append(beta.T.reshape(-1))  # sensor-major: index s * nb + k
        r = Z[m] - B[m] @ beta
        resid_ss += (r**2).sum(0)
        dof += int(m.sum()) - B.shape[1]
        xtx_inv.append(np.linalg.pinv(B[m].T @ B[m]))
    U = np.asarray(coefs)
    mean = U.mean(0)
    sig2 = resid_ss / max(dof, 1)
    nb = B.shape[1]
    # remove per-engine OLS estimation noise (independent across sensors) from the coefficient covariance
    cov = np.cov(U.T)
    est = np.mean(xtx_inv, axis=0)
    for si in range(n_sens):
        cov[si * nb : (si + 1) * nb, si * nb : (si + 1) * nb] -= sig2[si] * est
    w, V = np.linalg.eigh((cov + cov.T) / 2)
    order = np.argsort(w)[::-1]
    w, V = np.maximum(w[order], 0.0), V[:, order]
    k = N_FACTORS
    factors = V[:, :k].T * np.sqrt(w[:k])[:, None]  # (k, S*nb): u = mean + g @ factors + diag * e
    diag = np.sqrt(np.maximum(np.diag(cov) - (factors**2).sum(0), 0.0))
    life = df.groupby("unit")["life"].first().to_numpy(float)
    res = resolution(df)
    quant_var = (res / sd[0]) ** 2 / 12.0  # rounding noise already inside the pooled residual (z units, cond-0 scale)
    noise = np.sqrt(np.maximum(sig2 - quant_var, 0.0))
    freq = np.bincount(cond, minlength=N_COND) / len(cond)
    return {
        "n_engines": int(len(U)),
        "n_conditions": int(ncond),
        "op_centers": centers.tolist(),
        "op_scale": np.asarray(scale).tolist(),
        "cond_freq": freq.tolist(),
        "mu": mu.tolist(),
        "sd": sd.tolist(),
        "coef_mean": mean.reshape(n_sens, -1).tolist(),
        "coef_factors": factors.reshape(k, n_sens, -1).tolist(),
        "coef_diag": diag.reshape(n_sens, -1).tolist(),
        "noise_sigma": noise.tolist(),
        "resolution": res.tolist(),
        "life_mean": float(life.mean()),
        "life_sd": float(life.std(ddof=1)),
        "life_log_mu": float(np.log(life).mean()),
        "life_log_sigma": float(np.log(life).std(ddof=1)),
        "life_min": float(life.min()),
        "life_max": float(life.max()),
    }


def resolution(df: Any) -> np.ndarray:
    """Sensor quantisation step: smallest gap between distinct recorded values, snapped to a power of ten."""
    out = []
    for s in SENSOR_NAMES:
        u = np.unique(df[s].to_numpy(float))
        step = float(np.diff(u).min()) if u.size > 1 else 0.0
        out.append(10.0 ** np.round(np.log10(step)) if step > 0 else 0.0)
    return np.asarray(out)


def egt_gain(df: Any) -> float:
    """Regress T50 (degR) on life fraction near end of life: degR of T50 rise at failure, healthy-phase engine mean removed."""
    d = df["d"].to_numpy()
    t = df["T50"].to_numpy(float)
    return float(t[d >= 0.95].mean() - t[d <= HEALTHY_FRAC].mean())


def fit_all(data_dir: Path | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"sensors": list(SENSOR_NAMES), "basis_powers": list(BASIS_POWERS), "source": "NASA C-MAPSS train_FD001..FD004 (Saxena & Goebel 2008)", "variants": {}}
    for name, (fd, multi) in VARIANTS.items():
        df = load_train(fd, data_dir)
        v = fit_variant(df, multi)
        v["t50_rise_degR"] = egt_gain(df)
        out["variants"][name] = v
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=FIT_PATH)
    ap.add_argument("--validate", action="store_true", help="run the simulated-vs-real validation and print JSON")
    a = ap.parse_args(argv)
    if a.validate:
        from app.calib.engine_validate import run_validation

        print(json.dumps(run_validation(a.data_dir), indent=2))
        return 0
    fit = fit_all(a.data_dir)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(fit, separators=(",", ":"), default=float) + "\n")
    for k, v in fit["variants"].items():
        print(f"{k}: engines={v['n_engines']} life={v['life_mean']:.1f}+-{v['life_sd']:.1f} conds={v['n_conditions']}")
    print(f"wrote {a.out} ({a.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
