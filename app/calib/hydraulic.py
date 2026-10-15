"""Calibrate the v2 hydraulics model against the UCI hydraulic test rig (``python -m app.calib.hydraulic``).

Data: UCI "Condition monitoring of hydraulic systems" (ZeMA, CC BY 4.0), 2205 cycles of 60 s with cooler / valve /
pump-leakage / accumulator condition labels. Treated as untrusted numeric data (numpy/pandas only).

What this can and cannot do (see docs/SIM-V2-CALIBRATION.md): the rig is a test bench, not an aircraft system, and its
condition labels are *stepped by experiment design*, so it yields (a) label-to-sensor sensitivities, (b) noise levels
and sensor resolutions, (c) which label levels are detectable. It carries no time-to-failure information.

Overrides are written to the fit JSON only under a documented rule: a model parameter is overridden when the rig
measures the same physical effect with a consistent sign and a bootstrap |t| >= 2; otherwise the prior stays and the
JSON records why. Scale translation is relative (sd / mean) for pressures and absolute (deg C) for temperatures.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
from collections.abc import Iterator
from dataclasses import fields
from pathlib import Path
from typing import Any

import numpy as np

FIT_PATH = Path(__file__).resolve().parents[1] / "sim" / "v2" / "calibration" / "hydraulics_fit.json"
DATA_DIR = Path(os.environ.get("AEROTWIN_HYDRAULIC_DIR") or Path(__file__).resolve().parents[2] / "data" / "external" / "uci_hydraulic")
SENSORS = ("PS1", "PS2", "PS3", "PS4", "PS5", "PS6", "EPS1", "FS1", "FS2", "TS1", "TS2", "TS3", "TS4", "VS1")
UNITS = {"PS": "bar", "EPS1": "W", "FS": "l/min", "TS": "degC", "VS1": "mm/s"}
LABELS = ("cooler", "valve", "leak", "accumulator")
# severity s in [0, 1]: 0 = healthy label value, 1 = "close to total failure" label value
SEVERITY_DOC = {"cooler": "(100 - label) / 97", "valve": "(100 - label) / 27", "leak": "label / 2", "accumulator": "(130 - label) / 40"}
DETECT_DPRIME = 3.0
N_BOOT = 300
T_APPLY = 2.0


def severities(profile: np.ndarray) -> np.ndarray:
    """(n, 4) severity matrix in LABELS order from profile.txt columns (cooler, valve, leak, accumulator)."""
    return np.column_stack([(100.0 - profile[:, 0]) / 97.0, (100.0 - profile[:, 1]) / 27.0, profile[:, 2] / 2.0, (130.0 - profile[:, 3]) / 40.0])


def load_fit(path: Path | None = None) -> dict[str, Any]:
    """Load the committed fit (no data needed)."""
    return json.loads(Path(path or FIT_PATH).read_text())


def data_available(data_dir: Path | None = None) -> bool:
    d = Path(data_dir or DATA_DIR)
    return (d / "profile.txt").is_file() and all((d / f"{s}.txt").is_file() for s in SENSORS)


def _read_matrix(path: Path) -> np.ndarray:
    import pandas as pd

    return pd.read_csv(path, sep="\t", header=None, dtype=float, engine="c").to_numpy()


def load_rig(data_dir: Path | None = None) -> dict[str, Any]:
    """Per-cycle labels plus, per sensor: cycle means, within-cycle high-frequency noise sd, recorded resolution."""
    d = Path(data_dir or DATA_DIR)
    if not data_available(d):
        raise FileNotFoundError(d)
    profile = np.loadtxt(d / "profile.txt")
    sensors: dict[str, Any] = {}
    for name in SENSORS:
        m = _read_matrix(d / f"{name}.txt")
        hf = np.diff(m, axis=1).std(axis=1) / np.sqrt(2.0)  # first-difference estimator: sensor + fast process noise
        u = np.unique(np.round(m[::7].ravel(), 6))
        gaps = np.diff(u)
        gaps = gaps[gaps > 1e-9]
        sensors[name] = {"mean": m.mean(axis=1), "hf_sd": float(np.median(hf)), "resolution": float(gaps.min()) if gaps.size else 0.0, "samples": int(m.shape[1])}
    return {"profile": profile, "sensors": sensors}


def _ols(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.linalg.lstsq(x, y, rcond=None)[0]


def _cells(profile: np.ndarray) -> np.ndarray:
    _, inv = np.unique(profile[:, :4], axis=0, return_inverse=True)
    return inv.ravel()


def _within_cell_sd(y: np.ndarray, cells: np.ndarray) -> float:
    ss, dof = 0.0, 0
    for c in np.unique(cells):
        v = y[cells == c]
        ss += float(((v - v.mean()) ** 2).sum())
        dof += v.size - 1
    return float(np.sqrt(ss / max(dof, 1)))


def _cell_sd_median(y: np.ndarray, cells: np.ndarray) -> float:
    """Median over label cells of the within-cell sd: robust to the few cells where a hidden rig regime jumps between cycles."""
    return float(np.median([y[cells == c].std(ddof=1) for c in np.unique(cells) if (cells == c).sum() > 1]))


def fit_sensitivities(rig: dict[str, Any], seed: int = 0) -> dict[str, Any]:
    """OLS of each sensor's cycle mean on the four label severities (stable cycles), cell-bootstrap standard errors."""
    profile = rig["profile"]
    stable = profile[:, 4] == 0
    sev = severities(profile)
    cells = _cells(profile)
    X = np.column_stack([np.ones(len(profile)), sev])
    xs, cs = X[stable], cells[stable]
    uc = np.unique(cs)
    members = {c: np.flatnonzero(cs == c) for c in uc}
    rng = np.random.default_rng(seed)
    boots = [np.concatenate([members[c] for c in rng.choice(uc, size=uc.size, replace=True)]) for _ in range(N_BOOT)]
    out: dict[str, Any] = {}
    for name in SENSORS:
        y = rig["sensors"][name]["mean"]
        ys = y[stable]
        beta = _ols(xs, ys)
        resid = ys - xs @ beta
        r2 = 1.0 - float(resid.var() / ys.var())
        bb = np.asarray([_ols(xs[ix], ys[ix]) for ix in boots])
        se = bb.std(axis=0, ddof=1)
        beta_all = _ols(X, y)
        out[name] = {
            "unit": UNITS.get(name, UNITS.get(name[:2], "")),
            "mean": float(ys.mean()),
            "intercept": float(beta[0]),
            "coef_per_full_severity": {k: float(v) for k, v in zip(LABELS, beta[1:])},
            "coef_se": {k: float(v) for k, v in zip(LABELS, se[1:])},
            "coef_all_cycles": {k: float(v) for k, v in zip(LABELS, beta_all[1:])},
            "r2": r2,
            "within_cell_sd": _within_cell_sd(ys, cs),
            "cell_sd_median": _cell_sd_median(ys, cs),
            "cell_rel_sd_median": _cell_sd_median(ys, cs) / abs(float(ys.mean())) if ys.mean() else None,
            "hf_noise_sd": rig["sensors"][name]["hf_sd"],
            "resolution": rig["sensors"][name]["resolution"],
            "samples_per_cycle": rig["sensors"][name]["samples"],
        }
    return out


