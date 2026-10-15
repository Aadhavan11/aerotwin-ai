"""Validity pack for the sim-trained health models (``app.ml.train_generic``).

ALL DATA ARE SIMULATED and train / test come from the same simulator, so everything here measures internal
consistency (leakage, split hygiene, calibration, regime sensitivity, rule vs learned) and NOT real-world accuracy.

Sections of the result dict
---------------------------
leakage      feature-ablation retrains (drop age / wear channel / keep only extras ...) with MAE and coverage deltas, plus a
             check of how deterministic the health-index and RUL labels are given the wear-signal channel.
splits       seed-grouped (the trainer's), unit-grouped, temporal and a deliberately leaky row-random split; unit overlap is
             asserted programmatically on the samples the trainer produced.
calibration  reliability of the raw p10/p50/p90 and CQR-widened quantiles; conformal coverage by true-RUL bin.
regimes      in-distribution test vs held-out stress regime vs ageing fleet (units start half worn).
rule_vs_sim  rule demo model vs sim-trained on the same rows with a unit-clustered paired bootstrap CI on the MAE difference.
"""
from __future__ import annotations

import argparse
import json
import time
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence

import numpy as np

from app.ml import conformal as cf
from app.ml import train_generic as tg
from app.ml.sensors import GENERIC

SCHEMA = "aerotwin.ml-validity/1"
CAVEAT = ("CIRCULAR EVALUATION: train and test data come from the same simulator ('same simulator generated train and test'). "
          "These checks show internal consistency, not field accuracy.")
ROLES = ("train", "calib", "test", "shift", "aged")
RUL_BINS = (0.0, 25.0, 50.0, 100.0, 200.0, 400.0)       # true-RUL bin edges; the last bin is rul >= 400 (the cap)
AGED_WEAR = 0.5


# --------------------------------------------------------------------------
# Pure helpers (unit-tested)
# --------------------------------------------------------------------------
def audit_split(groups: dict[str, Sequence[Any]]) -> dict[str, Any]:
    """Pairwise overlap counts of identifiers (unit keys, seeds...) between named partitions."""
    names = list(groups)
    sets = {n: set(groups[n]) for n in names}
    overlaps: dict[str, int] = {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            overlaps[f"{a}&{b}"] = len(sets[a] & sets[b])
    return {"sizes": {n: len(sets[n]) for n in names}, "overlaps": overlaps, "disjoint": all(v == 0 for v in overlaps.values())}


def assert_disjoint(groups: dict[str, Sequence[Any]], what: str = "unit") -> dict[str, Any]:
    """Raise AssertionError naming the offending pairs when any identifier is in two partitions."""
    rep = audit_split(groups)
    if not rep["disjoint"]:
        bad = {k: v for k, v in rep["overlaps"].items() if v}
        raise AssertionError(f"{what} leakage between partitions: {bad}")
    return rep


def binned_r2(x: np.ndarray, y: np.ndarray, bins: int = 40) -> Optional[float]:
    """R^2 of the best piecewise-constant function of ``x`` (quantile bins). ~1 means y is (nearly) a function of x."""
    ok = ~(np.isnan(x) | np.isnan(y))
    x, y = x[ok], y[ok]
    if len(x) < 20 or np.var(y) < 1e-12:
        return None
    edges = np.unique(np.quantile(x, np.linspace(0, 1, bins + 1)))
    b = np.clip(np.searchsorted(edges, x, side="right") - 1, 0, max(len(edges) - 2, 0))
    means = np.array([y[b == k].mean() if (b == k).any() else 0.0 for k in range(len(edges) - 1)])
    return float(1.0 - np.sum((y - means[b]) ** 2) / np.sum((y - y.mean()) ** 2))


def spearman(x: np.ndarray, y: np.ndarray) -> Optional[float]:
    ok = ~(np.isnan(x) | np.isnan(y))
    if ok.sum() < 3:
        return None
    rx = np.argsort(np.argsort(x[ok])).astype(float)
    ry = np.argsort(np.argsort(y[ok])).astype(float)
    if rx.std() == 0 or ry.std() == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def paired_bootstrap_ci(diff: np.ndarray, groups: np.ndarray, n_boot: int = 1000, seed: int = 0, level: float = 0.95) -> dict[str, float]:
    """Cluster bootstrap of the mean of ``diff`` resampling whole ``groups`` (units): rows of one unit are not independent."""
    diff = np.asarray(diff, dtype=float)
    _, inv = np.unique(np.asarray(groups).astype(str), return_inverse=True)
    g = int(inv.max()) + 1
    sums = np.bincount(inv, weights=diff, minlength=g)
    cnt = np.bincount(inv, minlength=g).astype(float)
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, g, size=(n_boot, g))
    means = sums[pick].sum(axis=1) / np.maximum(cnt[pick].sum(axis=1), 1.0)
    lo, hi = np.percentile(means, [50 * (1 - level), 100 - 50 * (1 - level)])
    return {"mean": float(diff.mean()), "lo": float(lo), "hi": float(hi), "p_positive": float((means > 0).mean()), "n_groups": g, "n_rows": int(len(diff))}


