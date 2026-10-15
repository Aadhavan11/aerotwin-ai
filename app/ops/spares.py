"""Rotable spares pool sizing on SIMULATED removal histories. Advisory only; see docs/SPARES.md.

Pipeline: removal records -> weekly demand series per (kind, station) -> intermittent-demand forecast (Croston, SBA or a
Willemain-style bootstrap, chosen by ADI / CV2 class) -> VARI-METRIC two-echelon approximation (central depot per part + bases,
negative-binomial pipeline, expected backorders EBO) -> greedy marginal analysis (EBO reduction per dollar) -> discrete-event
Monte-Carlo validation of the analytic fill rate.

All prices, shop turnaround times, scrap fractions and the AOG cost are ASSUMED priors (``ASSUMPTIONS``), not field data.
"""
from __future__ import annotations

import hashlib
import heapq
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np
from scipy import stats

from app.solver.costs import DELAY_COST_USD_H, PART_PRICE_USD

PROVENANCE = "SIMULATED"
DISCLAIMER = ("SIMULATED removal histories; prices, turnaround times, transport times and scrap fractions are assumed priors. "
              "Synthetic != field validity. Advisory only, not a provisioning source.")
WEEK_DAYS = 7.0
ADI_CUT = 1.32   # Syntetos-Boylan-Croston classification cut-offs
CV2_CUT = 0.49
ALPHA = 0.1
MAX_UNITS = 25
DEFAULT_DEPOT = "ATL"
KIND_PART = {"brakes": "BRAKE-ASSY", "hydraulics": "HYD-PUMP", "avionics": "AVIONICS-LRU", "apu": "APU-STARTER", "tires": "TIRE-ASSY",
             "ecs": "ECS-PACK-VALVE", "electrical": "BATTERY-PACK"}  # engine / landing_gear have no catalogued rotable here
ASSUMPTIONS: dict[str, Any] = {
    "shop_tat_days": {"brakes": 10.0, "hydraulics": 18.0, "avionics": 25.0, "apu": 30.0, "tires": 7.0, "ecs": 20.0, "electrical": 12.0},
    "nff_tat_days": 3.0,           # shop check of a no-fault-found unit
    "condemned_fraction": 0.08,    # share of non-NFF removals scrapped (no scrap flag in the data)
    "buy_lead_days": 90.0,         # replacement purchase lead time for scrapped units
    "holding_rate_per_year": 0.15,
    "aog_cost_usd_per_hour": DELAY_COST_USD_H,
    "repair_cv": 0.3,              # simulator only (the analytic model is insensitive to the TAT distribution)
    "horizon_days": 365.0,
}


# ---------------- demand estimation ----------------
def croston(series: Sequence[float], alpha: float = ALPHA) -> float:
    """Croston per-period forecast z/p. Size z and interval p are smoothed at demand occasions, initialised at the first demand."""
    z = p = None
    last = 0
    for i, d in enumerate(series, start=1):
        if d > 0:
            q = i - last
            if z is None:
                z, p = float(d), float(q)
            else:
                z += alpha * (d - z)
                p += alpha * (q - p)
            last = i
    return 0.0 if z is None or p is None else z / p


def sba(series: Sequence[float], alpha: float = ALPHA) -> float:
    """Syntetos-Boylan approximation: Croston debiased by (1 - alpha/2)."""
    return (1.0 - alpha / 2.0) * croston(series, alpha)


def adi_cv2(series: Sequence[float]) -> tuple[float, float]:
    """Average demand interval (periods per demand occasion) and squared CV of the non-zero sizes."""
    nz = [float(d) for d in series if d > 0]
    if not nz:
        return math.inf, 0.0
    m = sum(nz) / len(nz)
    var = sum((x - m) ** 2 for x in nz) / len(nz)
    return len(series) / len(nz), var / (m * m)


def classify(adi: float, cv2: float) -> str:
    if math.isinf(adi):
        return "none"
    if adi < ADI_CUT:
        return "smooth" if cv2 < CV2_CUT else "erratic"
    return "intermittent" if cv2 < CV2_CUT else "lumpy"


