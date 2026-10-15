"""Sensitivity / uncertainty harness for the v2 simulator.

Parameters are perturbed WITHOUT editing model code: :func:`perturbed` temporarily rewrites entries of the module-level
``PARAMS`` dicts (``app/sim/v2/models/*.py``) and of the world tables ``INFANT``, ``FALSE_CALL`` and ``POLICY`` (thresholds
and intervals) by multiplicative factors, and restores them in a ``finally`` block. The override mutates module globals
in place, so it is NOT thread-safe: never run two perturbed simulations concurrently in one process.

Methods (all assumption-driven; see docs/SENSITIVITY.md): one-at-a-time tornado with common random numbers, Morris
elementary-effects screening over parameter groups, and a Latin-hypercube joint envelope.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import importlib
import json
import math
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Mapping, Sequence

import numpy as np

SCHEMA = "aerotwin.sensitivity/1"
DEFAULT_RANGE = (0.7, 1.3)
MODEL_MODULES = ("apu", "avionics", "brakes", "ecs", "electrical", "engine", "hydraulics", "landing_gear", "tires")
CYCLES_PER_YEAR = 2190
REMOVAL_ACTIONS = ("replace", "overhaul", "nff_return")
FALLBACK_FH_PER_CYCLE = 1.9  # only used when the world exposes no flight-hour total

BoundsFn = Callable[[str], "tuple[float, float] | None"]


# ---------------------------------------------------------------------------------------------------------------
# parameter catalogue and perturbation
# ---------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class ParamSpec:
    id: str          # "<group>.<name>" e.g. "engine.hot_per_c", "infant.avionics.M", "policy.brakes.wear_below"
    group: str       # model kind or "infant" / "false_call" / "policy"
    table: str       # "model:<module>" | "INFANT" | "FALSE_CALL" | "POLICY"
    key: str         # dict key
    field: int | str | None  # tuple index (INFANT, FALSE_CALL) or KindPolicy field name
    base: float
    prob: bool = False  # clip the perturbed value to <= 1


def _model_params(mod: str) -> dict[str, Any]:
    return importlib.import_module(f"app.sim.v2.models.{mod}").PARAMS


def _world():
    return importlib.import_module("app.sim.v2.world")


def _tables() -> dict[str, dict[str, Any]]:
    w = _world()
    t = {f"model:{m}": _model_params(m) for m in MODEL_MODULES}
    t.update({"INFANT": w.INFANT, "FALSE_CALL": w.FALSE_CALL, "POLICY": w.POLICY})
    return t


def _is_num(v: Any) -> bool:
    return isinstance(v, float) and not isinstance(v, bool) and math.isfinite(v) and v > 0.0


def discover() -> list[ParamSpec]:
    """Candidate parameters from the LIVE tables: positive floats (ints are structural and skipped)."""
    out: list[ParamSpec] = []
    for m in MODEL_MODULES:
        for k, v in _model_params(m).items():
            if _is_num(v):
                out.append(ParamSpec(f"{m}.{k}", m, f"model:{m}", k, None, float(v)))
    w = _world()
    for kind, (mult, tau) in w.INFANT.items():
        out.append(ParamSpec(f"infant.{kind}.M", "infant", "INFANT", kind, 0, float(mult)))
        out.append(ParamSpec(f"infant.{kind}.tau", "infant", "INFANT", kind, 1, float(tau)))
    for kind, (rate, p) in w.FALSE_CALL.items():
        out.append(ParamSpec(f"false_call.{kind}.rate", "false_call", "FALSE_CALL", kind, 0, float(rate)))
        out.append(ParamSpec(f"false_call.{kind}.p_call", "false_call", "FALSE_CALL", kind, 1, float(p), prob=True))
    for kind, pol in w.POLICY.items():
        out.append(ParamSpec(f"policy.{kind}.wear_below", "policy", "POLICY", kind, "wear_below", float(pol.wear_below)))
        out.append(ParamSpec(f"policy.{kind}.check_days", "policy", "POLICY", kind, "check_days", float(pol.check_days)))
    return out


def registry_id(spec: ParamSpec) -> str:
    """Id of the spec in ``params_registry`` (which keys entries by module path, not by harness id)."""
    if spec.table.startswith("model:"):
        return f"v2.models.{spec.table[6:]}.PARAMS[{spec.key}]"
    if spec.table == "POLICY":
        return f"v2.world.POLICY[{spec.key}].{spec.field}"
    return f"v2.world.{spec.table}[{spec.key}][{spec.field}]"


def _registry_perturb() -> dict[str, bool] | None:
    """registry id -> perturb flag, or None when the registry cannot be imported."""
    try:
        reg = importlib.import_module("app.sim.v2.params_registry")
        return {p.id: bool(p.perturb) for p in reg.registry()}
    except Exception:
        return None


def drop_anchors(specs: Sequence[ParamSpec], include_anchors: bool = False) -> tuple[list[ParamSpec], int, bool]:
    """Filter out anchor/structural parameters (registry ``perturb=False``).

    Returns ``(specs, excluded_count, registry_used)``. Without a registry nothing can be excluded."""
    flags = _registry_perturb()
    if flags is None:
        return list(specs), 0, False
    if include_anchors:
        return list(specs), 0, True
    kept = [s for s in specs if flags.get(registry_id(s), True)]
    return kept, len(specs) - len(kept), True


def params_fingerprint() -> str:
    """Stable hash of every perturbable table (used to prove exact restoration)."""
    def enc(v: Any) -> Any:
        if dataclasses.is_dataclass(v) and not isinstance(v, type):
            return dataclasses.asdict(v)
        if isinstance(v, tuple):
            return list(v)
        return v
    blob = {name: {k: enc(v) for k, v in sorted(t.items())} for name, t in sorted(_tables().items())}
    return hashlib.sha256(json.dumps(blob, sort_keys=True, default=repr).encode()).hexdigest()


@contextlib.contextmanager
def perturbed(factors: Mapping[str, float], specs: Sequence[ParamSpec] | None = None) -> Iterator[None]:
    """Temporarily multiply the named parameters by ``factors[id]``; restore every table exactly on exit.

    Not thread-safe (mutates module globals). Unknown ids raise ``KeyError`` before anything is changed."""
    catalogue = {s.id: s for s in (specs if specs is not None else discover())}
    unknown = [i for i in factors if i not in catalogue]
    if unknown:
        raise KeyError(f"unknown parameter ids: {unknown}")
    tables = _tables()
    saved = {name: dict(t) for name, t in tables.items()}
    try:
        for pid, f in factors.items():
            s = catalogue[pid]
            t = tables[s.table]
            val = s.base * float(f)
            if s.prob:
                val = min(val, 1.0)
            if s.table.startswith("model:"):
                t[s.key] = val
            elif s.table == "POLICY":
                t[s.key] = dataclasses.replace(t[s.key], **{str(s.field): val})
            else:
                cur = list(t[s.key])
                cur[int(s.field)] = val  # type: ignore[arg-type]
                t[s.key] = tuple(cur)
        yield
    finally:
        for name, t in tables.items():
            t.clear()
            t.update(saved[name])


def factor_range(pid: str, bounds: BoundsFn | None = None) -> tuple[float, float]:
    """Multiplicative (low, high) for a parameter: ``bounds(id)`` if given (or the lazily imported params_registry's
    ``bounds``) and not None, else the generic [0.7, 1.3]."""
    fn = bounds
    if fn is None:
        try:
            reg = importlib.import_module("app.sim.v2.params_registry")
            fn = getattr(reg, "bounds", None)
        except Exception:  # registry is optional (owned by another change)
            fn = None
    if fn is not None:
        try:
            b = fn(pid)
            if b is not None and len(b) == 2 and 0 < float(b[0]) <= float(b[1]):
                return float(b[0]), float(b[1])
        except Exception:
            pass
    return DEFAULT_RANGE


# ---------------------------------------------------------------------------------------------------------------
# one run
# ---------------------------------------------------------------------------------------------------------------
def run_metrics(seed: int, tails: int, years: float, factors: Mapping[str, float] | None = None,
                specs: Sequence[ParamSpec] | None = None) -> dict[str, float]:
    """Simulate ``tails`` aircraft for ``years`` (2190 cycles/yr) and return flat output metrics.

    Counts are scaled to a 24-tail, one-year basis so runs of different size are comparable.
    Keys: ``removals.<kind>`` (unscheduled), ``mtbur_fh.<kind>``, ``removals.total``, ``nff_share``,
    ``engine_shop_visits``, ``deferrals``."""
    from app.sim.v2.runner import build_world
    ncyc = max(1, int(round(years * CYCLES_PER_YEAR)))
    scale = (24.0 / tails) / years
    with perturbed(factors or {}, specs):
        w = build_world({"seed": int(seed), "fleet": int(tails)})
        w.run(ncyc)
        rem = [r for r in w.removals() if r.action in REMOVAL_ACTIONS]
        deferrals = len(w.maintenance.mel.items)
        counts = dict(w.counts)
    ctx = getattr(w, "_ctx", None)
    fh = None
    if ctx is not None and getattr(ctx, "total_flight_hours", None) is not None:
        fh = float(np.mean(ctx.total_flight_hours))
    if fh is None or fh <= 0:
        fh = ncyc * FALLBACK_FH_PER_CYCLE
    fh = min(fh, ncyc * 6.0)
    kinds = sorted(counts)
    out: dict[str, float] = {}
    unsched = [r for r in rem if r.unscheduled]
    for k in kinds:
        n = sum(1 for r in unsched if r.kind == k)
        out[f"removals.{k}"] = n * scale
        unit_fh = counts[k] * fh  # censored: zero removals => MTBUR reported as the total exposure (lower bound)
        out[f"mtbur_fh.{k}"] = unit_fh / max(n, 1)
    out["removals.total"] = len(unsched) * scale
    out["nff_share"] = (sum(1 for r in unsched if r.nff) / len(unsched)) if unsched else 0.0
    out["engine_shop_visits"] = sum(1 for r in rem if r.kind == "engine" and r.action == "overhaul") * scale
    out["deferrals"] = deferrals * scale
    return out


def _job(args: tuple[Any, ...]) -> dict[str, float]:
    seed, tails, years, factors, specs = args
    return run_metrics(seed, tails, years, factors, specs)


def run_many(jobs: Sequence[tuple[int, int, float, Mapping[str, float]]], specs: Sequence[ParamSpec] | None = None,
             workers: int = 1) -> list[dict[str, float]]:
    """Run ``(seed, tails, years, factors)`` jobs. ``workers > 1`` uses forked processes (each has its own copy of the
    module tables, so the thread-unsafe override is safe); results are identical to the serial path and ordered."""
    args = [(s, t, y, dict(f), specs) for s, t, y, f in jobs]
    if workers <= 1 or len(args) <= 1:
        return [_job(a) for a in args]
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=min(workers, len(args)), mp_context=mp.get_context("fork")) as ex:
        return list(ex.map(_job, args))


def _mean(vals: Sequence[float]) -> float:
    return float(np.mean(vals)) if len(vals) else 0.0


def _sd(vals: Sequence[float]) -> float:
    return float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0


def _pick_outputs(m: Mapping[str, float]) -> list[str]:
    return sorted(m)


# ---------------------------------------------------------------------------------------------------------------
# methods
# ---------------------------------------------------------------------------------------------------------------
def tornado(specs: Sequence[ParamSpec], seeds: Sequence[int], tails: int, years: float,
            bounds: BoundsFn | None = None, baseline: list[dict[str, float]] | None = None,
            progress: Callable[[str], None] | None = None, workers: int = 1) -> dict[str, Any]:
    """One-at-a-time low/high with the SAME seeds (common random numbers) vs the seed-to-seed noise."""
    base = baseline or run_many([(s, tails, years, {}) for s in seeds], specs, workers)
    outputs = _pick_outputs(base[0])
    bmean = {o: _mean([b[o] for b in base]) for o in outputs}
    bsd = {o: _sd([b[o] for b in base]) for o in outputs}
    rows = []
    rngs = [factor_range(sp.id, bounds) for sp in specs]
    jobs = [(s, tails, years, {sp.id: f}) for sp, rg in zip(specs, rngs) for f in rg for s in seeds]
    done = run_many(jobs, specs, workers)  # order: per param, low seeds then high seeds
    n = len(seeds)
    for i, sp in enumerate(specs):
        lo_f, hi_f = rngs[i]
        lo = done[i * 2 * n: i * 2 * n + n]
        hi = done[i * 2 * n + n: (i + 1) * 2 * n]
        res: dict[str, Any] = {}
        for o in outputs:
            d = np.array([h[o] - q[o] for h, q in zip(hi, lo)])  # paired (same-seed) differences
            eff = float(d.mean())
            se = float(d.std(ddof=1) / math.sqrt(len(d))) if len(d) > 1 else 0.0
            res[o] = {"low": _mean([q[o] for q in lo]), "high": _mean([h[o] for h in hi]), "effect": eff, "se": se,
                      "seed_sd": bsd[o],
                      "indistinguishable_from_seed_noise": bool(abs(eff) <= max(2.0 * se, 1e-12) or abs(eff) < bsd[o] / math.sqrt(len(seeds)))}
        rows.append({"param": sp.id, "group": sp.group, "base": sp.base, "low_factor": lo_f, "high_factor": hi_f, "outputs": res})
        if progress:
            progress(f"tornado {sp.id}")
    return {"baseline_mean": bmean, "baseline_seed_sd": bsd, "n_seeds": len(seeds), "rows": rows}


def groups_of(specs: Sequence[ParamSpec]) -> dict[str, list[ParamSpec]]:
    g: dict[str, list[ParamSpec]] = {}
    for s in specs:
        g.setdefault(s.group, []).append(s)
    return g


def morris(specs: Sequence[ParamSpec], groups: Sequence[str], r: int, seed: int, tails: int, years: float,
           rng_seed: int = 0, bounds: BoundsFn | None = None, outputs: Sequence[str] = ("removals.total", "nff_share"),
           progress: Callable[[str], None] | None = None, workers: int = 1) -> dict[str, Any]:
    """Morris elementary effects where each FACTOR is a parameter group (all its parameters share one multiplier).

    4-level grid on [lo, hi] (x in {0, 1/3, 2/3, 1}), step 2/3, ``r`` random one-at-a-time trajectories, fixed sim seed.
    Reports mu (mean EE), mu* (mean |EE|) and sigma per group and output, in output units per unit of x."""
    by_group = groups_of(specs)
    gs = [g for g in groups if g in by_group]
    k = len(gs)
    rng = np.random.default_rng(rng_seed)
    levels = np.array([0.0, 1 / 3, 2 / 3, 1.0])
    delta = 2 / 3
    paths: list[list[tuple[np.ndarray, int]]] = []  # per trajectory: [(x, group index moved to reach x)]
    for _ in range(r):
        x = rng.choice(levels[:2], size=k)  # start where +delta stays on the grid
        steps = [(x.copy(), -1)]
        for gi in rng.permutation(k):
            x = x.copy()
            x[gi] += delta
            steps.append((x, int(gi)))
        paths.append(steps)
    keys = sorted({tuple(float(v) for v in x) for tr in paths for x, _ in tr})
    jobs = []
    for key in keys:
        fac: dict[str, float] = {}
        for gi, g in enumerate(gs):
            for sp in by_group[g]:
                lo, hi = factor_range(sp.id, bounds)
                fac[sp.id] = lo + key[gi] * (hi - lo)
        jobs.append((seed, tails, years, fac))
    cache = dict(zip(keys, run_many(jobs, specs, workers)))
    ees: dict[str, list[list[float]]] = {g: [] for g in gs}
    for tr in paths:
        for (x0, _), (x1, gi) in zip(tr, tr[1:]):
            y0, y1 = cache[tuple(float(v) for v in x0)], cache[tuple(float(v) for v in x1)]
            ees[gs[gi]].append([(y1[o] - y0[o]) / delta for o in outputs])
    if progress:
        progress(f"morris runs {len(cache)}")
    res: dict[str, Any] = {}
    for gi, g in enumerate(gs):
        a = np.array(ees[g]) if ees[g] else np.zeros((0, len(outputs)))
        res[g] = {o: {"mu": float(a[:, j].mean()) if len(a) else 0.0, "mu_star": float(np.abs(a[:, j]).mean()) if len(a) else 0.0,
                      "sigma": float(a[:, j].std(ddof=1)) if len(a) > 1 else 0.0} for j, o in enumerate(outputs)}
    return {"groups": gs, "r": r, "levels": [float(v) for v in levels], "outputs": list(outputs), "sim_seed": seed,
            "n_runs": len(cache), "effects": res}


def latin_hypercube(n: int, d: int, rng: np.random.Generator) -> np.ndarray:
    """``n`` x ``d`` Latin-hypercube sample on [0, 1): one point per stratum per column."""
    u = (np.arange(n)[:, None] + rng.random((n, d))) / n
    for j in range(d):
        u[:, j] = u[rng.permutation(n), j]
    return u


def envelope(specs: Sequence[ParamSpec], n: int, seed: int, tails: int, years: float, rng_seed: int = 0,
             bounds: BoundsFn | None = None, progress: Callable[[str], None] | None = None,
             workers: int = 1) -> dict[str, Any]:
    """Joint perturbation of ALL parameters (LHS over each [lo, hi]); percentiles of the outputs over the N samples.
    This is an assumption-driven spread, not a statistical confidence interval."""
    rng = np.random.default_rng(rng_seed)
    u = latin_hypercube(n, len(specs), rng)
    jobs = []
    for i in range(n):
        fac = {}
        for j, sp in enumerate(specs):
            lo, hi = factor_range(sp.id, bounds)
            fac[sp.id] = lo + float(u[i, j]) * (hi - lo)
        jobs.append((seed, tails, years, fac))
    runs = run_many(jobs, specs, workers)
    if progress:
        progress(f"envelope {n} runs")
    outs = sorted(runs[0])
    pct = {o: {p: float(np.percentile([r[o] for r in runs], q)) for p, q in (("p5", 5), ("p50", 50), ("p95", 95))} for o in outs}
    return {"n": n, "sim_seed": seed, "percentiles": pct, "label": "assumption-driven spread under generic +/-30% parameter ranges; not a confidence interval"}


# ---------------------------------------------------------------------------------------------------------------
# orchestration and reporting
# ---------------------------------------------------------------------------------------------------------------
def select_specs(all_specs: Sequence[ParamSpec], max_params: int | None, rng_seed: int = 0) -> list[ParamSpec]:
    """Deterministic subset spread across groups (round-robin over sorted groups) when ``max_params`` is set."""
    if max_params is None or max_params >= len(all_specs):
        return list(all_specs)
    g = groups_of(all_specs)
    names = sorted(g)
    out: list[ParamSpec] = []
    i = 0
    while len(out) < max_params:
        for name in names:
            if i < len(g[name]) and len(out) < max_params:
                out.append(g[name][i])
        i += 1
    return out


QUICK = dict(tails=3, years=0.2, seeds=(1, 2), max_params=6, morris_groups=("engine", "infant", "false_call"), morris_r=2,
             envelope_n=4)
FULL = dict(tails=24, years=1.0, seeds=(1, 7, 99), max_params=48, morris_groups=(
    "apu", "avionics", "brakes", "ecs", "electrical", "engine", "hydraulics", "landing_gear", "tires", "infant", "false_call", "policy"),
    morris_r=4, envelope_n=30)


def run_all(quick: bool = True, bounds: BoundsFn | None = None, progress: Callable[[str], None] | None = None,
            overrides: Mapping[str, Any] | None = None, workers: int | None = None,
            include_anchors: bool = False) -> dict[str, Any]:
    cfg = dict(QUICK if quick else FULL)
    cfg.update(overrides or {})
    t0 = time.perf_counter()
    all_specs, excluded_anchor_count, registry_used = drop_anchors(discover(), include_anchors)
    specs = select_specs(all_specs, cfg["max_params"])
    seeds = list(cfg["seeds"])
    tails, years = int(cfg["tails"]), float(cfg["years"])
    fp0 = params_fingerprint()
    nw = max(1, int(workers if workers is not None else min(4, os.cpu_count() or 1)))
    base = run_many([(s, tails, years, {}) for s in seeds], all_specs, nw)
    t_base = time.perf_counter() - t0
    tor = tornado(specs, seeds, tails, years, bounds, base, progress, nw)
    mor = morris(all_specs, cfg["morris_groups"], int(cfg["morris_r"]), seeds[0], tails, years, bounds=bounds, progress=progress, workers=nw)
    env = envelope(specs if quick else all_specs, int(cfg["envelope_n"]), seeds[0], tails, years, bounds=bounds, progress=progress, workers=nw)
    return {
        "schema": SCHEMA,
        "config": {"mode": "quick" if quick else "full", "tails": tails, "years": years, "seeds": seeds,
                   "n_params_discovered": len(all_specs), "n_params_tornado": len(specs),
                   "include_anchors": bool(include_anchors), "excluded_anchor_count": excluded_anchor_count,
                   "registry_used": registry_used,
                   "default_factor_range": list(DEFAULT_RANGE), "counts_scaled_to": "24 tails, 1 year",
                   "morris_groups": list(cfg["morris_groups"]), "morris_r": int(cfg["morris_r"]), "envelope_n": int(cfg["envelope_n"])},
        "excluded_anchor_count": excluded_anchor_count, "registry_used": registry_used,
        "warnings": ([] if registry_used else ["registry unavailable: anchors may be included"]),
        "caveats": ["assumption-driven: parameter ranges are generic multiplicative factors, not calibrated uncertainties",
                    "seed noise is estimated from a handful of seeds; small effects are not resolvable",
                    "MTBUR with zero removals is censored at total exposure"]
        + ([] if registry_used else ["registry unavailable: anchors may be included"]),
        "runtime_s": {"total": time.perf_counter() - t0, "single_run_s": t_base / max(len(seeds), 1), "workers": nw},
        "params_fingerprint_unchanged": params_fingerprint() == fp0,
        "tornado": tor, "morris": mor, "envelope": env,
    }


def top_params(result: Mapping[str, Any], output: str, n: int = 5) -> list[tuple[str, float]]:
    rows = result["tornado"]["rows"]
    ranked = sorted(((r["param"], abs(r["outputs"][output]["effect"])) for r in rows), key=lambda t: -t[1])
    return ranked[:n]


def tornado_svg(result: Mapping[str, Any], output: str, top: int = 12) -> str:
    """Hand-built SVG tornado for one output: bars of (high - low) effect, grey when indistinguishable from seed noise."""
    rows = sorted(result["tornado"]["rows"], key=lambda r: -abs(r["outputs"][output]["effect"]))[:top]
    base = result["tornado"]["baseline_mean"][output]
    lab_w, w, bar_h, pad = 230, 760, 20, 40
    h = pad + len(rows) * (bar_h + 6) + 30
    mx = max([abs(r["outputs"][output]["effect"]) for r in rows] + [1e-9])
    cx = lab_w + (w - lab_w) / 2
    scale = (w - lab_w - 40) / 2 / mx
    esc = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;")  # noqa: E731
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" font-family="sans-serif" font-size="12">',
             f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
             f'<text x="10" y="20" font-size="14" font-weight="bold">Tornado: {esc(output)} (baseline mean {base:.3g}; effect = high - low)</text>',
             f'<line x1="{cx}" y1="{pad - 8}" x2="{cx}" y2="{h - 20}" stroke="#444"/>']
    for i, r in enumerate(rows):
        o = r["outputs"][output]
        y = pad + i * (bar_h + 6)
        x1 = cx + min(0.0, o["effect"]) * scale
        bw = abs(o["effect"]) * scale
        color = "#9aa0a6" if o["indistinguishable_from_seed_noise"] else ("#c0392b" if o["effect"] > 0 else "#2471a3")
        parts.append(f'<text x="{lab_w - 8}" y="{y + 14}" text-anchor="end">{esc(r["param"])}</text>')
        parts.append(f'<rect x="{x1:.1f}" y="{y}" width="{max(bw, 1):.1f}" height="{bar_h}" fill="{color}"/>')
        parts.append(f'<text x="{cx + (bw + 4 if o["effect"] >= 0 else -bw - 4):.1f}" y="{y + 14}" '
                     f'text-anchor="{"start" if o["effect"] >= 0 else "end"}">{o["effect"]:+.3g}</text>')
    parts.append(f'<text x="10" y="{h - 6}" fill="#555">grey = indistinguishable from seed noise; red/blue = positive/negative effect of raising the parameter</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def markdown_summary(result: Mapping[str, Any]) -> str:
    c = result["config"]
    L = [f"# Sensitivity summary ({c['mode']})", "",
         f"- fleet {c['tails']} tails, {c['years']} yr, seeds {c['seeds']}; counts scaled to 24 tails / 1 yr",
         f"- anchors excluded {c.get('excluded_anchor_count', 0)} (include_anchors={c.get('include_anchors', False)}); "
         f"registry used: {c.get('registry_used', False)}",
         f"- parameters discovered {c['n_params_discovered']}, in tornado {c['n_params_tornado']}, range {c['default_factor_range']}",
         f"- runtime {result['runtime_s']['total']:.1f} s ({result['runtime_s']['single_run_s']:.2f} s per run)",
         "- ASSUMPTION-DRIVEN: ranges are generic, not calibrated uncertainties; see docs/SENSITIVITY.md", ""]
    outs = [o for o in result["tornado"]["baseline_mean"] if o.startswith("removals.") or o in ("nff_share", "engine_shop_visits", "deferrals")]
    L += ["## Top 5 parameters per output (|high - low|)", ""]
    for o in outs:
        tops = ", ".join(f"{p} ({e:.3g})" for p, e in top_params(result, o))
        L.append(f"- **{o}** (baseline {result['tornado']['baseline_mean'][o]:.3g}, seed sd {result['tornado']['baseline_seed_sd'][o]:.3g}): {tops}")
    L += ["", "## Morris (group screening)", "", "| group | " + " | ".join(f"{o} mu* / sigma" for o in result["morris"]["outputs"]) + " |",
          "|---|" + "---|" * len(result["morris"]["outputs"])]
    for g, v in result["morris"]["effects"].items():
        L.append(f"| {g} | " + " | ".join(f"{v[o]['mu_star']:.3g} / {v[o]['sigma']:.3g}" for o in result["morris"]["outputs"]) + " |")
    L += ["", f"## Envelope (N={result['envelope']['n']}; {result['envelope']['label']})", "", "| output | p5 | p50 | p95 |", "|---|---|---|---|"]
    for o in outs:
        p = result["envelope"]["percentiles"][o]
        L.append(f"| {o} | {p['p5']:.3g} | {p['p50']:.3g} | {p['p95']:.3g} |")
    return "\n".join(L) + "\n"


def write_outputs(result: Mapping[str, Any], out_dir: str) -> list[str]:
    from pathlib import Path
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    paths = []
    p = d / "sensitivity.json"
    p.write_text(json.dumps(result, indent=2, sort_keys=True))
    paths.append(str(p))
    p = d / "SUMMARY.md"
    p.write_text(markdown_summary(result))
    paths.append(str(p))
    for o in result["tornado"]["baseline_mean"]:
        if o.startswith("removals.") or o in ("nff_share", "engine_shop_visits", "deferrals"):
            p = d / f"tornado_{o.replace('.', '_')}.svg"
            p.write_text(tornado_svg(result, o))
            paths.append(str(p))
    return paths