def quantile_reliability(y: np.ndarray, q: np.ndarray, nominal: float, below_cap_only: bool = False) -> float:
    """Observed frequency of y <= q."""
    if below_cap_only:
        m = y < tg.CAP
        y, q = y[m], q[m]
    return float(np.mean(y <= q)) if len(y) else float("nan")


def coverage_by_bin(y: np.ndarray, lo: np.ndarray, hi: np.ndarray, edges: Sequence[float] = RUL_BINS) -> list[dict[str, Any]]:
    out = []
    for a, b in zip(edges, list(edges[1:]) + [np.inf]):
        m = (y >= a) & (y < b)
        out.append({"lo": float(a), "hi": None if np.isinf(b) else float(b), "n": int(m.sum()),
                    "coverage": float(np.mean((y[m] >= lo[m]) & (y[m] <= hi[m]))) if m.any() else None})
    return out


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------
@contextmanager
def _aged_worlds(kinds: Sequence[str], wear: float = AGED_WEAR) -> Iterator[None]:
    """Temporarily make ``train_generic`` start every unit of ``kinds`` part-worn (``World.pre_age``). The cycle / age
    counter still starts at 0, so the age feature is misleading by construction (a realistic out-of-distribution case)."""
    orig = tg._regime_world

    def patched(seed, tails, maintain, shifted, util, oat_shift):
        w = orig(seed, tails, maintain, shifted, util, oat_shift)
        w.pre_age({k: np.full(w.counts[k], wear) for k in kinds if k in w.counts}, max_steps=1500)
        return w

    tg._regime_world = patched
    try:
        yield
    finally:
        tg._regime_world = orig


def quick_config() -> tg.GenConfig:
    def rs(seeds: Sequence[int], shifted: bool = False) -> tuple[tg.RunSpec, ...]:
        return tuple(tg.RunSpec(s, maintain=bool(i % 2), shifted=shifted) for i, s in enumerate(seeds))
    return tg.GenConfig(train=rs((101, 102, 103)), calib=rs((201, 202)), test=rs((301, 302)), shift=rs((401,), True), kinds=("tires",),
                        n_tails=4, horizon=600, maintained_horizon=400, stride=4, max_iter=30)


def build_data(cfg: tg.GenConfig, aged_seeds: Sequence[int]) -> dict[str, dict[str, tg.Samples]]:
    recs = tg.generate(cfg)
    with _aged_worlds(cfg.kinds):
        aged: list[tg.UnitRecord] = []
        for s in aged_seeds:
            aged.extend(tg.simulate_run(tg.RunSpec(int(s), maintain=False), cfg))
    recs["aged"] = aged
    return {k: {r: tg.build_samples(recs[r], k, cfg.stride) for r in ROLES} for k in cfg.kinds}