def choose_method(klass: str) -> str:
    """Documented rule: smooth/erratic -> Croston (p ~ 1, bias negligible); intermittent -> SBA; lumpy -> bootstrap."""
    return {"smooth": "croston", "erratic": "croston", "intermittent": "sba", "lumpy": "bootstrap"}.get(klass, "none")


def _seed(*parts: Any) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


def bootstrap_ltd(series: Sequence[float], lead_periods: int, n: int = 2000, seed: int = 0) -> np.ndarray:
    """Willemain-style bootstrap of lead-time demand: 2-state Markov chain (zero / non-zero) with jittered non-zero sizes."""
    s = np.asarray(series, dtype=float)
    nz = s[s > 0]
    if nz.size == 0 or lead_periods <= 0:
        return np.zeros(n)
    rng = np.random.default_rng(seed)
    st = (s > 0).astype(int)
    cnt = np.ones((2, 2)) * 0.0
    for a, b in zip(st[:-1], st[1:]):
        cnt[a, b] += 1
    p1 = np.array([cnt[0, 1] / cnt[0].sum() if cnt[0].sum() else nz.size / s.size, cnt[1, 1] / cnt[1].sum() if cnt[1].sum() else nz.size / s.size])
    state = np.full(n, int(st[-1]))
    total = np.zeros(n)
    for _ in range(int(lead_periods)):
        state = (rng.random(n) < p1[state]).astype(int)
        x = rng.choice(nz, size=n)
        jit = np.maximum(np.rint(x + rng.standard_normal(n) * np.sqrt(x)), 1.0)
        total += np.where(state == 1, jit, 0.0)
    return total


@dataclass
class DemandForecast:
    kind: str
    station: str
    events: int
    weeks: int
    adi: float
    cv2: float
    klass: str
    method: str
    per_week: float
    vmr: float
    nff_share: float
    lead_days: float = 0.0
    ltd_mean: float = 0.0
    ltd_q: dict[str, float] = field(default_factory=dict)

    @property
    def per_day(self) -> float:
        return self.per_week / WEEK_DAYS

    def row(self) -> dict[str, Any]:
        return {"kind": self.kind, "part": KIND_PART.get(self.kind), "station": self.station, "events": self.events, "weeks": self.weeks,
                "adi": None if math.isinf(self.adi) else round(self.adi, 3), "cv2": round(self.cv2, 3), "class": self.klass,
                "method": self.method, "forecast_per_week": round(self.per_week, 4), "forecast_per_year": round(self.per_week * 52.18, 2),
                "variance_to_mean": round(self.vmr, 3), "nff_share": round(self.nff_share, 3), "lead_time_days": round(self.lead_days, 1),
                "lead_time_demand_mean": round(self.ltd_mean, 3), "lead_time_demand_quantiles": {k: round(v, 2) for k, v in self.ltd_q.items()}}


def forecast_series(kind: str, station: str, series: Sequence[float], nff_share: float = 0.0, lead_days: float = 0.0) -> DemandForecast:
    """Forecast one weekly series; lead-time demand mean and p50/p90/p95 from the bootstrap over ``lead_days``."""
    s = [float(x) for x in series]
    adi, cv2 = adi_cv2(s)
    klass = classify(adi, cv2)
    method = choose_method(klass)
    nz = sum(1 for x in s if x > 0)
    if method == "croston":
        pw = croston(s)
    elif method == "sba":
        pw = sba(s)
    elif method == "bootstrap":
        pw = float(np.mean(s))  # bootstrap resamples the observed history: its per-period mean is the sample mean
    else:
        pw = 0.0
    mean = sum(s) / len(s) if s else 0.0
    vmr = max(1.0, float(np.var(s)) / mean) if mean > 0 and len(s) > 1 else 1.0
    f = DemandForecast(kind, station, int(sum(s)), len(s), adi, cv2, klass, method, pw, vmr, nff_share, lead_days)
    f.ltd_mean = pw * lead_days / WEEK_DAYS
    if nz and lead_days > 0:
        ltd = bootstrap_ltd(s, int(math.ceil(lead_days / WEEK_DAYS)), seed=_seed(kind, station))
        f.ltd_q = {"p50": float(np.quantile(ltd, 0.5)), "p90": float(np.quantile(ltd, 0.9)), "p95": float(np.quantile(ltd, 0.95))}
    return f


