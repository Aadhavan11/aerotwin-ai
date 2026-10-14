"""Calibrate the v2 electrical battery channels against NASA PCoE Li-ion ageing cells B0005/B0006/B0007/B0018.

Run: ``python -m app.calib.battery`` (fit + write JSON), ``--validate`` (before/after distribution distances).

These are 18650 cells charged 1.5 A / 4.2 V and discharged at 2 A to 2.5-2.7 V (rated 2 Ah) at 24 degC ambient, aged to a
30% capacity fade in ~100-170 cycles. They are NOT aircraft batteries: only the SHAPE of fade, the relative coupling of
resistance growth to fade, and measurement scatter are transferred. Absolute rates and limits are not.
Units: capacity fade is in percentage points (pp) of the cell's own initial capacity (mean of its first 3 discharges),
which is the unit of the model's ``fade_pct``; resistance is Re + Rct (ohm) normalised by the cell's first 3 impedance
readings, so only ``R / R0`` transfers.

The .mat files are treated as untrusted numeric data (scipy.io.loadmat parses a fixed binary format; nothing is executed).
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from app.calib.hydraulic import distances, nominal_context, params_override

FIT_PATH = Path(__file__).resolve().parents[1] / "sim" / "v2" / "calibration" / "electrical_fit.json"
DATA_DIR = Path(os.environ.get("AEROTWIN_BATTERY_DIR") or Path(__file__).resolve().parents[2] / "data" / "external" / "nasa_battery_5" / "extracted" / "fy08q4")
CELLS = ("B0005", "B0006", "B0007", "B0018")
N0 = 3  # readings averaged for the initial capacity / resistance
SHAPE_SCALE = 150.0  # life-fraction x in [0, 1] is mapped to k_equiv = SHAPE_SCALE * x so one set of bounds serves both CVs


def load_fit(path: Path | None = None) -> dict[str, Any]:
    """Load the committed fit (no data needed)."""
    return json.loads(Path(path or FIT_PATH).read_text())


def data_available(data_dir: Path | None = None) -> bool:
    d = Path(data_dir or DATA_DIR)
    return all((d / f"{c}.mat").is_file() for c in CELLS)


def load_cells(data_dir: Path | None = None) -> dict[str, dict[str, Any]]:
    from scipy.io import loadmat

    d = Path(data_dir or DATA_DIR)
    if not data_available(d):
        raise FileNotFoundError(d)
    out: dict[str, dict[str, Any]] = {}
    for name in CELLS:
        cycles = loadmat(d / f"{name}.mat", squeeze_me=True, struct_as_record=False)[name].cycle
        cap, tmean, imp_k, re, rct, amb = [], [], [], [], [], set()
        for c in cycles:
            amb.add(float(c.ambient_temperature))
            if c.type == "discharge":
                cap.append(float(c.data.Capacity))
                tmean.append(float(np.mean(c.data.Temperature_measured)))
            elif c.type == "impedance":
                imp_k.append(len(cap))
                re.append(float(c.data.Re))
                rct.append(float(c.data.Rct))
        cap_a = np.asarray(cap)
        r_tot = np.asarray(re) + np.asarray(rct)
        out[name] = {
            "k": np.arange(len(cap_a), dtype=float),
            "cap_ah": cap_a,
            "fade_pp": 100.0 * (1.0 - cap_a / cap_a[:N0].mean()),
            "temp_mean_c": np.asarray(tmean),
            "imp_k": np.asarray(imp_k, dtype=float),
            "re": np.asarray(re),
            "rct": np.asarray(rct),
            "r_rel": r_tot / r_tot[:N0].mean(),
            "ambient_c": sorted(amb),
        }
    return out


# ---- capacity-fade candidates: fade_pp(k), all zero at k = 0 ---------------------------------------------------------

def _linear(k: np.ndarray, s: float) -> np.ndarray:
    return s * k


def _decay(k: np.ndarray, r: float) -> np.ndarray:
    return 100.0 * (1.0 - np.exp(-r * k))


def _accel(k: np.ndarray, a: float, b: float) -> np.ndarray:
    return a * (np.exp(b * k) - 1.0)


def _knee(k: np.ndarray, s1: float, ds: float, kn: float, w: float) -> np.ndarray:
    return s1 * k + ds * w * (np.logaddexp(0.0, (k - kn) / w) - np.logaddexp(0.0, -kn / w))


CANDIDATES: dict[str, tuple[Callable[..., np.ndarray], list[float], tuple[list[float], list[float]]]] = {
    "linear": (_linear, [0.2], ([0.0], [5.0])),
    "exp_decay": (_decay, [0.002], ([1e-6], [0.1])),
    "exp_accel": (_accel, [1.0, 0.01], ([0.0, 1e-5], [50.0, 0.1])),
    "knee": (_knee, [0.15, 0.15, 80.0, 10.0], ([0.0, 0.0, 10.0, 1.0], [2.0, 5.0, 400.0, 60.0])),
}


def fit_curve(name: str, k: np.ndarray, y: np.ndarray) -> np.ndarray:
    from scipy.optimize import curve_fit

    fn, p0, bounds = CANDIDATES[name]
    try:
        popt, _ = curve_fit(fn, k, y, p0=p0, bounds=bounds, maxfev=20000)
    except RuntimeError:
        popt = np.asarray(p0, dtype=float)
    return np.asarray(popt, dtype=float)


def predict(name: str, params: np.ndarray, k: np.ndarray) -> np.ndarray:
    return CANDIDATES[name][0](np.asarray(k, dtype=float), *params)


def _err(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    return {"rmse_pp": float(np.sqrt(np.mean((y - p) ** 2))), "mae_pp": float(np.mean(np.abs(y - p))), "end_error_pp": float(p[-1] - y[-1])}


def fit_capacity(cells: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Per-cell fits and held-out-cell CV, in absolute cycles and in life fraction (window-normalised cycles)."""
    out: dict[str, Any] = {"candidates": list(CANDIDATES), "per_cell": {}, "loco_cycles": {}, "loco_life_fraction": {}, "summary": {}}
    xs = {c: SHAPE_SCALE * cells[c]["k"] / cells[c]["k"][-1] for c in CELLS}
    for name in CANDIDATES:
        out["per_cell"][name], out["loco_cycles"][name], out["loco_life_fraction"][name] = {}, {}, {}
        for c in CELLS:
            d = cells[c]
            p = fit_curve(name, d["k"], d["fade_pp"])
            out["per_cell"][name][c] = {"params": p.tolist(), **_err(d["fade_pp"], predict(name, p, d["k"]))}
            others = [o for o in CELLS if o != c]
            pk = fit_curve(name, np.concatenate([cells[o]["k"] for o in others]), np.concatenate([cells[o]["fade_pp"] for o in others]))
            out["loco_cycles"][name][c] = {"params": pk.tolist(), **_err(d["fade_pp"], predict(name, pk, d["k"]))}
            px = fit_curve(name, np.concatenate([xs[o] for o in others]), np.concatenate([cells[o]["fade_pp"] for o in others]))
            out["loco_life_fraction"][name][c] = {"params": px.tolist(), **_err(d["fade_pp"], predict(name, px, xs[c]))}
        out["summary"][name] = {
            "per_cell_rmse_mean_pp": float(np.mean([out["per_cell"][name][c]["rmse_pp"] for c in CELLS])),
            "loco_cycles_rmse_mean_pp": float(np.mean([out["loco_cycles"][name][c]["rmse_pp"] for c in CELLS])),
            "loco_life_fraction_rmse_mean_pp": float(np.mean([out["loco_life_fraction"][name][c]["rmse_pp"] for c in CELLS])),
        }
    return out