# --------------------------------------------------------------------------
# Column-subset fit (feature ablations / alternative splits)
# --------------------------------------------------------------------------
def _fit_cols(train: tg.Samples, calib: tg.Samples, cols: np.ndarray, cfg: tg.GenConfig) -> dict[str, Any]:
    models = {n: tg._hgb(cfg, loss="quantile", quantile=a).fit(train.X[:, cols], train.rul) for n, a in (("q10", 0.1), ("q50", 0.5), ("q90", 0.9))}
    rp = tg.raw_predict(models, calib.X[:, cols])
    q = float(cf.CQR(tg.ALPHA).fit(calib.rul, rp["q10"], rp["q90"]).q)
    return {"models": models, "cols": cols, "q": q}


def _pred_cols(fit: dict[str, Any], X: np.ndarray) -> dict[str, np.ndarray]:
    rp = tg.raw_predict(fit["models"], X[:, fit["cols"]])
    lo, mid, hi = tg._clip_band(rp["q10"] - fit["q"], rp["q50"], rp["q90"] + fit["q"])
    return {"p10": lo, "p50": mid, "p90": hi, "raw10": rp["q10"], "raw90": rp["q90"]}


def _metrics(fit: dict[str, Any], s: tg.Samples) -> dict[str, Any]:
    if len(s) == 0:
        return {"n_rows": 0}
    p = _pred_cols(fit, s.X)
    m = tg._reg_metrics(s.rul, p["p50"])
    return {"n_rows": int(len(s)), "mae": m["mae"], "mae_below_cap": m["mae_below_cap"], "coverage_80": cf.coverage(s.rul, p["p10"], p["p90"])}


def _delta(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Optional[float]]:
    return {k: (None if a.get(k) is None or b.get(k) is None else float(a[k] - b[k])) for k in ("mae", "mae_below_cap", "coverage_80")}


# --------------------------------------------------------------------------
# (a) leakage audit
# --------------------------------------------------------------------------
def leakage_audit(kind: str, d: dict[str, tg.Samples], cfg: tg.GenConfig) -> dict[str, Any]:
    names = tg.feature_names(kind)
    spec = GENERIC[kind]
    n = len(names)
    wear = np.array([i for i, nm in enumerate(names) if nm.startswith(spec.wear_signal + ".")])
    age = np.array([names.index("age")])
    extras = np.array([i for i, nm in enumerate(names) if any(nm.startswith(e + ".") for e in spec.extras)])
    everything = np.arange(n)
    variants = {
        "full": everything,
        "no_age": np.setdiff1d(everything, age),
        "no_wear_channel": np.setdiff1d(everything, wear),
        "extras_only": extras,
        "wear_channel_only": wear,
        "age_only": age,
    }
    res: dict[str, Any] = {}
    for v, cols in variants.items():
        fit = _fit_cols(d["train"], d["calib"], cols, cfg)
        res[v] = {"n_features": int(len(cols)), "test": _metrics(fit, d["test"]), "shift": _metrics(fit, d["shift"])}
    for v in variants:
        res[v]["delta_vs_full_test"] = _delta(res[v]["test"], res["full"]["test"])
        res[v]["delta_vs_full_shift"] = _delta(res[v]["shift"], res["full"]["shift"])
    tr = d["train"]
    wi = names.index(f"{spec.wear_signal}.last")
    x = tr.X[:, wi]
    res["label_determinism"] = {
        "wear_channel": spec.wear_signal, "n_rows": int(len(tr)),
        "hi_vs_wear_pearson": (float(np.corrcoef(x[~np.isnan(x)], tr.hi[~np.isnan(x)])[0, 1]) if (~np.isnan(x)).sum() > 3 and np.nanstd(x) > 0 else None),
        "hi_vs_wear_spearman": spearman(x, tr.hi), "hi_given_wear_binned_r2": binned_r2(x, tr.hi),
        "rul_given_wear_binned_r2": binned_r2(x, tr.rul), "rul_given_age_binned_r2": binned_r2(tr.X[:, names.index("age")], tr.rul),
        "verdict": "",
    }
    ld = res["label_determinism"]
    r2 = ld["hi_given_wear_binned_r2"]
    ld["verdict"] = ("hi target is (nearly) a deterministic function of the wear channel: the health regressor mostly re-reads its input"
                     if r2 is not None and r2 >= 0.95 else "hi target is not a deterministic function of the wear channel alone")
    return res