def detectability(sens: dict[str, Any], profile: np.ndarray) -> dict[str, Any]:
    """For each label level: best sensor and standardised shift d' = |coef * s| / within-cell sd; first level with d' >= 3."""
    out: dict[str, Any] = {}
    sev = severities(profile)
    for j, lab in enumerate(LABELS):
        levels = sorted({round(float(s), 4) for s in sev[:, j] if s > 0})
        rows = []
        for s in levels:
            best = max(SENSORS, key=lambda n: abs(sens[n]["coef_per_full_severity"][lab]) * s / sens[n]["within_cell_sd"])
            dp = abs(sens[best]["coef_per_full_severity"][lab]) * s / sens[best]["within_cell_sd"]
            rows.append({"severity": s, "hi_equivalent": round(1.0 - s, 4), "best_sensor": best, "dprime": float(dp), "detectable": bool(dp >= DETECT_DPRIME)})
        first = next((r["hi_equivalent"] for r in rows if r["detectable"]), None)
        out[lab] = {"levels": rows, "first_detectable_hi": first}
    return out


def build_overrides(sens: dict[str, Any], prior: dict[str, float]) -> tuple[dict[str, float], dict[str, Any]]:
    """Apply the documented rule; return (overrides, per-parameter decision record)."""
    over: dict[str, float] = {}
    notes: dict[str, Any] = {}
    # The rig is an instrumented lab bench: its noise is taken as a lower bound for fielded sensors, so a rig value
    # above the prior raises the prior and a rig value below it leaves the prior unchanged.
    p_rel = sens["PS1"]["cell_rel_sd_median"]
    p_val = float(p_rel * prior["pressure_healthy"])
    over["pressure_noise"] = max(p_val, prior["pressure_noise"])
    notes["pressure_noise"] = {"applied": p_val > prior["pressure_noise"], "rig_rel_sd": p_rel, "rig_value_kpa": p_val, "prior": prior["pressure_noise"],
                               "rule": "PS1 median within-cell sd / mean times model healthy pressure; max(rig, prior)",
                               "caveat": "cycle-to-cycle spread inside one label cell; lab-grade, far below fielded-sensor noise, so the prior is kept"}
    t_sd = float(np.mean([sens[f"TS{i}"]["cell_sd_median"] for i in range(1, 5)]))
    over["temp_noise"] = max(t_sd, prior["temp_noise"])
    notes["temp_noise"] = {"applied": t_sd > prior["temp_noise"], "rig_sd_c": t_sd, "prior": prior["temp_noise"],
                           "rule": "mean of TS1-TS4 median within-cell sd, degC absolute; max(rig, prior)",
                           "caveat": "lab-grade rig temperatures are far quieter than the prior; the prior is kept as the fielded-sensor floor"}
    # heat_eta: fluid temperature rise per unit pump-leak severity (assumes eta_loss proxy 1 <-> label 'severe leakage')
    coef = float(np.mean([sens[f"TS{i}"]["coef_per_full_severity"]["leak"] for i in range(1, 5)]))
    se = float(np.mean([sens[f"TS{i}"]["coef_se"]["leak"] for i in range(1, 5)]))
    t = coef / se if se > 0 else 0.0
    ok = coef > 0 and abs(t) >= T_APPLY
    if ok:
        over["heat_eta"] = coef
    notes["heat_eta"] = {"applied": bool(ok), "rig_degC_per_full_leak": coef, "se": se, "t": t, "prior": prior["heat_eta"],
                         "rule": "apply if coefficient > 0 and |t| >= 2"}
    for ch, sensor, lab, param in (("pump_pressure_kpa", "PS1", "leak", "leak_weight"), ("pump_pressure_kpa", "PS1", "accumulator", "precharge_weight")):
        c, s = sens[sensor]["coef_per_full_severity"][lab], sens[sensor]["coef_se"][lab]
        notes[param] = {
            "applied": False, "channel": ch, "rig_rel_change_per_full_severity": c / sens[sensor]["mean"], "t": c / s if s > 0 else 0.0,
            "reason": "sign conflict: rig pump outlet pressure rises with this condition label (load-controlled test bench); the model's pressure falls with damage. Prior kept.",
        }
    notes["case_drain / fluid_qty / filter_dp channels"] = {"applied": False, "reason": "no corresponding rig sensor; priors kept"}
    notes["rates (eta_drift, leak_scale, precharge_rate, acid_rate, life_cycles, failure_*)"] = {"applied": False, "reason": "rig degradation is stepped by design: no time-to-failure information"}
    return over, notes


