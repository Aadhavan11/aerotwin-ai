"""Per-airline alert operating-point selector (SIMULATED evaluation; advisory only).

Given an operator's costs, choose per component kind the alert threshold on the scorer's anomaly score that minimises the
expected cost per 1000 flight hours, subject to a minimum median lead time and a maximum false-alarm rate (inspection
capacity). The cost curve comes from re-scoring the back-test streams (``app.ml.backtest``) once and sweeping the threshold.

Cost model (all USD, ILLUSTRATIVE PRIORS the airline must replace):
  * every false-alarm episode (rising edge on a fault-free unit or before the fault onset) costs ``false_alarm_cost``
  * a functional failure that was never detected in time costs ``miss_cost``
  * a failure detected with lead L cycles costs ``max(0, miss_cost - lead_value * min(L, lead_cap_cycles))``
  * cost per 1000 FH = 1000 * total cost / total scored flight hours (fault + control streams, pooled)
Kinds with no functional failure in the horizon: lead time undefined, only false-alarm cost is reported, no optimum.

CIRCULAR EVALUATION: simulator-generated faults and scorer training data; these are not field numbers.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

SCHEMA = "aerotwin.operating_point/1"
CAVEAT = ("CIRCULAR EVALUATION: the same simulator generated the scorer's training data, the injected faults and the test streams. "
          "Cost inputs are illustrative priors. Not field performance; replace with the airline's own costs and real data.")
DEFAULT_GRID = tuple(round(float(x), 2) for x in np.linspace(0.05, 0.95, 19))
FA_EPISODE_SOURCE = "rising edges of the alert stream"


@dataclass(frozen=True)
class CostParams:
    false_alarm_cost: float = 8_000.0        # PRIOR: unnecessary inspection/removal + NFF shop visit
    miss_cost: float = 60_000.0              # PRIOR: unplanned failure (AOG hours + cancellation + secondary damage)
    lead_value: float = 600.0                # PRIOR: planned-vs-unplanned saving per cycle of lead
    lead_cap_cycles: float = 40.0            # lead beyond this earns no extra value
    max_false_alarms_per_1000fh: float = 20.0  # inspection capacity
    min_lead_cycles: float = 10.0            # minimum median lead time of detected failures

    def key(self) -> tuple[float, ...]:
        return tuple(asdict(self).values())


@dataclass
class Stream:
    """One scored unit stream. ``role`` is 'fault' (known onset, failure None if it never fails) or 'control'."""
    cluster: Any
    role: str
    cycles: np.ndarray
    score: np.ndarray
    fh_cum: np.ndarray
    onset: int = 0
    failure: Optional[int] = None
    kind: str = ""


@dataclass
class _Stat:
    cluster: Any
    fa: np.ndarray            # (T,) false-alarm episodes
    fh: float
    has_failure: bool
    detected: np.ndarray      # (T,) bool (only meaningful when has_failure)
    lead_c: np.ndarray        # (T,) nan when not detected / no failure
    lead_fh: np.ndarray


def _fh_at(fh_cum: np.ndarray, cycle: int) -> float:
    return 0.0 if cycle <= 0 else float(fh_cum[min(cycle, len(fh_cum)) - 1])


def _episodes(a: np.ndarray) -> np.ndarray:
    """Rising edges per column of a (n, T) bool matrix."""
    if a.shape[0] == 0:
        return np.zeros(a.shape[1], dtype=float)
    return a[0].astype(float) + (a[1:] & ~a[:-1]).sum(axis=0)


def stream_stats(s: Stream, thresholds: np.ndarray) -> _Stat:
    T = len(thresholds)
    cyc = np.asarray(s.cycles)
    alert = np.asarray(s.score, dtype=float)[:, None] >= thresholds[None, :]
    fh = 0.0 if len(cyc) == 0 else _fh_at(s.fh_cum, int(cyc[-1])) - _fh_at(s.fh_cum, int(cyc[0]) - 1)
    nan = np.full(T, np.nan)
    if s.role == "control":
        return _Stat(s.cluster, _episodes(alert), fh, False, np.zeros(T, bool), nan, nan.copy())
    pre = cyc < s.onset
    fa = _episodes(alert[pre])
    ok = (cyc >= s.onset) & ((cyc <= s.failure) if s.failure is not None else True)
    sub = alert & ok[:, None]
    det = sub.any(axis=0)
    if s.failure is None:
        return _Stat(s.cluster, fa, fh, False, det, nan, nan.copy())
    first = np.argmax(sub, axis=0)
    c = cyc[first].astype(float)
    lead = np.where(det, float(s.failure) - c, np.nan)
    f_end = _fh_at(s.fh_cum, int(s.failure))
    lfh = np.where(det, f_end - np.array([_fh_at(s.fh_cum, int(x)) for x in c]), np.nan)
    return _Stat(s.cluster, fa, fh, True, det, lead, lfh)


@dataclass
class _Agg:
    clusters: list[Any]
    fa: np.ndarray            # (C, T)
    fh: np.ndarray            # (C,)
    fail_cluster: np.ndarray  # (F,) cluster index of each failure stream
    det: np.ndarray           # (F, T)
    lead_c: np.ndarray        # (F, T)
    lead_fh: np.ndarray       # (F, T)


def _aggregate(stats: Sequence[_Stat], T: int) -> _Agg:
    clusters = sorted({st.cluster for st in stats}, key=repr)
    idx = {c: i for i, c in enumerate(clusters)}
    fa = np.zeros((len(clusters), T))
    fh = np.zeros(len(clusters))
    fc, det, lc, lf = [], [], [], []
    for st in stats:
        i = idx[st.cluster]
        fa[i] += st.fa
        fh[i] += st.fh
        if st.has_failure:
            fc.append(i)
            det.append(st.detected)
            lc.append(st.lead_c)
            lf.append(st.lead_fh)
    z = np.zeros((0, T))
    return _Agg(clusters, fa, fh, np.asarray(fc, dtype=int), np.array(det, dtype=bool) if det else z.astype(bool),
                np.array(lc) if lc else z, np.array(lf) if lf else z)


def _wmedian(vals: np.ndarray, w: np.ndarray) -> float:
    ok = np.isfinite(vals) & (w > 0)
    if not ok.any():
        return float("nan")
    return float(np.median(np.repeat(vals[ok], w[ok].astype(int))))


def _curves(agg: _Agg, p: CostParams, w: np.ndarray) -> dict[str, np.ndarray]:
    """Per-threshold curves for cluster weights ``w`` (C,)."""
    fh = float((agg.fh * w).sum())
    fa = (agg.fa * w[:, None]).sum(axis=0)
    wf = w[agg.fail_cluster] if len(agg.fail_cluster) else np.zeros(0)
    n_fail = float(wf.sum())
    T = agg.fa.shape[1]
    if len(wf):
        capped = np.minimum(np.nan_to_num(agg.lead_c, nan=0.0), p.lead_cap_cycles)
        per = np.where(agg.det, np.maximum(0.0, p.miss_cost - p.lead_value * capped), p.miss_cost)
        fail_cost = (per * wf[:, None]).sum(axis=0)
        n_det = (agg.det * wf[:, None]).sum(axis=0)
        med_c = np.array([_wmedian(agg.lead_c[:, j], wf) for j in range(T)])
        med_f = np.array([_wmedian(agg.lead_fh[:, j], wf) for j in range(T)])
    else:
        fail_cost = np.zeros(T)
        n_det = np.zeros(T)
        med_c = med_f = np.full(T, np.nan)
    per1000 = (lambda x: 1000.0 * x / fh) if fh > 0 else (lambda x: np.full_like(x, np.nan, dtype=float))
    total = fa * p.false_alarm_cost + fail_cost
    tpr = n_det / n_fail if n_fail > 0 else np.full(T, np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        prec = np.where(n_det + fa > 0, n_det / (n_det + fa), np.nan)
    return {"cost": per1000(total), "fa_rate": per1000(fa), "tpr": tpr, "precision": prec, "lead_c": med_c, "lead_fh": med_f, "n_fail": np.full(T, n_fail)}


def feasible_mask(cur: dict[str, np.ndarray], p: CostParams) -> np.ndarray:
    ok = np.isfinite(cur["cost"]) & (cur["fa_rate"] <= p.max_false_alarms_per_1000fh)
    if cur["n_fail"][0] > 0:
        ok &= np.isfinite(cur["lead_c"]) & (cur["lead_c"] >= p.min_lead_cycles)
    return ok


def select(cur: dict[str, np.ndarray], p: CostParams) -> Optional[int]:
    """Index of the feasible minimum-cost threshold; ties go to the higher (less sensitive) threshold. None if infeasible."""
    ok = feasible_mask(cur, p)
    if not ok.any():
        return None
    cost = np.where(ok, cur["cost"], np.inf)
    return int(len(cost) - 1 - np.argmin(cost[::-1]))


def _ci(x: Sequence[float]) -> list[Optional[float]]:
    v = np.asarray([a for a in x if a is not None and np.isfinite(a)], dtype=float)
    if len(v) == 0:
        return [None, None]
    return [float(np.percentile(v, 5)), float(np.percentile(v, 95))]


def _f(x: Any) -> Optional[float]:
    return None if x is None or not np.isfinite(x) else float(x)


def optimise_kind(streams: Sequence[Stream], p: CostParams, default_threshold: float, grid: Sequence[float] = DEFAULT_GRID,
                  n_boot: int = 200, seed: int = 20261015) -> dict[str, Any]:
    """Cost curve, constrained optimum, regret and unit-cluster bootstrap intervals for one component kind."""
    grid_a = np.array(sorted({round(float(g), 6) for g in grid} | {round(float(default_threshold), 6)}))
    T = len(grid_a)
    d_idx = int(np.argmin(np.abs(grid_a - default_threshold)))
    agg = _aggregate([stream_stats(s, grid_a) for s in streams], T)
    C = len(agg.clusters)
    cur = _curves(agg, p, np.ones(C))
    no_fail = len(agg.fail_cluster) == 0
    opt = None if no_fail else select(cur, p)
    rng = np.random.default_rng(seed)
    boots = []
    for _ in range(max(0, n_boot)):
        w = np.bincount(rng.integers(0, C, size=C), minlength=C).astype(float) if C else np.zeros(0)
        cb = _curves(agg, p, w)
        ob = None if no_fail else select(cb, p)
        boots.append((cb, ob))
    flags: list[str] = []
    if no_fail:
        flags.append("no failures observed: cannot optimise (cost shown is false alarms only; lead time undefined)")
    elif opt is None:
        flags.append("no threshold on the grid satisfies both constraints (infeasible)")
    rows = []
    for j in range(T):
        rows.append({
            "threshold": float(grid_a[j]), "tpr": _f(cur["tpr"][j]), "precision": _f(cur["precision"][j]),
            "false_alarms_per_1000fh": _f(cur["fa_rate"][j]), "median_lead_cycles": _f(cur["lead_c"][j]), "median_lead_fh": _f(cur["lead_fh"][j]),
            "expected_cost_per_1000fh": _f(cur["cost"][j]), "cost_ci90": _ci([b[0]["cost"][j] for b in boots]),
            "feasible": bool(feasible_mask(cur, p)[j]), "is_default": j == d_idx, "is_optimum": opt is not None and j == opt,
        })
    out: dict[str, Any] = {"n_units": C, "n_failures": int(len(agg.fail_cluster)), "flight_hours": float(agg.fh.sum()), "default_threshold": float(grid_a[d_idx]),
                           "grid": rows, "flags": flags, "cannot_optimise": bool(no_fail), "n_boot": len(boots)}
    d_cost = _f(cur["cost"][d_idx])
    out["default"] = {"threshold": float(grid_a[d_idx]), "expected_cost_per_1000fh": d_cost, "cost_ci90": _ci([b[0]["cost"][d_idx] for b in boots]),
                      "feasible": bool(feasible_mask(cur, p)[d_idx]), "false_alarms_per_1000fh": _f(cur["fa_rate"][d_idx]), "median_lead_cycles": _f(cur["lead_c"][d_idx])}
    if opt is None:
        out["optimum"] = None
        out["regret_per_1000fh"] = None
        out["constraint_status"] = "not_applicable" if no_fail else "infeasible"
        return out
    o_cost = _f(cur["cost"][opt])
    thr_b = [float(grid_a[ob]) for cb, ob in boots if ob is not None]
    cost_at_point = [cb["cost"][opt] for cb, _ in boots]
    cost_at_opt = [cb["cost"][ob] for cb, ob in boots if ob is not None]
    regret_b = [cb["cost"][d_idx] - cb["cost"][ob] for cb, ob in boots if ob is not None]
    out["optimum"] = {"threshold": float(grid_a[opt]), "threshold_ci90": _ci(thr_b), "expected_cost_per_1000fh": o_cost,
                      "cost_ci90": _ci(cost_at_point), "resampled_optimum_cost_ci90": _ci(cost_at_opt),
                      "false_alarms_per_1000fh": _f(cur["fa_rate"][opt]), "median_lead_cycles": _f(cur["lead_c"][opt]), "median_lead_fh": _f(cur["lead_fh"][opt]),
                      "feasible_fraction_in_bootstrap": (len(thr_b) / len(boots)) if boots else None}
    out["regret_per_1000fh"] = None if d_cost is None or o_cost is None else float(d_cost - o_cost)
    out["regret_ci90"] = _ci(regret_b)
    out["constraint_status"] = "satisfied"
    return out


# --------------------------------------------------------------------------
# Stream collection from the back-test machinery (SIMULATED)
# --------------------------------------------------------------------------
def default_threshold_for(kind: str, scorer: str) -> float:
    from app.ml import health, train_generic as tg

    if scorer == "simtrained":
        b = tg.load(kind)
        if b is not None:
            return float(b["anomaly_threshold"])
    if kind == "engine":
        bundle = health._bundle()
        if bundle is not None and "anomaly_threshold" in bundle.meta:
            return float(bundle.meta["anomaly_threshold"])
        return 0.5
    return 0.75


def _score_values(kind: str, rows: list[dict[str, Any]], upto: int, stride: int, scorer: str) -> tuple[np.ndarray, np.ndarray]:
    from datetime import timedelta

    from app.contracts.models import TelemetryRow
    from app.ml import backtest as bt, health

    flag = "simtrained" if scorer == "simtrained" else "rule"
    cyc, sc = [], []
    with bt._health_model(flag):
        for c in range(3, min(upto, len(rows)) + 1):
            if (c - 3) % stride:
                continue
            win = [TelemetryRow(tail="BT", component_id=r["cid"], ts=bt._T0 + timedelta(hours=r["cycle"]), cycle=int(r["cycle"]), values=r["values"])
                   for r in rows[max(0, c - health.WINDOW):c]]
            cyc.append(c)
            sc.append(float(health.score_component(kind, "BT", rows[0]["cid"], win).anomaly_score))
    return np.asarray(cyc, dtype=int), np.asarray(sc, dtype=float)


def collect_streams(cfg: Any = None, scorer: str = "auto") -> dict[str, Any]:
    """Re-run the back-test worlds and keep raw anomaly scores. Returns {kind: {streams, scorer, default_threshold}}."""
    from app.ml import backtest as bt, train_generic as tg
    from app.sim.v2.faults import LIBRARY

    cfg = cfg or bt.quick_config()
    modes = [LIBRARY[m] for m in (cfg.modes or tuple(LIBRARY))]
    kinds = sorted({m.kind for m in modes})
    horizon_of = {m.id: int(min(cfg.max_horizon, cfg.onset + cfg.onset_jitter + m.ramp_cycles + cfg.tail_cycles)) for m in modes}
    t_max = max(horizon_of.values())
    chosen = {k: (scorer if scorer != "auto" else bt.available_scorers(k)[-1]) for k in kinds}
    out: dict[str, Any] = {k: {"streams": [], "scorer": chosen[k], "default_threshold": default_threshold_for(k, chosen[k])} for k in kinds}
    for seed in cfg.seeds:
        ctrl = bt._Run(seed, cfg.n_tails, t_max, kinds)
        done_ctrl: set[tuple[str, int]] = set()
        for m in modes:
            h = horizon_of[m.id]
            n = ctrl.counts[m.kind]
            onset_u = cfg.onset + (np.arange(n) * 7) % (cfg.onset_jitter + 1)
            run = bt._Run(seed, cfg.n_tails, h, [m.kind], {(m.kind, m.id): onset_u.astype(np.int64)})
            sc = chosen[m.kind]
            for u in range(n):
                on = int(onset_u[u])
                fail = run.first_eol(m.kind, u, on)
                cfail = ctrl.first_eol(m.kind, u, 1)
                attributable = fail is not None and (cfail is None or cfail > fail)
                cluster = (seed, m.kind, u)
                if cfail is None or cfail > on:
                    upto = fail if fail is not None else h
                    cyc, s = _score_values(m.kind, run.rows[(m.kind, u)], upto, cfg.stride, sc)
                    out[m.kind]["streams"].append(Stream(cluster, "fault", cyc, s, run.fh[m.kind][:, u], on, fail if attributable else None, m.kind))
                if (m.kind, u) not in done_ctrl:
                    done_ctrl.add((m.kind, u))
                    cupto = min(t_max, cfail) if cfail is not None else t_max
                    cc, cs = _score_values(m.kind, ctrl.rows[(m.kind, u)], cupto, cfg.stride, sc)
                    out[m.kind]["streams"].append(Stream(cluster, "control", cc, cs, ctrl.fh[m.kind][:, u], 0, None, m.kind))
    _ = tg
    return out


def artifact_hash() -> str:
    from app.ml import health, train_generic as tg

    h = hashlib.sha256()
    for d, pat in ((tg.ARTIFACT_DIR, "generic_*"), (health.ARTIFACT_DIR, "*.json"), (health.ARTIFACT_DIR, "*.joblib")):
        for f in sorted(Path(d).glob(pat)):
            h.update(f.name.encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:16]


def optimise(collected: dict[str, Any], p: Optional[CostParams] = None, n_boot: int = 200, seed: int = 20261015) -> dict[str, Any]:
    p = p or CostParams()
    kinds = {}
    for k, d in sorted(collected.items()):
        r = optimise_kind(d["streams"], p, d["default_threshold"], n_boot=n_boot, seed=seed)
        r["scorer"] = d["scorer"]
        kinds[k] = r
    return {"schema": SCHEMA, "provenance": "SIMULATED", "caveat": CAVEAT, "params": asdict(p),
            "params_note": "all cost inputs are illustrative priors; the airline must set them", "bootstrap": {"resamples": n_boot, "unit": "cluster (seed, kind, unit)", "seed": seed, "ci": 0.90},
            "kinds": kinds}


_CACHE: dict[tuple[Any, ...], dict[str, Any]] = {}
_STREAMS: dict[tuple[Any, ...], dict[str, Any]] = {}


def result_for(p: CostParams, mode: str = "quick", n_boot: int = 200, seed: int = 20261015) -> dict[str, Any]:
    """Cached by (params, scorer artifact hash, mode, n_boot, seed)."""
    from app.ml import backtest as bt

    ah = artifact_hash()
    key = (p.key(), ah, mode, n_boot, seed)
    if key not in _CACHE:
        sk = (ah, mode)
        if sk not in _STREAMS:
            _STREAMS[sk] = collect_streams(bt.quick_config() if mode == "quick" else bt.BacktestConfig())
        res = optimise(_STREAMS[sk], p, n_boot, seed)
        res["artifact_hash"] = ah
        res["mode"] = mode
        _CACHE[key] = res
    return _CACHE[key]


def _num(x: Any, d: int = 2) -> str:
    return "-" if x is None else f"{x:,.{d}f}"


def to_markdown(res: dict[str, Any]) -> str:
    lines = [f"> {res['caveat']}", "", f"Parameters (illustrative priors): `{json.dumps(res['params'])}`", "",
             "| kind | scorer | failures | default thr | default cost/1000FH | optimum thr (90% CI) | optimum cost/1000FH (90% CI) | regret | status |",
             "|---|---|---|---|---|---|---|---|---|"]
    for k, r in res["kinds"].items():
        o, d = r["optimum"], r["default"]
        ot = "-" if o is None else f"{o['threshold']:.2f} ({_num(o['threshold_ci90'][0])}-{_num(o['threshold_ci90'][1])})"
        oc = "-" if o is None else f"{_num(o['expected_cost_per_1000fh'], 0)} ({_num(o['cost_ci90'][0], 0)}-{_num(o['cost_ci90'][1], 0)})"
        st = r["constraint_status"] + ("; " + "; ".join(r["flags"]) if r["flags"] else "")
        lines.append(f"| {k} | {r['scorer']} | {r['n_failures']} | {d['threshold']:.2f} | {_num(d['expected_cost_per_1000fh'], 0)} | {ot} | {oc} | {_num(r['regret_per_1000fh'], 0)} | {st} |")
    return "\n".join(lines) + "\n"


def svg_cost_curve(kind: str, r: dict[str, Any], w: int = 480, h: int = 280) -> str:
    g = r["grid"]
    xs = [row["threshold"] for row in g]
    ys = [row["expected_cost_per_1000fh"] for row in g]
    lo = [row["cost_ci90"][0] if row["cost_ci90"][0] is not None else row["expected_cost_per_1000fh"] for row in g]
    hi = [row["cost_ci90"][1] if row["cost_ci90"][1] is not None else row["expected_cost_per_1000fh"] for row in g]
    vals = [v for v in lo + hi + ys if v is not None]
    ymax = max(vals) if vals else 1.0
    ymin = min(0.0, min(vals)) if vals else 0.0
    ml, mr, mt, mb = 60, 15, 28, 38
    X = lambda x: ml + (x - xs[0]) / max(xs[-1] - xs[0], 1e-9) * (w - ml - mr)  # noqa: E731
    Y = lambda y: mt + (1 - (y - ymin) / max(ymax - ymin, 1e-9)) * (h - mt - mb)  # noqa: E731
    pts = " ".join(f"{X(x):.1f},{Y(y):.1f}" for x, y in zip(xs, ys) if y is not None)
    band = " ".join(f"{X(x):.1f},{Y(v):.1f}" for x, v in zip(xs, hi)) + " " + " ".join(f"{X(x):.1f},{Y(v):.1f}" for x, v in zip(reversed(xs), reversed(lo)))
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" font-family="sans-serif" font-size="11">',
             f'<rect width="{w}" height="{h}" fill="#fff"/>',
             f'<text x="{ml}" y="16" font-size="12">{kind}: expected cost per 1000 FH vs alert threshold (SIMULATED, illustrative costs)</text>',
             f'<polygon points="{band}" fill="#cfe3f5" stroke="none"/>',
             f'<polyline points="{pts}" fill="none" stroke="#1f5fa8" stroke-width="2"/>',
             f'<line x1="{ml}" y1="{h - mb}" x2="{w - mr}" y2="{h - mb}" stroke="#444"/><line x1="{ml}" y1="{mt}" x2="{ml}" y2="{h - mb}" stroke="#444"/>',
             f'<text x="{w / 2:.0f}" y="{h - 8}" text-anchor="middle">alert threshold on anomaly score</text>',
             f'<text x="{ml - 6}" y="{mt + 4}" text-anchor="end">{ymax:,.0f}</text><text x="{ml - 6}" y="{h - mb}" text-anchor="end">{ymin:,.0f}</text>']
    d = r["default"]
    xd = X(d["threshold"])
    parts.append(f'<line x1="{xd:.1f}" y1="{mt}" x2="{xd:.1f}" y2="{h - mb}" stroke="#b33" stroke-dasharray="4 3"/>')
    parts.append(f'<text x="{xd:.1f}" y="{mt - 2}" fill="#b33" text-anchor="middle">default</text>')
    if r["optimum"]:
        t = r["optimum"]["threshold"]
        xo = X(t)
        parts.append(f'<line x1="{xo:.1f}" y1="{mt}" x2="{xo:.1f}" y2="{h - mb}" stroke="#2a7" stroke-dasharray="4 3"/>')
        parts.append(f'<text x="{xo:.1f}" y="{h - mb + 14}" fill="#2a7" text-anchor="middle">optimum</text>')
    else:
        parts.append(f'<text x="{w - mr}" y="{mt + 14}" text-anchor="end" fill="#b33">{"; ".join(r["flags"])[:70]}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def write_outputs(res: dict[str, Any], out_dir: str | Path = "out/operating-point") -> Path:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "operating_point.json").write_text(json.dumps(res, indent=1, default=float))
    (d / "operating_point.md").write_text(to_markdown(res))
    for k, r in res["kinds"].items():
        (d / f"cost_curve_{k}.svg").write_text(svg_cost_curve(k, r))
    return d


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Per-kind alert operating-point selector (SIMULATED)")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--out", default="out/operating-point")
    ap.add_argument("--n-boot", type=int, default=200)
    for f_ in CostParams.__dataclass_fields__:
        ap.add_argument(f"--{f_.replace('_', '-')}", type=float, default=getattr(CostParams(), f_))
    a = ap.parse_args(argv)
    p = CostParams(**{f_: getattr(a, f_) for f_ in CostParams.__dataclass_fields__})
    res = result_for(p, "full" if a.full else "quick", a.n_boot)
    write_outputs(res, a.out)
    print(to_markdown(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