# --------------------------------------------------------------------------
# (b) split audit
# --------------------------------------------------------------------------
def _take(s: tg.Samples, mask: np.ndarray) -> tg.Samples:
    return tg.Samples(s.X[mask], s.rul[mask], s.hi[mask], s.anom[mask], s.unit[mask], s.seed[mask], s.ref[mask], s.raw)


def _cat(parts: Sequence[tg.Samples]) -> tg.Samples:
    return tg.Samples(np.concatenate([p.X for p in parts]), np.concatenate([p.rul for p in parts]), np.concatenate([p.hi for p in parts]),
                      np.concatenate([p.anom for p in parts]), np.concatenate([p.unit for p in parts]), np.concatenate([p.seed for p in parts]),
                      np.concatenate([p.ref for p in parts]), [])


def split_audit(kind: str, d: dict[str, tg.Samples], cfg: tg.GenConfig, seed: int = 0) -> dict[str, Any]:
    cols = np.arange(len(tg.feature_names(kind)))
    out: dict[str, Any] = {}

    def run(name: str, tr: tg.Samples, ca: tg.Samples, te: tg.Samples, note: str, require_unit_disjoint: bool = True) -> None:
        groups = {"train": list(tr.unit), "calib": list(ca.unit), "test": list(te.unit)}
        rep = assert_disjoint(groups, "unit") if require_unit_disjoint else audit_split(groups)
        rep_seed = audit_split({"train": list(tr.seed), "calib": list(ca.seed), "test": list(te.seed)})
        fit = _fit_cols(tr, ca, cols, cfg)
        out[name] = {"note": note, "unit_overlaps": rep["overlaps"], "unit_disjoint": rep["disjoint"], "seed_overlaps": rep_seed["overlaps"],
                     "seed_disjoint": rep_seed["disjoint"], "n_train": int(len(tr)), "n_test": int(len(te)), "test": _metrics(fit, te)}

    # 1. seed-grouped: exactly the partitions the trainer produced (asserted, not assumed)
    run("seed_grouped", d["train"], d["calib"], d["test"], "trainer's split: whole simulated worlds (seeds) held out; hence units too")
    # 2. unit-grouped: pool the three roles, assign whole units at random (worlds shared across partitions)
    pool = _cat([d["train"], d["calib"], d["test"]])
    units = np.array(sorted(set(pool.unit)))
    perm = np.random.default_rng(seed).permutation(len(units))
    n_tr, n_ca = int(0.5 * len(units)), int(0.25 * len(units))
    role = {u: ("train" if i < n_tr else "calib" if i < n_tr + n_ca else "test") for i, u in zip(range(len(units)), units[perm])}
    r = np.array([role[u] for u in pool.unit])
    run("unit_grouped", _take(pool, r == "train"), _take(pool, r == "calib"), _take(pool, r == "test"),
        "units disjoint but simulated worlds (weather, profile, stress draw) are shared across partitions")
    # 3. temporal: trainer's seeds, early cycles to fit, later cycles of held-out seeds to test
    tcut = float(np.median(d["train"].ref[:, 1])) if len(d["train"]) else 0.0
    tr, ca = (_take(d[k], d[k].ref[:, 1] < tcut) for k in ("train", "calib"))
    te = _take(d["test"], d["test"].ref[:, 1] >= tcut)
    run("temporal", tr, ca, te, f"fit on cycles < {tcut:.0f}, test on cycles >= {tcut:.0f} of held-out seeds (unit-disjoint AND time-disjoint)")
    if len(tr) and len(te):
        assert tr.ref[:, 1].max() < te.ref[:, 1].min(), "temporal split leaks time"
    # 4. row-random: the leaky reference a naive pipeline would use. Overlap is reported, not asserted away.
    idx = np.random.default_rng(seed + 1).permutation(len(pool))
    a, b = int(0.5 * len(pool)), int(0.75 * len(pool))
    m = np.zeros(len(pool), dtype=int)
    m[idx[a:b]] = 1
    m[idx[b:]] = 2
    run("row_random_LEAKY", _take(pool, m == 0), _take(pool, m == 1), _take(pool, m == 2),
        "DELIBERATELY LEAKY reference: rows of the same unit land in train and test; shows how optimistic a row split is",
        require_unit_disjoint=False)
    return out