def fit_all(data_dir: Path | None = None) -> dict[str, Any]:
    from app.sim.v2.models.hydraulics import PRIOR_PARAMS

    rig = load_rig(data_dir)
    sens = fit_sensitivities(rig)
    over, notes = build_overrides(sens, PRIOR_PARAMS)
    profile = rig["profile"]
    return {
        "meta": {
            "tag": "fitted to a test rig, not an aircraft: relative sensitivities and noise only; rates remain assumed priors (no citation)",
            "source": "UCI Condition monitoring of hydraulic systems (ZeMA gGmbH), CC BY 4.0",
            "n_cycles": int(len(profile)),
            "n_stable_cycles": int((profile[:, 4] == 0).sum()),
            "n_label_cells": int(len(np.unique(profile[:, :4], axis=0))),
            "severity_definition": SEVERITY_DOC,
            "cycle_features": "mean over each 60 s cycle",
            "bootstrap": {"resamples": N_BOOT, "unit": "label cell"},
            "detect_dprime": DETECT_DPRIME,
        },
        "sensors": sens,
        "detectability": detectability(sens, profile),
        "decisions": notes,
        "overrides": over,
    }


@contextlib.contextmanager
def params_override(params: dict[str, float], values: dict[str, float]) -> Iterator[None]:
    saved = dict(params)
    params.update(values)
    try:
        yield
    finally:
        params.clear()
        params.update(saved)


def nominal_context(n: int, oat_c: float = 15.0, dust_index: float = 0.2, cycle: int = 1) -> Any:
    from app.sim.v2.drivers import OperatingContext

    values: dict[str, Any] = {f.name: np.zeros(n) for f in fields(OperatingContext)}
    values.update(
        flight_hours=np.full(n, 2.0), oat_c=np.full(n, oat_c), dust_index=np.full(n, dust_index), apu_starts=np.ones(n), payload_frac=np.full(n, 0.7),
        thrust_derate=np.full(n, 0.1), calendar_days=np.full(n, cycle / 3.0), flight_cycles=np.full(n, float(cycle)), cycle_index=np.full(n, float(cycle)),
        utilisation=np.ones(n), wet_runway=np.zeros(n, dtype=bool),
    )
    return OperatingContext(**values)


