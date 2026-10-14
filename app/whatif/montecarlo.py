"""Monte-Carlo risk evaluation of a maintenance plan (SIMULATED data; advisory only, not a certified analysis).

``evaluate(state, plan, tasks, cfg, n, seed)`` runs ``n`` seeded trials of the plan, vectorised over trials with numpy, and returns the
distribution of what could go wrong. Everything is deterministic given ``(plan, state, cfg, n, seed)``; results are cached by that key.

Sampled per trial (all of it switched off by ``MCConfig.zero()``, which collapses every trial to the deterministic plan):

* part delivery   a part that is shipped in (or has ``eta_hours`` > 0) arrives ``lead * LogNormal(0, part_delay_sigma)``; the part of the
                  excess over the stated lead delays the job's start
* technician      Bernoulli ``noshow_prob`` per assignment; a no-show delays the start by Triangular(noshow_min_h, noshow_mode_h, noshow_max_h)
* job duration    planned job hours x LogNormal(0, duration_sigma) (or Triangular(lo, mode, hi) with ``duration_model="triangular"``)
* component       time to failure from the component's RUL p10/p50/p90 (cycles) fitted to a lognormal, converted to time with the aircraft's
                  rotation (``cycle_deadline``). Failing before the job is done is an unplanned AOG at that station:
                  AOG hours = hours from failure to job start (if any) + ``aog_recovery_h``; the aircraft's departures in that period are cancelled
* weather         per station used by the plan, Bernoulli ``weather_prob`` of one event in the next 5 days (duration lognormal, severity uniform),
                  plus the stored events of ``app.insights.disruptions``; delays are rolled along the rotation with
                  ``disruptions.propagate`` (existing logic). Only aircraft the plan touches are counted.

Flight delay from a job: the aircraft's next departure after the job slips by ``max(0, actual end - that departure)``;
the delay rolls along the rotation, absorbed by buffers above ``MIN_TURN_MIN`` (same rule as ``disruptions.propagate``). Per leg, a
maintenance delay and a weather delay are combined with ``max`` (not added: avoids double counting). Cancelled legs carry no delay.

Cost per trial reuses ``app/solver/costs.py``: fixed work cost (parts, labor, bay, overhead, shipping: the plan's own metrics) + overrun hours x
(crew labor + bay) rate + delay minutes x ``DELAY_COST_USD_H``/60 + ``DEFERRAL_ALLOWANCE_USD`` per missed deadline or unscheduled task +
AOG hours x ``AOG_COST_USD_H`` + cancelled flights x ``CANCELLATION_COST_USD``. The last two are documented PRIORS (costs.py has no such
constants): illustrative order-of-magnitude values, not real prices.

Simplifications: the planned job extension is charged through the same delay path (so cascades along the rotation ARE priced here, which
``PlanMetrics.cost_delay_usd`` ignores: the two agree when no job has an extension); one failure per component per trial; trials are independent.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import zlib
from collections import OrderedDict
from dataclasses import asdict, dataclass, fields
from datetime import timedelta
from typing import Any, Optional

import numpy as np

from app.contracts.models import Assignment, FleetState, Leg, Plan, Task
from app.solver import costs
from app.solver.common import DEFAULT_MAX_EXT_H, aware
from app.solver.tasks import cycle_deadline

CANCELLATION_COST_USD = 15_000.0   # PRIOR: cost of one cancelled flight (re-accommodation, crew, lost revenue); illustrative
AOG_COST_USD_H = 10_000.0          # PRIOR: cost of an hour of unplanned aircraft-on-ground (twice the delay hour); illustrative
MIN_TURN_MIN = 35.0                # same minimum turn as app.insights.disruptions
MAX_N = 5000
DEFAULT_N = 500
MAX_KNOCKON_LEGS = 6               # rotation legs after a job that a delay can roll onto
WEATHER_HORIZON_H = 120.0
Z90 = 1.2815515655446004           # standard normal 90th percentile


@dataclass(frozen=True)
class MCConfig:
    part_delay_sigma: float = 0.5
    noshow_prob: float = 0.04
    noshow_min_h: float = 2.0
    noshow_mode_h: float = 4.0
    noshow_max_h: float = 8.0
    duration_model: str = "lognormal"      # "lognormal" | "triangular"
    duration_sigma: float = 0.25
    duration_tri_lo: float = 0.9
    duration_tri_mode: float = 1.0
    duration_tri_hi: float = 1.6
    failure_enabled: bool = True
    aog_recovery_h: float = 6.0
    weather_prob: float = 0.05
    weather_duration_median_h: float = 3.0
    weather_severity_lo: float = 0.2
    weather_severity_hi: float = 0.8
    use_stored_weather: bool = True
    cancellation_cost_usd: float = CANCELLATION_COST_USD
    aog_cost_usd_h: float = AOG_COST_USD_H

    @staticmethod
    def zero() -> "MCConfig":
        """No uncertainty at all: every trial is the deterministic plan."""
        return MCConfig(part_delay_sigma=0.0, noshow_prob=0.0, duration_sigma=0.0, duration_tri_lo=1.0, duration_tri_mode=1.0, duration_tri_hi=1.0,
                        failure_enabled=False, weather_prob=0.0, use_stored_weather=False)

    @staticmethod
    def from_overrides(overrides: Optional[dict]) -> "MCConfig":
        base = MCConfig()
        if not overrides:
            return base
        known = {f.name: f for f in fields(MCConfig)}
        bad = sorted(set(overrides) - set(known))
        if bad:
            raise ValueError(f"unknown parameter(s): {', '.join(bad)}; allowed: {', '.join(sorted(known))}")
        out: dict[str, Any] = {}
        for k, v in overrides.items():
            cur = getattr(base, k)
            if isinstance(cur, bool):
                if not isinstance(v, bool):
                    raise ValueError(f"{k} must be true or false")
                out[k] = v
            elif isinstance(cur, str):
                if v not in ("lognormal", "triangular"):
                    raise ValueError("duration_model must be 'lognormal' or 'triangular'")
                out[k] = v
            else:
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
                    raise ValueError(f"{k} must be a finite number >= 0")
                out[k] = float(v)
        cfg = MCConfig(**{**asdict(base), **out})
        if not (cfg.noshow_min_h <= cfg.noshow_mode_h <= cfg.noshow_max_h and cfg.duration_tri_lo <= cfg.duration_tri_mode <= cfg.duration_tri_hi):
            raise ValueError("triangular parameters must satisfy lo <= mode <= hi")
        if cfg.noshow_prob > 1 or cfg.weather_prob > 1 or cfg.weather_severity_lo > cfg.weather_severity_hi or cfg.weather_severity_hi > 1:
            raise ValueError("probabilities must be in [0, 1] and weather_severity_lo <= weather_severity_hi <= 1")
        return cfg


# ---------------------------------------------------------------------------------------------------- helpers
def _tri_ppf(u: np.ndarray, lo: float, mode: float, hi: float) -> np.ndarray:
    """Inverse CDF of a triangular distribution (lo <= mode <= hi); lo == hi gives a constant."""
    if hi <= lo:
        return np.full_like(u, lo)
    fc = (mode - lo) / (hi - lo)
    left = lo + np.sqrt(u * (hi - lo) * (mode - lo))
    right = hi - np.sqrt((1.0 - u) * (hi - lo) * (hi - mode))
    return np.where(u < fc, left, right)


def _hours(t, now) -> float:
    return (aware(t) - now).total_seconds() / 3600.0


def fit_lognormal(p10: float, p50: float, p90: float) -> tuple[float, float]:
    """(mu, sigma) of a lognormal through the median and the p10..p90 spread of an RUL estimate (cycles)."""
    lo = max(p10, 0.5)
    mid = max(p50, lo)
    hi = max(p90, mid, lo + 1e-6)
    sigma = max((math.log(hi) - math.log(lo)) / (2.0 * Z90), 0.05)
    return math.log(max(mid, 0.5)), sigma


def _stock(state: FleetState, station: str, part: Optional[str]):
    if not part:
        return None
    for st in state.stations:
        if st.iata == station:
            for p in st.parts:
                if p.part == part:
                    return p
    return None


def _summary(x: np.ndarray) -> dict:
    return {"mean": round(float(x.mean()), 3), "p50": round(float(np.percentile(x, 50)), 3), "p90": round(float(np.percentile(x, 90)), 3),
            "p95": round(float(np.percentile(x, 95)), 3), "max": round(float(x.max()), 3)}


def cvar(x: np.ndarray, alpha: float = 0.95) -> float:
    """Mean of the worst ``1 - alpha`` share of trials (always >= the mean)."""
    k = max(1, int(math.ceil((1.0 - alpha) * len(x))))
    return float(np.sort(x)[-k:].mean())


# ---------------------------------------------------------------------------------------------------- engine
def evaluate(state: FleetState, plan: Plan, tasks: list[Task], cfg: Optional[MCConfig] = None, n: int = DEFAULT_N, seed: int = 0) -> dict:
    """Run ``n`` trials of ``plan``. Pure: no store, no cache (see ``run`` for those)."""
    from app.insights import disruptions

    cfg = cfg or MCConfig()
    n = int(n)
    now = aware(state.now)
    task_by_id = {t.id: t for t in tasks}
    assigns: list[Assignment] = sorted(plan.assignments, key=lambda a: (a.task_id, a.tail))
    A = len(assigns)
    unscheduled = len([t for t in tasks if t.id not in {a.task_id for a in assigns}])

    # --- legs of the aircraft the plan touches
    tails = sorted({a.tail for a in assigns})
    legs_by_tail: dict[str, list[Leg]] = {}
    for tail in tails:
        legs_by_tail[tail] = sorted((lg for lg in state.legs if lg.tail == tail and aware(lg.sched_arr) > now), key=lambda lg: (aware(lg.sched_dep), lg.id))
    all_legs = [lg for t in tails for lg in legs_by_tail[t]]
    col = {lg.id: i for i, lg in enumerate(all_legs)}
    G = len(all_legs)

    # --- weather (drawn first: shared across plans with the same seed)
    wx_min = np.zeros((n, G))
    if G:
        stored = disruptions.list_events() if cfg.use_stored_weather else []
        w0 = np.zeros(G)
        for d in disruptions.propagate(stored, all_legs) if stored else []:
            w0[col[d["leg_id"]]] = d["delay_min"]
        wx_min[:] = w0
        stations = sorted({s for lg in all_legs for s in (lg.dep, lg.arr)})
        if cfg.weather_prob > 0 and stations:
            wr = np.random.default_rng([int(seed), 0x5EA7])
            occ = wr.random((n, len(stations))) < cfg.weather_prob
            start_h = wr.random((n, len(stations))) * WEATHER_HORIZON_H
            dur_h = np.clip(cfg.weather_duration_median_h * np.exp(0.5 * wr.standard_normal((n, len(stations)))), 0.5, 12.0)
            sev = cfg.weather_severity_lo + wr.random((n, len(stations))) * (cfg.weather_severity_hi - cfg.weather_severity_lo)
            for i in np.nonzero(occ.any(axis=1))[0]:
                evs = list(stored)
                for j in np.nonzero(occ[i])[0]:
                    st = (now + timedelta(hours=float(start_h[i, j]))).isoformat().replace("+00:00", "Z")
                    evs.append({"id": f"MC-{i}-{j}", "station": stations[j], "start": st, "duration_h": float(dur_h[i, j]), "severity": float(sev[i, j])})
                row = np.zeros(G)
                for d in disruptions.propagate(evs, all_legs):
                    row[col[d["leg_id"]]] = d["delay_min"]
                wx_min[i] = row

    # --- per-assignment trial draws (seeded by task id: independent of plan order, shared across plans)
    maint_min = np.zeros((n, G))
    cancel = np.zeros((n, G), dtype=bool)
    miss = np.zeros((n, A), dtype=bool)
    late_ext = np.zeros((n, A), dtype=bool)
    failed = np.zeros((n, A), dtype=bool)
    delay_h_i = np.zeros((n, A))
    own_delay_min = np.zeros((n, A))
    aog_h = np.zeros((n, A))
    cancel_i = np.zeros((n, A))
    overrun_h = np.zeros((n, A))
    rate_over = costs.CREW_SIZE * costs.LABOR_RATE_USD_H + costs.BAY_RATE_USD_H
    per_min = costs.DELAY_COST_USD_H / 60.0

    for k, a in enumerate(assigns):
        r = np.random.default_rng([int(seed), zlib.crc32(a.task_id.encode())])
        z_part, z_dur, z_fail = r.standard_normal((3, n))
        u_ns, u_nslen, u_dur = r.random((3, n))
        task = task_by_id.get(a.task_id)
        s_h, e_h = _hours(a.start, now), _hours(a.end, now)
        mine = legs_by_tail.get(a.tail, [])
        nxt = [lg for lg in mine if _hours(lg.sched_dep, now) >= s_h - 1.0 / 60.0][:MAX_KNOCKON_LEGS]
        # the ground window ends when the aircraft's next departure is due (planned extension: the job ends after it)
        win_end = _hours(nxt[0].sched_dep, now) if nxt else e_h - a.extension_h
        planned = max(e_h - s_h, 0.0)
        # part delivery
        stock = _stock(state, a.station, task.required_part if task else None)
        lead = (float(stock.ship_hours) if a.part_source else float(stock.eta_hours)) if stock else 0.0
        part_delay = lead * np.maximum(np.exp(cfg.part_delay_sigma * z_part) - 1.0, 0.0) if lead > 0 else np.zeros(n)
        # technician no-show
        noshow = np.where((u_ns < cfg.noshow_prob) & (a.technician_id is not None),
                          _tri_ppf(u_nslen, cfg.noshow_min_h, cfg.noshow_mode_h, cfg.noshow_max_h), 0.0)
        # duration
        if cfg.duration_model == "triangular":
            f = _tri_ppf(u_dur, cfg.duration_tri_lo, cfg.duration_tri_mode, cfg.duration_tri_hi)
        else:
            f = np.exp(cfg.duration_sigma * z_dur)
        act_start = s_h + part_delay + noshow
        act_end = act_start + planned * f
        overrun_h[:, k] = np.maximum(act_end - e_h, 0.0)
        if task is not None:
            miss[:, k] = act_end > _hours(task.deadline, now) + 1e-9
        late_ext[:, k] = act_end > win_end + DEFAULT_MAX_EXT_H + 1e-9
        d = np.maximum(act_end - win_end, 0.0)           # hours the aircraft's next departure slips
        delay_h_i[:, k] = d
        # knock-on along the rotation
        buf = 0.0
        for j, lg in enumerate(nxt):
            if j:
                gap_min = (aware(lg.sched_dep) - aware(nxt[j - 1].sched_arr)).total_seconds() / 60.0
                buf += max(0.0, gap_min - MIN_TURN_MIN) / 60.0
            m = np.maximum(d - buf, 0.0) * 60.0
            own_delay_min[:, k] += m
            c = col[lg.id]
            maint_min[:, c] = np.maximum(maint_min[:, c], m)
        # component failure before the job is done
        comp = next((c for c in state.components if c.component_id == (task.component_id if task else None)), None)
        if cfg.failure_enabled and comp is not None and comp.rul_cycles is not None:
            mu, sg = fit_lognormal(comp.rul_cycles.p10, comp.rul_cycles.p50, comp.rul_cycles.p90)
            cyc = np.floor(np.clip(np.exp(mu + sg * z_fail), 0.0, 1e5)).astype(np.int64)
            uniq, inv = np.unique(cyc, return_inverse=True)
            fut = [lg for lg in legs_by_tail.get(a.tail, [])]
            t_f = np.array([_hours(cycle_deadline(fut, int(c), now)[0], now) for c in uniq])[inv]
            fl = t_f < act_end
            failed[:, k] = fl
            aog_h[:, k] = np.where(fl, np.maximum(act_start - t_f, 0.0) + cfg.aog_recovery_h, 0.0)
            deps = np.array([_hours(lg.sched_dep, now) for lg in mine])
            if len(deps):
                hit = fl[:, None] & (deps[None, :] >= t_f[:, None]) & (deps[None, :] <= (act_end + cfg.aog_recovery_h)[:, None])
                cols = [col[lg.id] for lg in mine]
                cancel[:, cols] |= hit
                cancel_i[:, k] = hit.sum(axis=1)

    # --- totals per trial
    leg_min = np.where(cancel, 0.0, np.maximum(maint_min, wx_min))
    flights_delayed = (leg_min > 0).sum(axis=1)
    flights_cancelled = cancel.sum(axis=1)
    delay_min = leg_min.sum(axis=1)
    aog_total = aog_h.sum(axis=1)
    misses = miss.sum(axis=1)
    m = plan.metrics
    fixed = m.cost_parts_usd + m.cost_labor_usd + m.cost_bay_usd + m.cost_overhead_usd + m.logistics_cost_usd + unscheduled * costs.DEFERRAL_ALLOWANCE_USD
    total = (fixed + overrun_h.sum(axis=1) * rate_over + delay_min * per_min + misses * costs.DEFERRAL_ALLOWANCE_USD
             + aog_total * cfg.aog_cost_usd_h + flights_cancelled * cfg.cancellation_cost_usd)

    # --- per-assignment risk (own effects; weather excluded; overlaps between assignments are not netted)
    own_cost = (overrun_h * rate_over + own_delay_min * per_min + miss * costs.DEFERRAL_ALLOWANCE_USD + aog_h * cfg.aog_cost_usd_h
                + cancel_i * cfg.cancellation_cost_usd)
    ranking = []
    for k, a in enumerate(assigns):
        ranking.append({
            "task_id": a.task_id, "tail": a.tail, "station": a.station, "start": aware(a.start).isoformat(), "end": aware(a.end).isoformat(),
            "p_late": round(float(miss[:, k].mean()), 4),
            "p_overrun_infeasible": round(float(late_ext[:, k].mean()), 4),
            "p_failure_before_done": round(float(failed[:, k].mean()), 4),
            "expected_delay_h": round(float(delay_h_i[:, k].mean()), 3),
            "expected_cost_usd": round(float(own_cost[:, k].mean()), 2),
        })
    tot_own = sum(r["expected_cost_usd"] for r in ranking) or 1.0
    for r in ranking:
        r["share_of_risk_cost"] = round(r["expected_cost_usd"] / tot_own, 4)
    ranking.sort(key=lambda r: (-r["expected_cost_usd"], -r["p_late"], r["task_id"]))
    for i, r in enumerate(ranking, 1):
        r["rank"] = i

    counts, edges = np.histogram(total, bins=20)
    return {
        "n": n, "seed": int(seed),
        "cost_usd": {**_summary(total), "std": round(float(total.std()), 3), "cvar95": round(cvar(total), 3)},
        "flights_delayed": _summary(flights_delayed.astype(float)),
        "flights_cancelled": _summary(flights_cancelled.astype(float)),
        "delay_min": _summary(delay_min),
        "aog_h": _summary(aog_total),
        "deadline_misses": _summary(misses.astype(float)),
        "p_miss_any_deadline": round(float((misses + unscheduled > 0).mean()), 4),
        "p_unplanned_aog": round(float((aog_total > 0).mean()), 4),
        "risk_ranking": ranking,
        "histogram": {"metric": "total_cost_usd", "bin_edges": [round(float(e), 2) for e in edges], "counts": [int(c) for c in counts]},
        "unscheduled_tasks": unscheduled,
        "_total": total,
    }


# ---------------------------------------------------------------------------------------------------- cache + entry point
_CACHE: "OrderedDict[tuple, dict]" = OrderedDict()
_CACHE_MAX = 32
_LOCK = threading.RLock()
_LAST: Optional[dict] = None


def plan_hash(state: FleetState, plan: Plan, cfg: MCConfig) -> str:
    """Hash of everything the trials depend on: assignments, plan metrics, the state inputs they read, and the config."""
    payload = {
        "assignments": [a.model_dump(mode="json") for a in sorted(plan.assignments, key=lambda a: a.task_id)],
        "metrics": plan.metrics.model_dump(mode="json"),
        "now": aware(state.now).isoformat(),
        "rul": sorted((c.component_id, c.rul_cycles.p10, c.rul_cycles.p50, c.rul_cycles.p90) for c in state.components if c.rul_cycles is not None),
        "legs": hashlib.sha1(json.dumps(sorted((lg.id, aware(lg.sched_dep).isoformat(), aware(lg.sched_arr).isoformat(), lg.dep, lg.arr) for lg in state.legs
                                               if lg.tail in {a.tail for a in plan.assignments})).encode()).hexdigest(),
        "parts": sorted((s.iata, p.part, p.qty, p.eta_hours, p.ship_hours) for s in state.stations for p in s.parts),
        "cfg": asdict(cfg),
    }
    if cfg.use_stored_weather:
        from app.insights import disruptions
        payload["stored_weather"] = disruptions.list_events()
    return hashlib.sha1(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _strip(res: dict) -> dict:
    return {k: v for k, v in res.items() if not k.startswith("_")}


def run(state: FleetState, plan: Plan, tasks: list[Task], cfg: Optional[MCConfig] = None, n: int = DEFAULT_N, seed: int = 0) -> dict:
    """Cached ``evaluate`` (key: plan hash, n, seed). Returns a JSON-ready dict (no numpy)."""
    cfg = cfg or MCConfig()
    n = max(1, min(int(n), MAX_N))
    h = plan_hash(state, plan, cfg)
    key = (h, n, int(seed))
    with _LOCK:
        hit = _CACHE.get(key)
        if hit is not None:
            _CACHE.move_to_end(key)
            return {**hit, "cached": True}
    res = _strip(evaluate(state, plan, tasks, cfg, n, seed))
    res["plan"] = {"id": plan.id, "mode": plan.mode, "feasible": plan.feasible, "plan_hash": h, "assignments": len(plan.assignments),
                   "deterministic": {"total_cost_usd": plan.metrics.total_cost_usd, "downtime_h": plan.metrics.downtime_h,
                                     "flights_affected": plan.metrics.flights_affected, "late_or_unscheduled": plan.metrics.late_or_unscheduled}}
    res["cached"] = False
    with _LOCK:
        _CACHE[key] = {k: v for k, v in res.items() if k != "cached"}
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)
    return res


def compare_runs(state: FleetState, optimised: Plan, baseline: Plan, tasks: list[Task], cfg: MCConfig, n: int, seed: int) -> dict:
    """Both plans under the same seed (common random numbers for weather and per-task draws) plus the deterministic compare rows."""
    from app.whatif.compare import compare

    ro, rb = run(state, optimised, tasks, cfg, n, seed), run(state, baseline, tasks, cfg, n, seed)

    def brief(r: dict) -> dict:
        return {"plan_id": r["plan"]["id"], "mode": r["plan"]["mode"], "cost_usd": r["cost_usd"], "p_miss_any_deadline": r["p_miss_any_deadline"],
                "flights_delayed_mean": r["flights_delayed"]["mean"], "flights_cancelled_mean": r["flights_cancelled"]["mean"],
                "aog_h_mean": r["aog_h"]["mean"], "deadline_misses_mean": r["deadline_misses"]["mean"]}

    rows = [{"name": r["name"], "plan_id": r["plan_id"], "mode": r["mode"], "feasible": r["feasible"], "total_cost_usd": r["metrics"]["total_cost_usd"],
             "late_or_unscheduled": r["metrics"]["late_or_unscheduled"]} for r in compare(state, {"optimised": optimised, "baseline": baseline}).rows]
    return {
        "optimised": brief(ro), "baseline": brief(rb),
        "delta_mean_cost_usd": round(ro["cost_usd"]["mean"] - rb["cost_usd"]["mean"], 2),
        "delta_cvar95_usd": round(ro["cost_usd"]["cvar95"] - rb["cost_usd"]["cvar95"], 2),
        "delta_p_miss_any_deadline": round(ro["p_miss_any_deadline"] - rb["p_miss_any_deadline"], 4),
        "deterministic": rows,
        "note": "negative delta = the optimised plan is cheaper / less risky than the static baseline",
    }


def set_last(result: dict) -> None:
    global _LAST
    with _LOCK:
        _LAST = result


def get_last() -> Optional[dict]:
    with _LOCK:
        return _LAST


def clear_cache() -> None:
    global _LAST
    with _LOCK:
        _CACHE.clear()
        _LAST = None