def shape_metrics(cells: dict[str, dict[str, Any]], best: str) -> dict[str, Any]:
    """Share of the window's end fade reached at half the window (0.5 = linear), from the best per-cell fit."""
    out: dict[str, Any] = {}
    for c in CELLS:
        d = cells[c]
        p = fit_curve(best, d["k"], d["fade_pp"])
        k_end = d["k"][-1]
        end = float(predict(best, p, np.array([k_end]))[0])
        half = float(predict(best, p, np.array([k_end / 2.0]))[0])
        out[c] = {"window_cycles": int(k_end) + 1, "fade_end_pp": float(d["fade_pp"][-1]), "fitted_fade_end_pp": end, "half_window_share": half / end if end else None}
    return out


def fit_resistance(cells: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """R/R0 - 1 = slope * fade_pp (through origin): per cell, pooled, bootstrap CI, held-out-cell error."""
    pts = {}
    for c in CELLS:
        d = cells[c]
        idx = np.clip(d["imp_k"].astype(int), 0, len(d["fade_pp"]) - 1)
        pts[c] = (d["fade_pp"][idx], d["r_rel"] - 1.0)

    def slope(x: np.ndarray, y: np.ndarray) -> float:
        den = float((x * x).sum())
        return float((x * y).sum() / den) if den > 0 else 0.0

    per_cell = {c: {"slope_per_pp": slope(*pts[c]), "n_impedance": int(len(pts[c][0])), "r_rel_end": float(cells[c]["r_rel"][-N0:].mean()),
                    "re_rel_end": float(cells[c]["re"][-N0:].mean() / cells[c]["re"][:N0].mean()), "rct_rel_end": float(cells[c]["rct"][-N0:].mean() / cells[c]["rct"][:N0].mean())}
                for c in CELLS}
    allx = np.concatenate([pts[c][0] for c in CELLS])
    ally = np.concatenate([pts[c][1] for c in CELLS])
    pooled = slope(allx, ally)
    from scipy import stats

    cs = np.asarray([per_cell[c]["slope_per_pp"] for c in CELLS])
    half = float(stats.t.ppf(0.975, len(cs) - 1) * cs.std(ddof=1) / np.sqrt(len(cs)))
    cell_ci = [float(cs.mean() - half), float(cs.mean() + half)]
    loco = {}
    for c in CELLS:
        ox = np.concatenate([pts[o][0] for o in CELLS if o != c])
        oy = np.concatenate([pts[o][1] for o in CELLS if o != c])
        s = slope(ox, oy)
        resid = pts[c][1] - s * pts[c][0]
        loco[c] = {"slope_per_pp": s, "rmse_rel": float(np.sqrt(np.mean(resid**2))), "mean_error_rel": float(resid.mean() * -1.0)}
    resid = ally - pooled * allx
    # scatter about the trend after removing a smooth per-cell trend (running median over 5 readings)
    detr = []
    for c in CELLS:
        y = cells[c]["r_rel"]
        sm = np.array([np.median(y[max(0, i - 2): i + 3]) for i in range(len(y))])
        detr.append(y - sm)
    return {
        "per_cell": per_cell,
        "pooled_slope_per_pp": pooled,
        "cell_slope_mean": float(cs.mean()),
        "cell_slope_ci95": cell_ci,
        "pooled_rmse_rel": float(np.sqrt(np.mean(resid**2))),
        "loco": loco,
        "scatter_rel_sd": float(np.std(np.concatenate(detr))),
    }


def fit_capacity_noise(cells: dict[str, dict[str, Any]], best: str) -> dict[str, float]:
    """Reading-to-reading capacity scatter. First differences remove the slow trend and the lack of fit of any smooth curve."""
    diff = np.concatenate([np.diff(cells[c]["fade_pp"]) for c in CELLS])
    resid = np.concatenate([cells[c]["fade_pp"] - predict(best, fit_curve(best, cells[c]["k"], cells[c]["fade_pp"]), cells[c]["k"]) for c in CELLS])
    return {
        "diff_sd_pp": float(diff.std() / np.sqrt(2.0)),
        "diff_robust_sd_pp": float(1.4826 * np.median(np.abs(diff - np.median(diff))) / np.sqrt(2.0)),
        "smooth_fit_resid_sd_pp": float(resid.std()),
    }


def temperature_acceleration(cells: dict[str, dict[str, Any]], cap: dict[str, Any], best: str) -> dict[str, Any]:
    ambient = sorted({t for c in CELLS for t in cells[c]["ambient_c"]})
    obs = {c: {"mean_cell_temp_c": float(cells[c]["temp_mean_c"].mean()), "mean_fade_pp_per_cycle": float(cells[c]["fade_pp"][-1] / cells[c]["k"][-1])} for c in CELLS}
    return {
        "supported": len(ambient) > 1,
        "ambient_c_values": ambient,
        "reason": "all four cells were cycled at one ambient temperature; the small differences in measured cell temperature are self-heating and "
                  "are confounded with discharge cut-off voltage (2.5/2.2/2.5 V) and cell-to-cell spread, so no Arrhenius activation energy can be fitted. "
                  "activation_k stays a literature prior.",
        "observational_only": obs,
    }


def build_overrides(res: dict[str, Any], noise: dict[str, float], prior: dict[str, float]) -> tuple[dict[str, float], dict[str, Any]]:
    over: dict[str, float] = {}
    notes: dict[str, Any] = {}
    lo, hi = res["cell_slope_ci95"]
    rejected = not lo <= prior["resistance_fade"] <= hi
    if rejected:
        over["resistance_fade"] = res["cell_slope_mean"]
    notes["resistance_fade"] = {"applied": bool(rejected), "rig_value": res["cell_slope_mean"], "cell_ci95_t": [lo, hi], "n_cells": len(CELLS), "prior": prior["resistance_fade"],
                                "rule": "mean of the 4 per-cell slopes of R/R0 - 1 per fade pp; applied when the prior lies outside the cell-level 95% t-interval (the data reject the prior)",
                                "translation": "dimensionless (relative resistance growth per percentage point of fade): transfers across cell format",
                                "caveat": "with 4 cells the interval is wide and includes 0 (B0018 shows no growth); the data reject the prior's magnitude, they do not pin the value. "
                                          "resistance_rate stays a prior and may double-count a few % of R0"}
    cap_noise = noise["diff_sd_pp"]
    over["capacity_noise"] = max(cap_noise, prior["capacity_noise"])
    notes["capacity_noise"] = {"applied": cap_noise > prior["capacity_noise"], "rig_value_pp": cap_noise, "robust_sd_pp": noise["diff_robust_sd_pp"],
                               "smooth_fit_resid_sd_pp": noise["smooth_fit_resid_sd_pp"], "prior": prior["capacity_noise"],
                               "rule": "sd of first differences of fade_pp / sqrt(2); max(rig, prior) because the rig is a lab instrument",
                               "caveat": "includes real capacity-regeneration jumps after rest, not only measurement noise"}
    r_noise = res["scatter_rel_sd"] * prior["resistance_base"]
    over["resistance_noise"] = max(r_noise, prior["resistance_noise"])
    notes["resistance_noise"] = {"applied": r_noise > prior["resistance_noise"], "rig_rel_sd": res["scatter_rel_sd"], "rig_value_mohm": r_noise, "prior": prior["resistance_noise"],
                                 "rule": "relative scatter of (Re + Rct) about a 5-point running median, times model fresh resistance (relative translation); max(rig, prior)"}
    notes["cycle_fade / calendar_fade / life_cycles"] = {"applied": False, "reason": "absolute rates of 18650 cells aged in ~150 cycles to 30% fade are not aircraft rates; priors kept"}
    notes["activation_k"] = {"applied": False, "reason": "no ambient-temperature variation in the data"}
    return over, notes


def fit_all(data_dir: Path | None = None) -> dict[str, Any]:
    from app.sim.v2.models.electrical import PRIOR_PARAMS

    cells = load_cells(data_dir)
    cap = fit_capacity(cells)
    best = min(CANDIDATES, key=lambda n: cap["summary"][n]["loco_cycles_rmse_mean_pp"])
    res = fit_resistance(cells)
    noise = fit_capacity_noise(cells, best)
    over, notes = build_overrides(res, noise, PRIOR_PARAMS)
    return {
        "meta": {
            "tag": "fitted to 18650 lab cells, not aircraft batteries: calibrates SHAPE and relative coupling, not absolute rates or limits",
            "source": "NASA PCoE Li-ion battery ageing set #5 (B0005, B0006, B0007, B0018)",
            "conditions": "charge 1.5 A CC to 4.2 V then CV to 20 mA; discharge 2 A CC to 2.7/2.5/2.2/2.5 V; ambient 24 degC; rated 2 Ah",
            "units": "fade in pp of own initial capacity (mean of first 3 discharges); resistance as (Re + Rct) / R0 with R0 = mean of first 3 impedance readings",
            "cells": {c: {"discharges": int(len(cells[c]["k"])), "impedance_readings": int(len(cells[c]["re"])), "initial_capacity_ah": float(cells[c]["cap_ah"][:N0].mean())} for c in CELLS},
            "best_capacity_model": best,
            "model_unit_translation": "model fade_pct (pp of healthy capacity) = rig fade_pp; model flight/start cycles are NOT rig cycles (rig 30% fade in ~100-170 cycles vs model ~2000 flights)",
        },
        "capacity_fade": cap,
        "shape": shape_metrics(cells, best),
        "resistance": res,
        "capacity_noise": noise,
        "temperature_acceleration": temperature_acceleration(cells, cap, best),
        "decisions": notes,
        "overrides": over,
    }


def _sim_pool(n: int, seed: int, target_fade_pp: float, samples: int = 60) -> dict[str, np.ndarray]:
    """Emit capacity and relative resistance from n units run until the median fade reaches target; pooled over `samples` snapshots."""
    from app.sim.v2.models.electrical import ElectricalModel

    model = ElectricalModel()
    rng = np.random.default_rng(seed)
    state = model.init(n, rng)
    ctx = nominal_context(n)
    r0 = model.emit(state, ctx, np.random.default_rng(seed + 1))["battery_resistance_mohm"]
    k = 0
    horizon = 20000
    snaps: list[tuple[np.ndarray, np.ndarray]] = []
    while k < horizon and float(np.median(state["fade_pct"])) < target_fade_pp:
        k += 1
        ctx.calendar_days[:] = k / 3.0
        model.step(state, ctx, rng)
        if k % 10 == 0:
            em = model.emit(state, ctx, rng)
            snaps.append((100.0 - em["battery_capacity_pct"], em["battery_resistance_mohm"] / r0))
    pick = np.linspace(0, len(snaps) - 1, min(samples, len(snaps))).astype(int)
    return {"fade_pp": np.concatenate([snaps[i][0] for i in pick]), "r_rel": np.concatenate([snaps[i][1] for i in pick]), "flights": np.array([float(k)])}


def validate(data_dir: Path | None = None, n_sim: int = 300, seed: int = 5) -> dict[str, Any]:
    from app.sim.v2.models import electrical as em

    cells = load_cells(data_dir)
    real_fade = np.concatenate([cells[c]["fade_pp"] for c in CELLS])
    real_r = np.concatenate([cells[c]["r_rel"] for c in CELLS])
    target = float(np.mean([cells[c]["fade_pp"][-1] for c in CELLS]))
    fitted = dict(em.PARAMS)
    out: dict[str, Any] = {"n_sim_units": n_sim, "target_end_fade_pp": target, "cases": {}}
    for label, params in (("prior", dict(em.PRIOR_PARAMS)), ("fitted", fitted)):
        with params_override(em.PARAMS, params):
            sim = _sim_pool(n_sim, seed, target)
        # sim fade is sampled uniformly in flights to the target; real fade is sampled uniformly in cycles to each cell's end
        out["cases"][label] = {
            "fade_pp": {**distances(real_fade, sim["fade_pp"]), "real_mean": float(real_fade.mean()), "sim_mean": float(sim["fade_pp"].mean())},
            "r_rel": {**distances(real_r, sim["r_rel"]), "real_mean": float(real_r.mean()), "sim_mean": float(sim["r_rel"].mean()), "real_sd": float(real_r.std()),
                      "sim_sd": float(sim["r_rel"].std())},
            "flights_to_target": float(sim["flights"][0]),
        }
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
    print(json.dumps({"best": fit["meta"]["best_capacity_model"], "overrides": fit["overrides"], "summary": fit["capacity_fade"]["summary"]}, indent=1))
    print(f"wrote {a.out} ({a.out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