def home_station(tail: str, stations: Sequence[str]) -> str:
    """Golden-fleet convention: tail SIM-i is based at stations[(i-1) % n]. Removal records carry no station."""
    m = re.search(r"(\d+)$", tail or "")
    i = int(m.group(1)) if m else 1
    return stations[(i - 1) % len(stations)]


def build_series(ds: Any, stations: Sequence[str]) -> tuple[dict[tuple[str, str], list[int]], dict[str, float], int]:
    """Weekly removal counts per (kind, station), NFF share per kind, number of weeks. ``ds`` is a reliability-pack Dataset."""
    from app.sim.clock import START

    weeks = max(1, int(ds.exposure.days // WEEK_DAYS))
    out: dict[tuple[str, str], list[int]] = {}
    nff: dict[str, list[int]] = {}
    for r in ds.removals:
        if not r.is_removal or r.kind not in KIND_PART:
            continue
        st = home_station(r.tail, stations)
        w = min(weeks - 1, max(0, int((r.time - START).total_seconds() // (86400 * WEEK_DAYS))))
        out.setdefault((r.kind, st), [0] * weeks)[w] += 1
        c = nff.setdefault(r.kind, [0, 0])
        c[0] += 1
        c[1] += int(r.nff or r.action == "nff_return")
    share = {k: (v[1] / v[0] if v[0] else 0.0) for k, v in nff.items()}
    return out, share, weeks


# ---------------- two-echelon model ----------------
@dataclass(frozen=True)
class Node:
    station: str
    lam: float            # demand per day
    ship_days: float      # depot -> station transport
    vmr: float = 1.0
    role: str = "outstation"
    current: Optional[int] = None


@dataclass
class Pool:
    kind: str
    part: str
    price: float
    depot: str
    nodes: list[Node]
    tat_days: float
    nff_share: float = 0.0
    condemned: float = 0.0
    nff_tat_days: float = 3.0
    buy_days: float = 90.0

    def node(self, station: str) -> Node:
        return next(n for n in self.nodes if n.station == station)


def _pmf(mean: float, var: float) -> np.ndarray:
    n = int(mean + 10.0 * math.sqrt(max(var, mean)) + 30)
    k = np.arange(n)
    if mean <= 0:
        out = np.zeros(n)
        out[0] = 1.0
        return out
    if var <= mean * (1.0 + 1e-9):
        return stats.poisson.pmf(k, mean)
    r = mean * mean / (var - mean)
    return stats.nbinom.pmf(k, r, r / (r + mean))


def _ebo(pmf: np.ndarray, s: int) -> float:
    k = np.arange(len(pmf))
    return float(np.sum(np.maximum(k - s, 0) * pmf))


def _fill(pmf: np.ndarray, s: int) -> float:
    return float(np.sum(pmf[:s])) if s > 0 else 0.0


def evaluate(pool: Pool, stock: dict[str, int], tat_scale: float = 1.0, dem_scale: float = 1.0) -> dict[str, Any]:
    """VARI-METRIC evaluation of ``stock`` (station -> units; the depot station's value is the central depot stock).

    Depot pipeline (units in transit to the depot or in repair / repurchase) is NB with mean sum(lam_i*ship_i) + lam0*Trep. A base's
    pipeline is demand over (ship + mean depot delay EBO0/lam0), with Sherbrooke's variance term for the random depot delay.
    """
    nodes = [Node(n.station, n.lam * dem_scale, n.ship_days, n.vmr, n.role, n.current) for n in pool.nodes]
    lam0 = sum(n.lam for n in nodes)
    rep = pool.nff_share * pool.nff_tat_days * tat_scale + (1 - pool.nff_share) * ((1 - pool.condemned) * pool.tat_days * tat_scale + pool.condemned * pool.buy_days)
    vmr0 = sum(n.lam * n.vmr for n in nodes) / lam0 if lam0 > 0 else 1.0
    mu0 = sum(n.lam * n.ship_days for n in nodes) + lam0 * rep
    pm0 = _pmf(mu0, mu0 * vmr0)
    s0 = int(stock.get(pool.depot, 0))
    ebo0 = _ebo(pm0, s0)
    k = np.arange(len(pm0))
    var_b0 = float(np.sum(np.maximum(k - s0, 0) ** 2 * pm0)) - ebo0 ** 2
    d0 = ebo0 / lam0 if lam0 > 0 else 0.0
    rows: dict[str, dict[str, float]] = {}
    for n in nodes:
        if n.lam <= 0:
            rows[n.station] = {"fill": 1.0, "ebo": 0.0, "pipeline_mean": 0.0}
        elif n.station == pool.depot:
            rows[n.station] = {"fill": _fill(pm0, s0), "ebo": ebo0 * n.lam / lam0, "pipeline_mean": mu0}
        else:
            m = n.lam * (n.ship_days + d0)
            v = max(m, n.lam * n.ship_days * n.vmr + n.lam * d0 + (n.lam / lam0) ** 2 * max(var_b0 - ebo0, 0.0))
            pm = _pmf(m, v)
            si = int(stock.get(n.station, 0))
            rows[n.station] = {"fill": _fill(pm, si), "ebo": _ebo(pm, si), "pipeline_mean": m}
    return {"stations": rows, "depot_ebo": ebo0, "depot_delay_days": d0, "objective": sum(r["ebo"] for r in rows.values()), "lam0": lam0}


def aog_risk(lam: float, fill: float, horizon_days: float) -> float:
    """P(at least one stock-out in the horizon), stock-outs taken as Poisson with rate lam*(1-fill)."""
    return 1.0 - math.exp(-lam * horizon_days * (1.0 - fill))


# ---------------- greedy marginal analysis ----------------
@dataclass
class Plan:
    stock: dict[str, dict[str, int]]
    mode: str
    target_fill: Optional[float]
    budget_usd: Optional[float]
    spend_usd: float
    budget_exhausted: bool = False


def recommend(pools: Sequence[Pool], target_fill: Optional[float] = 0.95, budget_usd: Optional[float] = None, assumptions: Optional[dict[str, Any]] = None) -> Plan:
    """Greedy marginal analysis across all pools: add the unit with the largest EBO reduction per dollar.

    * ``budget_usd`` set: spend the budget (and, when ``target_fill`` is also set, stop early once every station meets it).
    * only ``target_fill``: add units until every demand-bearing station meets it.
    * neither: add units while the yearly AOG-cost saving of one more unit exceeds its yearly holding cost.
    """
    a = {**ASSUMPTIONS, **(assumptions or {})}
    aog_year = 8760.0 * a["aog_cost_usd_per_hour"]
    stock = {p.kind: {n.station: 0 for n in p.nodes} for p in pools}
    cur = {p.kind: evaluate(p, stock[p.kind]) for p in pools}
    byk = {p.kind: p for p in pools}
    mode = "budget" if budget_usd is not None else ("target" if target_fill is not None else "cost")
    spend = 0.0
    exhausted = False
    cache: dict[tuple[str, str], float] = {}
    for _ in range(5000):
        best: tuple[float, str, str, float] | None = None
        for p in pools:
            ev = cur[p.kind]
            unmet = [n.station for n in p.nodes if n.lam > 0 and ev["stations"][n.station]["fill"] < (target_fill or 0.0) - 1e-12]
            for n in p.nodes:
                if n.lam <= 0 or stock[p.kind][n.station] >= MAX_UNITS:
                    continue
                if target_fill is not None and mode != "budget" and not (n.station in unmet or (n.station == p.depot and unmet)):
                    continue
                if mode == "budget" and target_fill is not None and not unmet:
                    continue
                if budget_usd is not None and spend + p.price > budget_usd + 1e-9:
                    exhausted = True
                    continue
                key = (p.kind, n.station)
                if key not in cache:
                    trial = dict(stock[p.kind])
                    trial[n.station] += 1
                    cache[key] = ev["objective"] - evaluate(p, trial)["objective"]
                g = cache[key]
                if g <= 1e-12:
                    continue
                if mode == "cost" and g * aog_year <= p.price * a["holding_rate_per_year"]:
                    continue
                ratio = g / p.price
                if best is None or ratio > best[0] + 1e-15:
                    best = (ratio, p.kind, n.station, p.price)
        if best is None:
            break
        _, k, st, price = best
        stock[k][st] += 1
        spend += price
        cur[k] = evaluate(byk[k], stock[k])
        for key in [c for c in cache if c[0] == k]:
            del cache[key]
    return Plan(stock, mode, target_fill, budget_usd, spend, exhausted)


def describe(pool: Pool, stock: dict[str, int], assumptions: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """Per-station rows for a stock vector: fill, EBO, AOG risk, holding cost and TAT +/-30% / demand +/-20% sensitivities."""
    a = {**ASSUMPTIONS, **(assumptions or {})}
    h = float(a["horizon_days"])
    base = evaluate(pool, stock)
    variants = {"tat_-30": (0.7, 1.0), "tat_+30": (1.3, 1.0), "demand_-20": (1.0, 0.8), "demand_+20": (1.0, 1.2)}
    sens = {k: evaluate(pool, stock, *v) for k, v in variants.items()}
    out = []
    for n in pool.nodes:
        r = base["stations"][n.station]
        s = int(stock.get(n.station, 0))
        out.append({
            "kind": pool.kind, "part": pool.part, "station": n.station, "role": "depot" if n.station == pool.depot else n.role,
            "recommended": s, "current": n.current, "demand_per_year": round(n.lam * 365.0, 2), "fill_rate": round(r["fill"], 4),
            "ebo": round(r["ebo"], 4), "aog_risk": round(aog_risk(n.lam, r["fill"], h), 4), "unit_price_usd": pool.price,
            "holding_cost_usd_year": round(s * pool.price * a["holding_rate_per_year"], 2),
            "expected_aog_cost_usd_year": round(r["ebo"] * 8760.0 * a["aog_cost_usd_per_hour"], 2),
            "sensitivity": {k: {"fill_rate": round(v["stations"][n.station]["fill"], 4), "ebo": round(v["stations"][n.station]["ebo"], 4),
                                "aog_risk": round(aog_risk(n.lam * variants[k][1], v["stations"][n.station]["fill"], h), 4)} for k, v in sens.items()},
        })
    return out


def network_fill(pool: Pool, stock: dict[str, int], **kw: float) -> float:
    ev = evaluate(pool, stock, **kw)
    lam = sum(n.lam for n in pool.nodes)
    return sum(n.lam * ev["stations"][n.station]["fill"] for n in pool.nodes) / lam if lam > 0 else 1.0


# ---------------- Monte-Carlo validation ----------------
def simulate(pool: Pool, stock: dict[str, int], trials: int = 500, seed: int = 7, horizon_days: float = 365.0, warmup_days: float = 120.0,
             demand_cv: float = 0.0, campaign: int = 1, assumptions: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Discrete-event simulation of the one-for-one network (infinite repair capacity, FIFO depot queue).

    ``demand_cv`` > 0 multiplies every trial's demand by a Gamma frailty (mean 1) = extra variance; ``campaign`` > 1 makes removals
    arrive in simultaneous clusters of that size (same mean rate). Fill = share of demands met from stock on arrival.
    """
    a = {**ASSUMPTIONS, **(assumptions or {})}
    rng = np.random.default_rng(seed)
    nodes = pool.nodes
    idx = {n.station: i for i, n in enumerate(nodes)}
    dep = idx[pool.depot]
    ship = [0.0 if i == dep else n.ship_days for i, n in enumerate(nodes)]
    total = horizon_days + warmup_days
    sig = math.sqrt(math.log(1.0 + a["repair_cv"] ** 2))
    dem = np.zeros(len(nodes))
    fil = np.zeros(len(nodes))
    hit = np.zeros(len(nodes))
    for _ in range(trials):
        mult = rng.gamma(1.0 / demand_cv ** 2, demand_cv ** 2) if demand_cv > 0 else 1.0
        ev: list[tuple[float, int]] = []
        for j, n in enumerate(nodes):
            k = rng.poisson(n.lam * mult / campaign * total)
            for t in rng.uniform(0.0, total, k):
                ev.extend([(float(t), j)] * campaign)
        ev.sort()
        m = len(ev)
        u = rng.random(m)
        z = rng.lognormal(0.0, sig, m) / math.exp(sig * sig / 2.0)
        s = [int(stock.get(n.station, 0)) for n in nodes]
        s[dep] = 0
        d = int(stock.get(pool.depot, 0))
        bo = [0] * len(nodes)
        queue: list[int] = []
        h: list[tuple[float, int, int]] = []
        c = 0
        flag = [False] * len(nodes)

        def drain(t: float) -> None:
            nonlocal d, c
            while h and h[0][0] <= t:
                tt, _, j = heapq.heappop(h)
                if j < 0:  # a unit becomes available at the depot
                    if queue:
                        r = queue.pop(0)
                        if r == dep:
                            bo[dep] -= 1
                        else:
                            c += 1
                            heapq.heappush(h, (tt + ship[r], c, r))
                    else:
                        d += 1
                elif bo[j] > 0:
                    bo[j] -= 1
                else:
                    s[j] += 1

        for i, (t, j) in enumerate(ev):
            drain(t)
            ok = (d > 0) if j == dep else (s[j] > 0)
            if t >= warmup_days:
                dem[j] += 1
                fil[j] += ok
                flag[j] = flag[j] or not ok
            if j == dep:
                if ok:
                    d -= 1
                else:
                    bo[dep] += 1
                    queue.append(dep)
            else:
                if ok:
                    s[j] -= 1
                else:
                    bo[j] += 1
                if d > 0:
                    d -= 1
                    c += 1
                    heapq.heappush(h, (t + ship[j], c, j))
                else:
                    queue.append(j)
            ui = u[i]
            if ui < pool.nff_share:
                back = t + ship[j] + pool.nff_tat_days * z[i]
            elif ui < pool.nff_share + (1 - pool.nff_share) * pool.condemned:
                back = t + pool.buy_days
            else:
                back = t + ship[j] + pool.tat_days * z[i]
            c += 1
            heapq.heappush(h, (back, c, -1))
        hit += np.array(flag, dtype=float)
    out = {}
    for i, n in enumerate(nodes):
        out[n.station] = {"fill": float(fil[i] / dem[i]) if dem[i] > 0 else 1.0, "demands": int(dem[i]), "aog_risk": float(hit[i] / trials)}
    tot = float(dem.sum())
    return {"stations": out, "fill": float(fil.sum() / tot) if tot > 0 else 1.0, "trials": trials, "seed": seed}


def validate(pool: Pool, stock: dict[str, int], trials: int = 500, seed: int = 7, assumptions: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Analytic vs simulated fill for the Poisson base case and two stress cases where the approximation is expected to break."""
    a = {**ASSUMPTIONS, **(assumptions or {})}
    ana = evaluate(pool, stock)
    lam = sum(n.lam for n in pool.nodes)
    ana_fill = sum(n.lam * ana["stations"][n.station]["fill"] for n in pool.nodes) / lam if lam > 0 else 1.0
    cases = {"base": {}, "high_variance": {"demand_cv": 0.6}, "campaign_x3": {"campaign": 3}}
    out = []
    for name, kw in cases.items():
        sim = simulate(pool, stock, trials, seed, a["horizon_days"], assumptions=assumptions, **kw)
        err = ana_fill - sim["fill"]
        out.append({"case": name, "analytic_fill": round(ana_fill, 4), "simulated_fill": round(sim["fill"], 4), "error_pp": round(100 * err, 2),
                    "within_3pp": abs(err) <= 0.03, "per_station": {s: {"analytic": round(ana["stations"][s]["fill"], 4), "simulated": round(v["fill"], 4)}
                                                                  for s, v in sim["stations"].items()}})
    return {"trials": trials, "seed": seed, "cases": out,
            "breaks_when": "demand variance well above Poisson (demand_cv case) and campaign removals (cluster size 3): the NB pipeline "
                           "uses the observed weekly variance-to-mean only and cannot see trial-level correlation or simultaneous clusters"}


# ---------------- integration with the simulator ----------------
def _dataset(src: Any) -> Any:
    from app.insights import reliability_pack as rp

    if isinstance(src, rp.Dataset):
        return src
    if isinstance(src, (str, Path)):
        return rp.from_run_dir(src)
    return rp.from_world(src)


def _ship_days(part: str, station: Any, depot: str, coords: dict[str, tuple[float, float]]) -> float:
    ps = next((p for p in station.parts if p.part == part), None)
    if ps is not None and ps.ship_from:
        return ps.ship_hours / 24.0
    a, b = coords.get(station.iata), coords.get(depot)
    if a is None or b is None or station.iata == depot:
        return 0.0 if station.iata == depot else 12.0 / 24.0
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    hv = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    km = 2 * 6371.0 * math.asin(math.sqrt(hv))
    return max(4.0, 3.0 + km / 700.0) / 24.0


def build_pools(ds: Any, stations: Sequence[Any], assumptions: Optional[dict[str, Any]] = None) -> tuple[list[Pool], list[DemandForecast]]:
    """Pools (one per kind) and the demand forecasts from a Dataset and a list of ``Station``."""
    a = {**ASSUMPTIONS, **(assumptions or {})}
    names = [s.iata for s in stations]
    series, nff, weeks = build_series(ds, names)
    coords = {s.iata: (s.lat, s.lon) for s in stations}
    pools: list[Pool] = []
    fcs: list[DemandForecast] = []
    for kind, part in KIND_PART.items():
        holders = [s for s in stations if any(p.part == part and p.ship_from is None for p in s.parts)]
        depot = max(holders, key=lambda s: next(p.qty for p in s.parts if p.part == part)).iata if holders else (DEFAULT_DEPOT if DEFAULT_DEPOT in names else names[0])
        tat = float(a["shop_tat_days"].get(kind, 20.0))
        nodes = []
        for st in stations:
            ps = next((p for p in st.parts if p.part == part), None)
            ship = _ship_days(part, st, depot, coords)
            lead = ship + tat
            f = forecast_series(kind, st.iata, series.get((kind, st.iata), [0] * weeks), nff.get(kind, 0.0), lead)
            fcs.append(f)
            nodes.append(Node(st.iata, f.per_day, ship, f.vmr, "main_base" if st.hangar_slots >= 2 else "outstation", ps.qty if ps is not None else None))
        pools.append(Pool(kind, part, PART_PRICE_USD.get(part, 10_000.0), depot, nodes, tat, nff.get(kind, 0.0), a["condemned_fraction"],
                          a["nff_tat_days"], a["buy_lead_days"]))
    return pools, fcs


def from_simulator(src: Any = None, stations: Optional[Sequence[Any]] = None, target_fill: Optional[float] = 0.95, budget_usd: Optional[float] = None,
                   assumptions: Optional[dict[str, Any]] = None, validate_trials: int = 0) -> dict[str, Any]:
    """Recommendation + current-vs-recommended comparison. ``src``: reliability Dataset, a v2 World, a run directory, or None for
    the live world / cached deterministic backfill."""
    return _compute(src, stations, target_fill, budget_usd, assumptions, validate_trials)[0]


def _compute(src: Any, stations: Optional[Sequence[Any]], target_fill: Optional[float], budget_usd: Optional[float],
             assumptions: Optional[dict[str, Any]], validate_trials: int) -> tuple[dict[str, Any], list[DemandForecast]]:
    from app.insights import reliability_pack as rp
    from app.solver.golden import golden_state

    if src is None:
        w = rp.live_world()
        ds = rp.from_world(w, {"basis": "live", "engine": "v2"}) if w is not None else rp.cached_backfill()
    else:
        ds = _dataset(src)
    sts = list(stations) if stations is not None else golden_state().stations
    pools, fcs = build_pools(ds, sts, assumptions)
    plan = recommend(pools, target_fill, budget_usd, assumptions)
    rows: list[dict[str, Any]] = []
    kinds: list[dict[str, Any]] = []
    for p in pools:
        r = describe(p, plan.stock[p.kind], assumptions)
        rows += r
        lam = sum(n.lam for n in p.nodes)
        ev = evaluate(p, plan.stock[p.kind])
        cur = {n.station: (n.current if n.current is not None else 0) for n in p.nodes}
        cev = evaluate(p, cur) if any(n.current is not None for n in p.nodes) else None
        kinds.append({"kind": p.kind, "part": p.part, "depot": p.depot, "unit_price_usd": p.price, "demand_per_year": round(lam * 365, 2),
                      "recommended_units": sum(plan.stock[p.kind].values()), "fill_rate": round(network_fill(p, plan.stock[p.kind]), 4),
                      "current_fill_rate": None if cev is None else round(sum(n.lam * cev["stations"][n.station]["fill"] for n in p.nodes) / lam, 4) if lam > 0 else 1.0,
                      "total_ebo": round(ev["objective"], 4), "depot_delay_days": round(ev["depot_delay_days"], 2),
                      "net_pipeline": {"nff_share": round(p.nff_share, 3), "condemned_share_of_non_nff": p.condemned,
                                       "repairable_return_fraction": round(p.nff_share + (1 - p.nff_share) * (1 - p.condemned), 3),
                                       "condemned_fraction": round((1 - p.nff_share) * p.condemned, 3)}})
    shortfalls = [{"kind": r["kind"], "part": r["part"], "station": r["station"], "current": r["current"], "recommended": r["recommended"],
                   "shortfall": r["recommended"] - r["current"]} for r in rows if r["current"] is not None and r["recommended"] > r["current"]]
    comparison = [{"kind": r["kind"], "part": r["part"], "station": r["station"], "current": r["current"], "recommended": r["recommended"],
                   "delta": None if r["current"] is None else r["recommended"] - r["current"]} for r in rows]
    tot_lam = sum(n.lam for p in pools for n in p.nodes)
    fill = sum(n.lam * evaluate(p, plan.stock[p.kind])["stations"][n.station]["fill"] for p in pools for n in p.nodes) / tot_lam if tot_lam > 0 else 1.0
    out: dict[str, Any] = {
        "simulated": True, "provenance": PROVENANCE, "disclaimer": DISCLAIMER,
        "source": {**ds.source}, "assumptions": {k: v for k, v in {**ASSUMPTIONS, **(assumptions or {})}.items()},
        "policy": {"mode": plan.mode, "target_fill": plan.target_fill, "budget_usd": plan.budget_usd, "budget_exhausted": plan.budget_exhausted},
        "summary": {"network_fill_rate": round(fill, 4), "recommended_units": sum(sum(v.values()) for v in plan.stock.values()),
                    "recommended_value_usd": round(plan.spend_usd, 2),
                    "holding_cost_usd_year": round(sum(r["holding_cost_usd_year"] for r in rows), 2),
                    "expected_aog_cost_usd_year": round(sum(r["expected_aog_cost_usd_year"] for r in rows), 2),
                    "max_station_aog_risk": max((r["aog_risk"] for r in rows), default=0.0),
                    "network_aog_risk": round(1.0 - math.prod(1.0 - r["aog_risk"] for r in rows), 4)},
        "kinds": kinds, "items": rows, "comparison": comparison, "shortfalls": shortfalls,
        "unmodelled_kinds": ["engine", "landing_gear"],
    }
    if validate_trials > 0:
        out["validation"] = [{"kind": p.kind, **validate(p, plan.stock[p.kind], validate_trials, assumptions=assumptions)} for p in pools
                             if sum(n.lam for n in p.nodes) > 0]
    return out, fcs


def demand_report(src: Any = None, stations: Optional[Sequence[Any]] = None, assumptions: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    out, fcs = _compute(src, stations, 0.95, None, assumptions, 0)
    return {"simulated": True, "provenance": PROVENANCE, "disclaimer": DISCLAIMER, "source": out["source"],
            "rule": f"ADI < {ADI_CUT} and CV2 < {CV2_CUT}: smooth, Croston; ADI < {ADI_CUT}, CV2 >= {CV2_CUT}: erratic, Croston; "
                    f"ADI >= {ADI_CUT}, CV2 < {CV2_CUT}: intermittent, SBA; both above: lumpy, bootstrap. alpha={ALPHA}, weekly buckets.",
            "forecasts": [f.row() for f in fcs]}
