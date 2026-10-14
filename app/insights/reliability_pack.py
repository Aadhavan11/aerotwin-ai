"""Industry-standard reliability metrics (MTBUR, MTBF, NFF rate, removal rate with Poisson CI, AMOS-style alerts, Pareto,
repeat defects) computed from v2 World / backfill-runner outputs. SIMULATED data; advisory only.

Definitions (all counts are *removals* = actions ``replace`` or ``overhaul``; on-wing repairs and inspections are not):
* unscheduled removal: ``Removal.unscheduled`` (reason failure / inspection_finding / nff).
* MTBUR = exposure / unscheduled removals, in flight hours (FH) and flight cycles (FC); exposure is the sum over the
  units of the kind (per ATA chapter: sum over the kinds in the chapter, i.e. unit-hours).
* MTBF = exposure / confirmed failures (reason ``failure`` and not NFF).
* NFF rate = NFF removals / unscheduled removals.
* premature-removal rate = removals of parts still above ``PREMATURE_HEALTH`` / all removals.
* removal rate per 1000 FH with an exact (Garwood) two-sided Poisson interval.
* AMOS-style alert: removals bucketed into 30-day periods, rate per 1000 FH per period (flight hours are spread uniformly
  over the calendar span); the latest complete period is compared with mean + 3 sigma of the trailing window (<= 12
  earlier periods; "warning" at +2 sigma). At least ``MIN_EVENTS`` removals in the latest period are needed to alert.
* repeat defect: >= 3 unscheduled removals of the same tail/position within 90 days.

Sources: a live ``World`` (``from_world``), a backfill run directory (``from_run_dir``; flight hours per cycle are
inferred from the removed parts' tsn/csn because only removals are stored) or ``backfill`` (deterministic cached 1-year
v2 run in-process, cache keyed by seed + config hash, at ``<DB dir>/cache``).
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import threading
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

from app import settings
from app.sim.clock import START
from app.sim.v2.drivers import LEGS_PER_DAY
from app.taxonomy import ata

PROVENANCE = "SIMULATED"
PERIOD_DAYS = 30
WINDOW = 12
MIN_WINDOW = 3
MIN_EVENTS = 2
SIGMA_ALERT = 3.0
SIGMA_WARNING = 2.0
PREMATURE_HEALTH = 0.5
REPEAT_N = 3
REPEAT_DAYS = 90
CONFIDENCE = 0.95
REMOVAL_ACTIONS = ("replace", "overhaul")
BACKFILL_YEARS = 1.0
CACHE_VERSION = 2
DISCLAIMER = "SIMULATED data; ATA-style, simplified, fictional part numbers. Advisory only, not a certified reliability analysis."


# ---------------- data ----------------
@dataclass
class RemovalRec:
    kind: str
    tail: str
    position: str
    action: str
    reason: str
    unscheduled: bool
    nff: bool
    health_before: float
    time: datetime
    tsn_h: float = 0.0
    csn: float = 0.0

    @property
    def is_removal(self) -> bool:
        return self.action in REMOVAL_ACTIONS

    @property
    def failure(self) -> bool:
        return self.reason == "failure" and not self.nff


@dataclass
class Exposure:
    units: dict[str, int]
    fh: dict[str, float]
    fc: dict[str, float]
    days: float


@dataclass
class Dataset:
    removals: list[RemovalRec]
    exposure: Exposure
    source: dict[str, Any] = field(default_factory=dict)


def _dt(s: str | None) -> datetime:
    if not s:
        return START
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _bool(v: Any) -> bool:
    return v if isinstance(v, bool) else str(v).strip().lower() in ("true", "1", "yes")


def from_world(world: Any, source: dict[str, Any] | None = None) -> Dataset:
    """Dataset from a live ``app.sim.v2.World`` (registry flight hours/cycles are the exposure)."""
    fh: dict[str, float] = defaultdict(float)
    fc: dict[str, float] = defaultdict(float)
    for p in world.registry.parts.values():
        fh[p.kind] += p.tsn_h
        fc[p.kind] += p.csn
    ctx = getattr(world, "_ctx", None)
    days = float(max(ctx.calendar_days)) if ctx is not None and len(ctx.calendar_days) else world.cycle / LEGS_PER_DAY
    recs = [RemovalRec(r.kind, r.tail or f"unit{r.unit}", r.position, r.action, r.removal_reason, bool(r.unscheduled), bool(r.nff),
                       float(r.health_before), _dt(r.time), float(r.tsn_h), float(r.csn)) for r in world.removals()]
    ex = Exposure(dict(world.counts), dict(fh), dict(fc), days)
    return Dataset(recs, ex, {"basis": "world", "cycles": int(world.cycle), "seed": int(world.seed), "tails": len(world.tails), **(source or {})})


def from_run_dir(path: str | Path) -> Dataset:
    """Dataset from ``data/sim_runs/<id>/`` (``manifest.json`` + ``removals.csv.gz``)."""
    d = Path(path)
    man = json.loads((d / "manifest.json").read_text())
    cycles = int(man["cycles"])
    tails = list(man["tails"])
    recs: list[RemovalRec] = []
    rp = d / "removals.csv.gz"
    if rp.exists():
        with gzip.open(rp, "rt", newline="") as f:
            for row in csv.DictReader(f):
                recs.append(RemovalRec(row["kind"], row.get("tail") or f"unit{row['unit']}", row["position"], row["action"],
                                       row["removal_reason"], _bool(row["unscheduled"]), _bool(row["nff"]), float(row["health_before"]),
                                       _dt(row.get("time")), float(row.get("tsn_h") or 0), float(row.get("csn") or 0)))
    from app.sim.v2.world import UNITS_PER_TAIL

    units = {k: len(tails) * UNITS_PER_TAIL.get(k, 1) for k in ata.KIND_SECTION}
    tot_h = sum(r.tsn_h for r in recs)
    tot_c = sum(r.csn for r in recs)
    overall = tot_h / tot_c if tot_c > 0 else 2.0
    fh, fc = {}, {}
    for k, n in units.items():
        h = sum(r.tsn_h for r in recs if r.kind == k)
        c = sum(r.csn for r in recs if r.kind == k)
        ratio = h / c if c > 0 else overall
        fc[k] = float(cycles * n)
        fh[k] = fc[k] * ratio
    return Dataset(recs, Exposure(units, fh, fc, cycles / LEGS_PER_DAY),
                   {"basis": "run_dir", "cycles": cycles, "seed": man.get("spec", {}).get("seed"), "run_id": man.get("run_id"),
                    "fh_per_cycle_inferred": True, "tails": len(tails)})


# ---------------- statistics ----------------
def _poisson_cdf(k: int, mu: float) -> float:
    if mu <= 0:
        return 1.0
    term = math.exp(-mu)
    total = term
    for i in range(1, k + 1):
        term *= mu / i
        total += term
    return min(total, 1.0)


def poisson_ci(n: int, level: float = CONFIDENCE) -> tuple[float, float]:
    """Exact (Garwood) two-sided confidence interval for a Poisson mean given ``n`` observed events."""
    a = (1.0 - level) / 2.0
    lo = 0.0
    if n > 0:
        a0, h = 0.0, n + 10.0 * math.sqrt(n) + 10.0
        for _ in range(100):
            m = 0.5 * (a0 + h)
            if 1.0 - _poisson_cdf(n - 1, m) < a:   # P(X >= n; m) too small -> raise mu
                a0 = m
            else:
                h = m
        lo = 0.5 * (a0 + h)
    a0, h = 0.0, n + 10.0 * math.sqrt(n) + 20.0
    for _ in range(100):
        m = 0.5 * (a0 + h)
        if _poisson_cdf(n, m) > a:                 # P(X <= n; m) still large -> raise mu
            a0 = m
        else:
            h = m
    return lo, 0.5 * (a0 + h)


def amos_threshold(window: Sequence[float]) -> tuple[float, float, float]:
    """(mean, sigma, mean + 3 sigma) of a trailing window; sigma is the sample standard deviation (0 for a single point)."""
    n = len(window)
    mean = sum(window) / n
    sigma = math.sqrt(sum((x - mean) ** 2 for x in window) / (n - 1)) if n > 1 else 0.0
    return mean, sigma, mean + SIGMA_ALERT * sigma


def amos_status(series: Sequence[float], latest_count: int | None = None, window: int = WINDOW) -> dict[str, Any]:
    """Alert status for the last element of ``series`` against the trailing window of earlier elements."""
    if len(series) < MIN_WINDOW + 1:
        return {"status": "insufficient_data", "latest": series[-1] if series else None, "threshold": None, "mean": None, "sigma": None,
                "warning_threshold": None, "window": max(len(series) - 1, 0)}
    win = list(series[-(window + 1):-1])
    mean, sigma, thr = amos_threshold(win)
    latest = float(series[-1])
    enough = latest_count is None or latest_count >= MIN_EVENTS
    status = "normal"
    if enough and latest > thr:
        status = "alert"
    elif enough and latest > mean + SIGMA_WARNING * sigma:
        status = "warning"
    return {"status": status, "latest": latest, "threshold": thr, "mean": mean, "sigma": sigma,
            "warning_threshold": mean + SIGMA_WARNING * sigma, "window": len(win)}


def _r(x: float | None, nd: int = 3) -> float | None:
    return None if x is None else round(float(x), nd)


def _ratio(num: float, den: float) -> float | None:
    return num / den if den > 0 else None


# ---------------- metrics ----------------
def _bucket(r: RemovalRec) -> int:
    return int((r.time - START).total_seconds() // (PERIOD_DAYS * 86400))


def _group_metrics(recs: list[RemovalRec], fh: float, fc: float, days: float) -> dict[str, Any]:
    rem = [r for r in recs if r.is_removal]
    uns = [r for r in rem if r.unscheduled]
    fail = [r for r in uns if r.failure]
    nff = [r for r in uns if r.nff]
    prem = [r for r in rem if r.health_before >= PREMATURE_HEALTH]
    lo, hi = poisson_ci(len(uns))
    per_k = 1000.0 / fh if fh > 0 else None
    n_periods = max(int(days // PERIOD_DAYS), 0)
    counts = [0] * n_periods
    for r in uns:
        b = _bucket(r)
        if 0 <= b < n_periods:
            counts[b] += 1
    fh_p = fh * PERIOD_DAYS / days if days > 0 else 0.0
    series = [c * 1000.0 / fh_p if fh_p > 0 else 0.0 for c in counts]
    alert = amos_status(series, counts[-1] if counts else None) if series else amos_status([])
    return {
        "flight_hours": _r(fh, 1), "flight_cycles": _r(fc, 1), "removals": len(rem), "unscheduled_removals": len(uns),
        "failures": len(fail), "nff_removals": len(nff),
        "mtbur_fh": _r(_ratio(fh, len(uns)), 1), "mtbur_fc": _r(_ratio(fc, len(uns)), 1),
        "mtbf_fh": _r(_ratio(fh, len(fail)), 1), "mtbf_fc": _r(_ratio(fc, len(fail)), 1),
        "nff_rate": _r(_ratio(len(nff), len(uns)), 4), "premature_removal_rate": _r(_ratio(len(prem), len(rem)), 4),
        "removal_rate_per_1000fh": _r(len(uns) * per_k if per_k else None, 4),
        "rate_ci_low": _r(lo * per_k if per_k else None, 4), "rate_ci_high": _r(hi * per_k if per_k else None, 4),
        "ci_level": CONFIDENCE, "alert_status": alert["status"],
    }, series, counts, alert


def compute(ds: Dataset) -> dict[str, Any]:
    ex = ds.exposure
    kinds = sorted(set(ex.units) | {r.kind for r in ds.removals})
    kinds = [k for k in kinds if k in ata.KIND_SECTION]
    per_kind, alerts = [], []
    for k in kinds:
        recs = [r for r in ds.removals if r.kind == k]
        m, series, counts, al = _group_metrics(recs, ex.fh.get(k, 0.0), ex.fc.get(k, 0.0), ex.days)
        ref = ata.ata_for(k)
        per_kind.append({"kind": k, "ata_chapter": ref.chapter, "ata_section": ref.section, "units": int(ex.units.get(k, 0)), **m})
        alerts.append({"kind": k, "ata_chapter": ref.chapter, "status": al["status"], "period_days": PERIOD_DAYS,
                       "latest_rate_per_1000fh": _r(al["latest"], 4), "latest_count": counts[-1] if counts else None,
                       "threshold_per_1000fh": _r(al["threshold"], 4), "warning_per_1000fh": _r(al["warning_threshold"], 4),
                       "window_mean_per_1000fh": _r(al["mean"], 4), "window_sigma_per_1000fh": _r(al["sigma"], 4),
                       "window_periods": al["window"], "rule": f"latest {PERIOD_DAYS}-day rate > mean + {SIGMA_ALERT:g} sigma of trailing {WINDOW} periods"})
    per_ata = []
    for ch in sorted({ata.chapter_for(k) for k in kinds}):
        ks = [k for k in kinds if ata.chapter_for(k) == ch]
        recs = [r for r in ds.removals if r.kind in ks]
        m, _, _, _ = _group_metrics(recs, sum(ex.fh.get(k, 0.0) for k in ks), sum(ex.fc.get(k, 0.0) for k in ks), ex.days)
        per_ata.append({"ata_chapter": ch, "title": ata.CHAPTERS[ch], "kinds": ks, "units": sum(int(ex.units.get(k, 0)) for k in ks), **m})
    total = sum(p["unscheduled_removals"] for p in per_ata)
    cum = 0
    pareto = []
    for p in sorted(per_ata, key=lambda p: (-p["unscheduled_removals"], p["ata_chapter"])):
        cum += p["unscheduled_removals"]
        pareto.append({"ata_chapter": p["ata_chapter"], "title": p["title"], "unscheduled_removals": p["unscheduled_removals"],
                       "share": _r(_ratio(p["unscheduled_removals"], total), 4), "cumulative_share": _r(_ratio(cum, total), 4)})
    cum = 0
    top = []
    for p in sorted(per_kind, key=lambda p: (-p["unscheduled_removals"], p["kind"])):
        cum += p["unscheduled_removals"]
        top.append({"kind": p["kind"], "ata_chapter": p["ata_chapter"], "unscheduled_removals": p["unscheduled_removals"],
                    "share": _r(_ratio(p["unscheduled_removals"], total), 4), "cumulative_share": _r(_ratio(cum, total), 4),
                    "removal_rate_per_1000fh": p["removal_rate_per_1000fh"], "nff_removals": p["nff_removals"]})
    by_tail: dict[str, int] = defaultdict(int)
    for r in ds.removals:
        if r.is_removal and r.unscheduled:
            by_tail[r.tail] += 1
    top_tails = [{"tail": t, "unscheduled_removals": n} for t, n in sorted(by_tail.items(), key=lambda kv: (-kv[1], kv[0]))[:10]]
    span = ex.days
    return {
        "as_of": (START + timedelta(days=span)).isoformat(), "simulated": True, "provenance": PROVENANCE, "disclaimer": DISCLAIMER,
        "source": {**ds.source, "span_days": _r(span, 1)}, "taxonomy_label": ata.LABEL,
        "metrics": {"per_kind": per_kind, "per_ata": per_ata},
        "pareto": pareto, "alerts": alerts, "repeat_defects": repeat_defects(ds.removals),
        "top_contributors": {"kinds": top, "tails": top_tails}, "unscheduled_removals_total": total,
    }


def repeat_defects(removals: Iterable[RemovalRec], n: int = REPEAT_N, days: int = REPEAT_DAYS) -> list[dict[str, Any]]:
    """Tail/position groups with >= n unscheduled removals inside any ``days``-day window (largest such window reported)."""
    groups: dict[tuple[str, str], list[RemovalRec]] = defaultdict(list)
    for r in removals:
        if r.unscheduled and r.is_removal:
            groups[(r.tail, r.position)].append(r)
    out = []
    span = timedelta(days=days)
    for (tail, pos), rs in groups.items():
        rs.sort(key=lambda r: r.time)
        best: list[RemovalRec] = []
        j = 0
        for i in range(len(rs)):
            while rs[i].time - rs[j].time > span:
                j += 1
            if i - j + 1 > len(best):
                best = rs[j:i + 1]
        if len(best) >= n:
            kind = best[0].kind
            out.append({"tail": tail, "kind": kind, "position": pos, "ata_chapter": ata.chapter_for(kind), "count": len(best),
                        "window_days": days, "first": best[0].time.isoformat(), "last": best[-1].time.isoformat(),
                        "reasons": sorted({r.reason for r in best}), "nff_count": sum(1 for r in best if r.nff)})
    out.sort(key=lambda d: (-d["count"], d["tail"], d["position"]))
    return out


# ---------------- Weibull (existing fits, exposed not edited) ----------------
def weibull_fits() -> list[dict[str, Any]]:
    """Per-kind Weibull fits from ``app.insights.reliability`` (unmodified) for the pack."""
    from app.insights import reliability

    try:
        return list(reliability.report()["kinds"])
    except Exception:  # noqa: BLE001  (the pack must not fail because the twin has no data)
        return []


# ---------------- backfill + cache ----------------
_LOCK = threading.Lock()
_MEM: dict[str, dict[str, Any]] = {}


def config_hash(seed: int, years: float, fleet: Any = None) -> str:
    cfg = {"v": CACHE_VERSION, "seed": int(seed), "years": float(years), "fleet": fleet, "legs_per_day": LEGS_PER_DAY, "initial_wear": True}
    return hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]


def _cache_dir() -> Path:
    return Path(settings.DB_PATH).parent / "cache"


def run_backfill(seed: int, years: float = BACKFILL_YEARS, fleet: Any = None) -> Dataset:
    """Step a fresh deterministic v2 world (same construction as the runner) without writing telemetry."""
    from app.sim.v2.runner import CYCLES_PER_YEAR, build_world

    w = build_world({"seed": int(seed), "fleet": fleet, "initial_wear": True})
    for _ in range(max(1, int(round(years * CYCLES_PER_YEAR)))):
        w.step()
        w.rows = []
    return from_world(w, {"basis": "backfill", "years": years})


def _ds_to_json(ds: Dataset) -> dict[str, Any]:
    return {"removals": [{**r.__dict__, "time": r.time.isoformat()} for r in ds.removals],
            "exposure": ds.exposure.__dict__, "source": ds.source}


def _ds_from_json(j: dict[str, Any]) -> Dataset:
    recs = [RemovalRec(**{**r, "time": datetime.fromisoformat(r["time"])}) for r in j["removals"]]
    return Dataset(recs, Exposure(**j["exposure"]), j["source"])


def cached_backfill(seed: int | None = None, years: float = BACKFILL_YEARS, fleet: Any = None) -> Dataset:
    """Deterministic backfill; cached in memory and on disk keyed by seed + config hash (first call ~15-20 s)."""
    seed = settings.SEED if seed is None else int(seed)
    key = config_hash(seed, years, fleet)
    with _LOCK:
        if key in _MEM:
            return _ds_from_json(_MEM[key])
        path = _cache_dir() / f"reliability_{key}.json"
        try:
            if path.exists():
                _MEM[key] = json.loads(path.read_text())
                return _ds_from_json(_MEM[key])
        except (OSError, ValueError, TypeError, KeyError):
            pass
        ds = run_backfill(seed, years, fleet)
        payload = _ds_to_json(ds)
        _MEM[key] = payload
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, sort_keys=True))
        except OSError:
            pass
        return ds


def live_world() -> Any | None:
    """The in-process live v2 world when SIM_ENGINE=v2 and it has stepped, else None."""
    if settings.SIM_ENGINE != "v2":
        return None
    try:
        from app.sim.router import simulator

        w = getattr(simulator.source, "world", None)
        return w if w is not None and w.cycle > 0 else None
    except Exception:  # noqa: BLE001
        return None


def pack(include_weibull: bool = True) -> dict[str, Any]:
    """The reliability pack for the live v2 world if available, else the cached deterministic 1-year backfill."""
    w = live_world()
    if w is not None:
        ds = from_world(w, {"basis": "live", "engine": "v2"})
    else:
        ds = cached_backfill()
        ds.source = {**ds.source, "engine": "v2", "cache_key": config_hash(settings.SEED, BACKFILL_YEARS)}
    out = compute(ds)
    out["weibull"] = weibull_fits() if include_weibull else []
    return out