# --------------------------------------------------------------------------
# (c) calibration, (d) regimes, (e) rule vs sim
# --------------------------------------------------------------------------
def calibration(bundle: dict, s: tg.Samples) -> dict[str, Any]:
    if len(s) == 0:
        return {"n_rows": 0}
    raw = tg.predict(bundle, s.X, "raw")
    cq = tg.predict(bundle, s.X, "cqr")
    pts = []
    for label, q, nom in (("raw p10", raw["p10"], 0.1), ("raw p50", raw["p50"], 0.5), ("raw p90", raw["p90"], 0.9),
                          ("cqr p10", cq["p10"], 0.1), ("cqr p90", cq["p90"], 0.9)):
        pts.append({"name": label, "nominal": nom, "observed": quantile_reliability(s.rul, q, nom),
                    "observed_below_cap": quantile_reliability(s.rul, q, nom, True)})
    return {"n_rows": int(len(s)), "reliability": pts, "coverage_80_cqr": cf.coverage(s.rul, cq["p10"], cq["p90"]),
            "coverage_80_raw": cf.coverage(s.rul, raw["p10"], raw["p90"]), "coverage_by_rul_bin": coverage_by_bin(s.rul, cq["p10"], cq["p90"])}


def regimes(kind: str, bundle: dict, d: dict[str, tg.Samples]) -> dict[str, Any]:
    out = {}
    for name, key in (("in_distribution", "test"), ("held_out_stress_regime", "shift"), ("ageing_fleet", "aged")):
        e = tg.evaluate_set(bundle, d[key], kind, with_rule=False)
        if e.get("n_rows", 0) == 0:
            out[name] = {"n_rows": 0}
            continue
        out[name] = {"n_rows": e["n_rows"], "n_units": e["n_units"], "frac_at_cap": e["frac_at_cap"], "mae": e["rul"]["mae"],
                     "mae_below_cap": e["rul"]["mae_below_cap"], "coverage_80": e["coverage_80"]["cqr"], "health_mae": e["health_mae"],
                     "anomaly_auc": e["anomaly_auc"]}
    return out


def rule_vs_sim(kind: str, bundle: dict, d: dict[str, tg.Samples], n_boot: int = 1000, max_rows: int = 300) -> dict[str, Any]:
    out = {}
    for name, key in (("in_distribution", "test"), ("held_out_stress_regime", "shift"), ("ageing_fleet", "aged")):
        s = d[key]
        r = tg._rule_predict(kind, s, max_rows=max_rows) if len(s) else None
        if r is None:
            out[name] = {"n_rows": 0}
            continue
        i = r["idx"]
        y = s.rul[i]
        sim = tg.predict(bundle, s.X[i], "cqr")["p50"]
        er, es = np.abs(r["p50"] - y), np.abs(sim - y)
        ci = paired_bootstrap_ci(er - es, s.unit[i], n_boot=n_boot)
        out[name] = {"n_rows": int(len(i)), "n_unscorable_by_rule": int(r["n_unscorable"]), "mae_rule": float(er.mean()), "mae_sim": float(es.mean()),
                     "mae_diff_rule_minus_sim": ci, "sim_better_significant": bool(ci["lo"] > 0), "rule_better_significant": bool(ci["hi"] < 0)}
    return out