def distances(real: np.ndarray, sim: np.ndarray) -> dict[str, float]:
    """Two-sample KS and Wasserstein-1 (the latter in units of the real sd)."""
    from scipy import stats

    sd = float(np.std(real)) or 1.0
    return {"ks": float(stats.ks_2samp(real, sim).statistic), "wasserstein_over_real_sd": float(stats.wasserstein_distance(real, sim) / sd)}


def _sim_channels(n: int, seed: int, flights: int, every: int) -> dict[str, np.ndarray]:
    """Pooled emitted readings of n units sampled every `every` flights; flights=0 gives fresh units only."""
    from app.sim.v2.models.hydraulics import HydraulicsModel

    model = HydraulicsModel()
    rng = np.random.default_rng(seed)
    state = model.init(n, rng)
    ctx = nominal_context(n)
    pools: dict[str, list[np.ndarray]] = {c: [] for c in model.channels}
    for k in range(flights + 1):
        if k > 0:
            ctx.calendar_days[:] = k / 3.0
            model.step(state, ctx, rng)
        if k % every == 0:
            for c, v in model.emit(state, ctx, rng).items():
                pools[c].append(v)
    return {c: np.concatenate(v) for c, v in pools.items()}


def validate(data_dir: Path | None = None, n_sim: int = 400, seed: int = 11) -> dict[str, Any]:
    """Before/after (prior vs fitted PARAMS) distribution distances between the model and the rig."""
    from app.sim.v2.models import hydraulics as hm

    rig = load_rig(data_dir)
    profile = rig["profile"]
    healthy = (profile[:, 0] == 100) & (profile[:, 1] == 100) & (profile[:, 2] == 0) & (profile[:, 3] == 130) & (profile[:, 4] == 0)
    fitted = {k: hm.PARAMS[k] for k in hm.PARAMS}
    prior = dict(hm.PRIOR_PARAMS)
    ps1 = rig["sensors"]["PS1"]["mean"]
    ts = np.mean([rig["sensors"][f"TS{i}"]["mean"] for i in range(1, 5)], axis=0)
    out: dict[str, Any] = {"n_healthy_cycles": int(healthy.sum()), "n_sim_units": n_sim, "cases": {}}
    for label, params in (("prior", prior), ("fitted", fitted)):
        with params_override(hm.PARAMS, params):
            fresh = _sim_channels(n_sim, seed, 0, 1)
            life = _sim_channels(n_sim, seed + 1, 1500, 50)
        res: dict[str, Any] = {}
        # healthy-cell noise: centred, relative for pressure (sd / mean), absolute degC for temperature
        r = ps1[healthy] / ps1[healthy].mean() - 1.0
        s = fresh["pump_pressure_kpa"] / fresh["pump_pressure_kpa"].mean() - 1.0
        res["healthy_pressure_relative"] = {**distances(r, s), "real_sd": float(r.std()), "sim_sd": float(s.std())}
        r = ts[healthy] - ts[healthy].mean()
        s = fresh["fluid_temp_c"] - fresh["fluid_temp_c"].mean()
        res["healthy_fluid_temp_c"] = {**distances(r, s), "real_sd": float(r.std()), "sim_sd": float(s.std())}
        # all cycles / lifetime sweep, relative to each source's own healthy mean (unflattering by design)
        r = ps1 / ps1[healthy].mean() - 1.0
        s = life["pump_pressure_kpa"] / fresh["pump_pressure_kpa"].mean() - 1.0
        res["all_pressure_relative"] = {**distances(r, s), "real_mean": float(r.mean()), "sim_mean": float(s.mean())}
        r = ts - ts[healthy].mean()
        s = life["fluid_temp_c"] - fresh["fluid_temp_c"].mean()
        res["all_fluid_temp_c"] = {**distances(r, s), "real_mean": float(r.mean()), "sim_mean": float(s.mean())}
        out["cases"][label] = res
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=FIT_PATH)
    ap.add_argument("--validate", action="store_true", help="print before/after validation JSON instead of fitting")
    a = ap.parse_args(argv)
    if a.validate:
        print(json.dumps(validate(a.data_dir), indent=2))
        return 0
    fit = fit_all(a.data_dir)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(fit, indent=1, default=float) + "\n")
    print(json.dumps(fit["overrides"], indent=1))
    print(f"wrote {a.out} ({a.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