def run_validity(cfg: Optional[tg.GenConfig] = None, aged_seeds: Sequence[int] = (501, 502), n_boot: int = 1000, log_fn=None) -> dict[str, Any]:
    cfg = cfg or tg.default_config()
    t0 = time.time()
    data = build_data(cfg, aged_seeds)
    if log_fn:
        log_fn(f"data built ({time.time() - t0:.0f}s)")
    res: dict[str, Any] = {"schema": SCHEMA, "caveat": CAVEAT, "provenance": "SIMULATED", "kinds": {}, "aged_wear_fraction": AGED_WEAR,
                           "config": {"train_seeds": [s.seed for s in cfg.train], "calib_seeds": [s.seed for s in cfg.calib], "test_seeds": [s.seed for s in cfg.test],
                                      "shift_seeds": [s.seed for s in cfg.shift], "aged_seeds": list(aged_seeds), "n_tails": cfg.n_tails, "horizon": cfg.horizon,
                                      "maintained_horizon": cfg.maintained_horizon, "stride": cfg.stride, "max_iter": cfg.max_iter, "seed": cfg.seed}}
    for kind in cfg.kinds:
        d = data[kind]
        # the partitions the trainer will actually use must be unit- and seed-disjoint (hard assertion)
        assert_disjoint({r: list(d[r].unit) for r in ("train", "calib", "test", "shift")}, f"{kind} unit")
        assert_disjoint({r: list(d[r].seed) for r in ("train", "calib", "test", "shift")}, f"{kind} seed")
        bundle = tg.fit_kind(kind, d["train"], d["calib"], cfg)
        res["kinds"][kind] = {
            "rows": {r: int(len(d[r])) for r in ROLES},
            "leakage": leakage_audit(kind, d, cfg),
            "splits": split_audit(kind, d, cfg),
            "calibration": {"test": calibration(bundle, d["test"]), "shift": calibration(bundle, d["shift"])},
            "regimes": regimes(kind, bundle, d),
            "rule_vs_sim": rule_vs_sim(kind, bundle, d, n_boot=n_boot),
        }
        if log_fn:
            log_fn(f"{kind} done ({time.time() - t0:.0f}s)")
    res["seconds"] = round(time.time() - t0, 1)
    return res


# --------------------------------------------------------------------------
# Output: SVG + Markdown
# --------------------------------------------------------------------------
def calibration_svg(kind: str, cal: dict[str, Any], title_suffix: str = "test") -> str:
    """Hand-built SVG: left reliability (nominal vs observed), right conformal 80% coverage by true-RUL bin."""
    W, H = 760, 340
    L, T, S = 60, 50, 250          # left panel origin and side
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" font-family="sans-serif" font-size="11">',
             '<rect width="100%" height="100%" fill="white"/>',
             f'<text x="{W / 2}" y="22" text-anchor="middle" font-size="14">{kind}: quantile calibration ({title_suffix}; SIMULATED data)</text>',
             f'<rect x="{L}" y="{T}" width="{S}" height="{S}" fill="none" stroke="#444"/>',
             f'<line x1="{L}" y1="{T + S}" x2="{L + S}" y2="{T}" stroke="#999" stroke-dasharray="4 3"/>',
             f'<text x="{L + S / 2}" y="{T + S + 32}" text-anchor="middle">nominal quantile</text>',
             f'<text x="16" y="{T + S / 2}" text-anchor="middle" transform="rotate(-90 16 {T + S / 2})">observed frequency of y &lt;= q</text>']
    for v in (0.0, 0.25, 0.5, 0.75, 1.0):
        parts.append(f'<text x="{L + v * S}" y="{T + S + 14}" text-anchor="middle">{v:g}</text>')
        parts.append(f'<text x="{L - 6}" y="{T + S - v * S + 4}" text-anchor="end">{v:g}</text>')
    colour = {"raw": "#1f77b4", "cqr": "#d62728"}
    for p in cal.get("reliability", []):
        o = p["observed"]
        if o != o:
            continue
        cx, cy = L + p["nominal"] * S, T + S - o * S
        c = colour["cqr" if p["name"].startswith("cqr") else "raw"]
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5" fill="{c}"/>')
        parts.append(f'<text x="{cx + 8:.1f}" y="{cy + 4:.1f}" fill="{c}">{p["name"]} ({o:.2f})</text>')
    # right panel: coverage by bin
    L2, bw = 400, 50
    parts += [f'<rect x="{L2}" y="{T}" width="{S + 60}" height="{S}" fill="none" stroke="#444"/>',
              f'<line x1="{L2}" y1="{T + S - 0.8 * S:.1f}" x2="{L2 + S + 60}" y2="{T + S - 0.8 * S:.1f}" stroke="#999" stroke-dasharray="4 3"/>',
              f'<text x="{L2 + S + 62}" y="{T + S - 0.8 * S + 4:.1f}">0.8</text>',
              f'<text x="{L2 + (S + 60) / 2}" y="{T + S + 32}" text-anchor="middle">true RUL bin (cycles); label n rows</text>']
    for i, b in enumerate(cal.get("coverage_by_rul_bin", [])):
        x = L2 + 8 + i * (bw + 5)
        lab = f"{b['lo']:g}-{b['hi']:g}" if b["hi"] is not None else f"{b['lo']:g}+"
        cov = b["coverage"]
        if cov is not None:
            h = cov * S
            parts.append(f'<rect x="{x}" y="{T + S - h:.1f}" width="{bw}" height="{h:.1f}" fill="#d62728" opacity="0.75"/>')
            parts.append(f'<text x="{x + bw / 2}" y="{T + S - h - 4:.1f}" text-anchor="middle">{cov:.2f}</text>')
        parts.append(f'<text x="{x + bw / 2}" y="{T + S + 14}" text-anchor="middle">{lab}</text>')
        parts.append(f'<text x="{x + bw / 2}" y="{T + S + 26}" text-anchor="middle" fill="#666">n={b["n"]}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def _f(x: Any, d: int = 2) -> str:
    return "-" if x is None else f"{x:.{d}f}"


def to_markdown(res: dict[str, Any]) -> str:
    L = [f"> {res['caveat']}", ""]
    L += ["## Leakage audit (retrained with features removed; test = in-distribution, shift = held-out stress regime)", "",
          "| kind | variant | #feat | MAE test | dMAE test | cov80 test | dcov test | MAE shift | dMAE shift | cov80 shift |", "|---|---|---|---|---|---|---|---|---|---|"]
    for k, kd in res["kinds"].items():
        for v, e in kd["leakage"].items():
            if v == "label_determinism":
                continue
            L.append(f"| {k} | {v} | {e['n_features']} | {_f(e['test'].get('mae'), 1)} | {_f(e['delta_vs_full_test']['mae'], 1)} | {_f(e['test'].get('coverage_80'))} "
                     f"| {_f(e['delta_vs_full_test']['coverage_80'])} | {_f(e['shift'].get('mae'), 1)} | {_f(e['delta_vs_full_shift']['mae'], 1)} | {_f(e['shift'].get('coverage_80'))} |")
    L += ["", "| kind | wear channel | hi vs wear (Pearson) | hi vs wear (Spearman) | R2 hi given wear | R2 RUL given wear | R2 RUL given age | verdict |", "|---|---|---|---|---|---|---|---|"]
    for k, kd in res["kinds"].items():
        e = kd["leakage"]["label_determinism"]
        L.append(f"| {k} | {e['wear_channel']} | {_f(e['hi_vs_wear_pearson'])} | {_f(e['hi_vs_wear_spearman'])} | {_f(e['hi_given_wear_binned_r2'])} "
                 f"| {_f(e['rul_given_wear_binned_r2'])} | {_f(e['rul_given_age_binned_r2'])} | {e['verdict']} |")
    L += ["", "## Split audit (unit overlap asserted on the produced samples)", "",
          "| kind | scheme | units disjoint | seeds disjoint | n train | n test | MAE | MAE below cap | cov80 |", "|---|---|---|---|---|---|---|---|---|"]
    for k, kd in res["kinds"].items():
        for n, e in kd["splits"].items():
            L.append(f"| {k} | {n} | {e['unit_disjoint']} | {e['seed_disjoint']} | {e['n_train']} | {e['n_test']} | {_f(e['test'].get('mae'), 1)} "
                     f"| {_f(e['test'].get('mae_below_cap'), 1)} | {_f(e['test'].get('coverage_80'))} |")
    L += ["", "## Calibration (80% interval from CQR; observed frequency of y <= q)", "",
          "| kind | set | raw p10 | raw p50 | raw p90 | cqr p10 | cqr p90 | cov80 cqr | cov80 raw |", "|---|---|---|---|---|---|---|---|---|"]
    for k, kd in res["kinds"].items():
        for s, c in kd["calibration"].items():
            if not c.get("n_rows"):
                continue
            o = {p["name"]: p["observed"] for p in c["reliability"]}
            L.append(f"| {k} | {s} | {_f(o['raw p10'])} | {_f(o['raw p50'])} | {_f(o['raw p90'])} | {_f(o['cqr p10'])} | {_f(o['cqr p90'])} "
                     f"| {_f(c['coverage_80_cqr'])} | {_f(c['coverage_80_raw'])} |")
    L += ["", "### Conformal coverage by true-RUL bin (test)", "", "| kind | " + " | ".join(f"{a:g}+" for a in RUL_BINS) + " |", "|---|" + "---|" * len(RUL_BINS)]
    for k, kd in res["kinds"].items():
        c = kd["calibration"]["test"]
        if c.get("n_rows"):
            L.append(f"| {k} | " + " | ".join(f"{_f(b['coverage'])} (n={b['n']})" for b in c["coverage_by_rul_bin"]) + " |")
    L += ["", "## Performance by regime", "", "| kind | regime | rows | at cap | MAE | MAE below cap | cov80 | health MAE | anomaly AUC |", "|---|---|---|---|---|---|---|---|---|"]
    for k, kd in res["kinds"].items():
        for n, e in kd["regimes"].items():
            if e.get("n_rows"):
                L.append(f"| {k} | {n} | {e['n_rows']} | {_f(e['frac_at_cap'])} | {_f(e['mae'], 1)} | {_f(e['mae_below_cap'], 1)} | {_f(e['coverage_80'])} "
                         f"| {_f(e['health_mae'], 3)} | {_f(e['anomaly_auc'])} |")
    L += ["", "## Rule vs sim-trained (MAE of p50 RUL on the same rows; paired unit-cluster bootstrap 95% CI of rule - sim)", "",
          "| kind | regime | rows | MAE rule | MAE sim | diff (rule - sim) | 95% CI | verdict |", "|---|---|---|---|---|---|---|---|"]
    for k, kd in res["kinds"].items():
        for n, e in kd["rule_vs_sim"].items():
            if not e.get("n_rows"):
                continue
            c = e["mae_diff_rule_minus_sim"]
            v = "sim better" if e["sim_better_significant"] else "rule better" if e["rule_better_significant"] else "not distinguishable"
            L.append(f"| {k} | {n} | {e['n_rows']} | {_f(e['mae_rule'], 1)} | {_f(e['mae_sim'], 1)} | {_f(c['mean'], 1)} | [{_f(c['lo'], 1)}, {_f(c['hi'], 1)}] | {v} |")
    return "\n".join(L) + "\n"


def write_outputs(res: dict[str, Any], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    p = out_dir / "validity.json"
    p.write_text(json.dumps(res, indent=1, default=float))
    paths.append(p)
    p = out_dir / "validity.md"
    p.write_text(to_markdown(res))
    paths.append(p)
    for k, kd in res["kinds"].items():
        for s in ("test", "shift"):
            c = kd["calibration"][s]
            if c.get("n_rows"):
                q = out_dir / f"calibration_{k}_{s}.svg"
                q.write_text(calibration_svg(k, c, s))
                paths.append(q)
    return paths


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="ML validity pack for the sim-trained health models")
    ap.add_argument("--quick", action="store_true", help="tiny training configuration (< 90 s)")
    ap.add_argument("--out", type=Path, default=Path("out/ml-validity"))
    ap.add_argument("--boot", type=int, default=1000)
    a = ap.parse_args(argv)
    cfg = replace(quick_config()) if a.quick else tg.default_config()
    res = run_validity(cfg, aged_seeds=(501,) if a.quick else (501, 502), n_boot=200 if a.quick else a.boot, log_fn=print)
    for p in write_outputs(res, a.out):
        print("wrote", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
