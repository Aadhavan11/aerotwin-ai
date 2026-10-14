"""Life-limited parts (LLP) tracking and engine shop-visit forecast on SIMULATED data. Advisory only.

* Each aircraft carries 12 LLPs: per engine a fan disk, HPC rotor stage, HPT disk and LPT shaft; per aircraft a main
  landing gear leg (left, right), the nose gear leg and the MLG retraction actuator.
* Part numbers, serials and limits are FICTIONAL documented priors (engine disks 20,000-30,000 cycles, gear 20,000-30,000
  landings, plus calendar limits). Never use for real maintenance planning.
* Cycles since new are seeded deterministically (``settings.SEED``, tail, position hash) as a pre-owned stagger and then
  consume the live fleet counters (engine component cycles for engine parts, ``Aircraft.cycles`` for gear parts).
* ``record_replacement`` resets a part (hook for the maintenance registry; ``apply_install_registry`` is tolerant of
  absent or partial registries).
* Shop-visit forecast per engine combines three drivers (earliest p50 wins): EGT margin trend, LLP limit, health RUL.
  The RUL model is capped (CMAPSS-style 125 cycles); a capped RUL is censored and is NOT used as a driver.
"""
from __future__ import annotations

import hashlib
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Sequence

import numpy as np

from app import settings

PROVENANCE = "SIMULATED"
DISCLAIMER = "SIMULATED data; fictional part numbers and documented prior limits. Advisory only, not a maintenance planning source."
WATCH_CYCLES = 2000.0
ALERT_CYCLES = 500.0
DAYS_PER_MONTH = 30.4375
HORIZON_MONTHS = 12
MIN_CPD = 0.05
MAX_DAYS = 36500.0  # clamp for date arithmetic
DEFAULT_CPD = 4.0  # flights/day when a tail has no legs
EGT_MARGIN_NEW_C = 70.0
EGT_MARGIN_FLOOR_C = 15.0
EGT_LOSS_PRIOR_C_PER_CYCLE = 0.007
TREND_WINDOW = 30
MIN_TREND_POINTS = 6
RUL_CAP = 125.0
SEED_LIFE_FRAC = 0.97
LLP_SPREAD = 0.08  # planning spread (+/-) on the LLP-driven shop-visit flights

# key -> (label, ATA chapter, scope, limit cycles, calendar limit years, part no stem)
CATALOG: dict[str, dict[str, Any]] = {
    "fan_disk": {"label": "Fan disk", "ata": "72-20", "scope": "engine", "limit": 30000.0, "years": 25.0, "pn": "FX-F"},
    "hpc_rotor": {"label": "HPC rotor stage", "ata": "72-30", "scope": "engine", "limit": 25000.0, "years": 22.0, "pn": "FX-C"},
    "hpt_disk": {"label": "HPT disk", "ata": "72-50", "scope": "engine", "limit": 20000.0, "years": 20.0, "pn": "FX-T"},
    "lpt_shaft": {"label": "LPT shaft", "ata": "72-50", "scope": "engine", "limit": 30000.0, "years": 25.0, "pn": "FX-S"},
    "mlg_leg_l": {"label": "MLG leg, left", "ata": "32-11", "scope": "aircraft", "limit": 25000.0, "years": 20.0, "pn": "FX-GL"},
    "mlg_leg_r": {"label": "MLG leg, right", "ata": "32-11", "scope": "aircraft", "limit": 25000.0, "years": 20.0, "pn": "FX-GR"},
    "nlg_leg": {"label": "Nose gear leg", "ata": "32-21", "scope": "aircraft", "limit": 30000.0, "years": 20.0, "pn": "FX-GN"},
    "mlg_retract_actuator": {"label": "MLG retraction actuator", "ata": "32-31", "scope": "aircraft", "limit": 20000.0, "years": 15.0, "pn": "FX-GA"},
}
ENGINE_KEYS = tuple(k for k, v in CATALOG.items() if v["scope"] == "engine")
AIRCRAFT_KEYS = tuple(k for k, v in CATALOG.items() if v["scope"] == "aircraft")

_lock = threading.RLock()
_replacements: dict[tuple[str, str], dict[str, Any]] = {}


# ---------------- helpers ----------------
def _h(*parts: object) -> int:
    return int.from_bytes(hashlib.sha256(":".join(str(p) for p in (settings.SEED, *parts)).encode()).digest()[:8], "big")


def _u(*parts: object) -> float:
    return (_h(*parts) % 1_000_000) / 1_000_000.0


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def status_for(remaining_cycles: float) -> str:
    """ok / watch (< 2000) / alert (< 500) / expired (<= 0), on effective remaining cycles."""
    if remaining_cycles <= 0:
        return "expired"
    if remaining_cycles < ALERT_CYCLES:
        return "alert"
    if remaining_cycles < WATCH_CYCLES:
        return "watch"
    return "ok"


# ---------------- replacement hook ----------------
def record_replacement(tail: str, position: str, at_cycles: float, when: Optional[datetime] = None, serial: Optional[str] = None) -> None:
    """A part at (tail, position) was replaced when its counter read ``at_cycles``; cycles since new restart at 0."""
    with _lock:
        _replacements[(tail, position)] = {"at_cycles": float(at_cycles), "when": when, "serial": serial}


def clear_replacements() -> None:
    with _lock:
        _replacements.clear()


def apply_install_registry(registry: Any, counters: Optional[dict[str, float]] = None) -> int:
    """Tolerant bridge from ``InstallRegistry``: parts with ``llp_limit_cycles`` and a known position key are treated as
    replaced (zero cycles since new) at the current counter of their tail. Returns the number of parts applied."""
    n = 0
    try:
        parts = list(getattr(registry, "parts", {}).values())
    except Exception:  # noqa: BLE001
        return 0
    for p in parts:
        tail = getattr(p, "installed_on", None)
        pos = getattr(p, "position", None)
        if not tail or not pos or getattr(p, "llp_limit_cycles", None) is None or getattr(p, "status", "installed") != "installed":
            continue
        base = (counters or {}).get(tail)
        if base is None:
            continue
        record_replacement(tail, str(pos), base - float(getattr(p, "csn", 0.0)), serial=getattr(p, "serial", None))
        n += 1
    return n


# ---------------- fleet inputs ----------------
@dataclass
class TailInputs:
    tail: str
    cycles: float
    cycles_per_day: float
    engine_cycles: dict[str, float] = field(default_factory=dict)  # "engine_1" -> cycles
    location: str = ""


def _aircraft() -> list[Any]:
    from app.insights import source

    if source._twin_has_data():
        from app.twin import state

        found = list(state.list_aircraft())
        if found:
            return found
    return list(source._generated(settings.SEED).aircraft)


def cycles_per_day_by_tail(legs: Iterable[Any]) -> dict[str, float]:
    """Observed utilisation: legs per tail over the span of the leg window (flights = cycles)."""
    legs = list(legs)
    if not legs:
        return {}
    t0 = min(lg.sched_dep for lg in legs)
    t1 = max(lg.sched_arr for lg in legs)
    span = max((t1 - t0).total_seconds() / 86400.0, 1.0)
    counts: dict[str, int] = {}
    for lg in legs:
        counts[lg.tail] = counts.get(lg.tail, 0) + 1
    return {t: n / span for t, n in counts.items()}


def fleet_inputs() -> tuple[list[TailInputs], list[Any], str, str]:
    """(per-tail inputs, engine ComponentStates, as_of iso, basis)."""
    from app.insights import source

    cpd = cycles_per_day_by_tail(source.legs())
    comps = [c for c in source.components() if c.kind == "engine"]
    eng: dict[str, dict[str, float]] = {}
    for c in comps:
        unit = c.component_id.split("/")[-1]
        eng.setdefault(c.tail, {})[unit] = float(c.cycles)
    out = []
    for a in _aircraft():
        out.append(TailInputs(a.tail, float(a.cycles), cpd.get(a.tail, DEFAULT_CPD), eng.get(a.tail, {}), getattr(a, "location", "")))
    return out, comps, source.as_of(), source.basis()


# ---------------- LLP model ----------------
def _positions(tail: TailInputs) -> list[tuple[str, str, str, float]]:
    """(position, catalog key, scope label, counter) for every LLP of the tail."""
    rows: list[tuple[str, str, str, float]] = []
    units = sorted(tail.engine_cycles) or ["engine_1", "engine_2"]
    for unit in units:
        counter = tail.engine_cycles.get(unit, tail.cycles)
        for key in ENGINE_KEYS:
            rows.append((f"{unit}:{key}", key, unit, counter))
    for key in AIRCRAFT_KEYS:
        rows.append((key, key, "airframe", tail.cycles))
    return rows


def part_state(tail: TailInputs, position: str, key: str, scope: str, counter: float, as_of: datetime) -> dict[str, Any]:
    """One part: deterministic stagger + live counter, then remaining cycles/days, limit date and status."""
    spec = CATALOG[key]
    limit = float(spec["limit"])
    rep = _replacements.get((tail.tail, position))
    if rep is not None:
        csn = max(counter - rep["at_cycles"], 0.0)
        stagger = 0.0
        replaced = True
    else:
        # pre-owned stagger; the part is assumed to have been replaced at SEED_LIFE_FRAC of its limit before (wrap), so
        # a live counter far above the limit never yields an impossible negative life at seed time
        wrap = limit * SEED_LIFE_FRAC
        stagger = wrap * _u(tail.tail, position, "stagger")
        csn = (counter + stagger) % wrap
        replaced = False
    cpd = max(tail.cycles_per_day, MIN_CPD)
    remaining_cycles = limit - csn
    age_days = min(csn / cpd, MAX_DAYS)
    installed = as_of - timedelta(days=age_days)
    if rep is not None and rep.get("when") is not None:
        installed = rep["when"]
    cal_limit_days = float(spec["years"]) * 365.25
    cal_date = installed + timedelta(days=cal_limit_days)
    cal_remaining_days = (cal_date - as_of).total_seconds() / 86400.0
    cyc_remaining_days = min(max(remaining_cycles / cpd, -MAX_DAYS), MAX_DAYS)
    if cal_remaining_days < cyc_remaining_days:
        driver, remaining_days = "calendar", cal_remaining_days
    else:
        driver, remaining_days = "cycles", cyc_remaining_days
    effective = min(remaining_cycles, cal_remaining_days * cpd)
    limit_date = as_of + timedelta(days=remaining_days)
    serial = (rep or {}).get("serial") or f"{spec['pn'][3:]}{_h(tail.tail, position, 'sn', 1 if replaced else 0) % 1_000_000:06d}"
    return {
        "tail": tail.tail,
        "position": position,
        "part_key": key,
        "label": spec["label"],
        "ata": spec["ata"],
        "scope": scope,
        "part_no": f"{spec['pn']}-{_h(key, 'pn') % 9000 + 1000}",
        "serial": serial,
        "cycle_limit": limit,
        "calendar_limit_years": float(spec["years"]),
        "csn": round(csn, 1),
        "remaining_cycles": round(remaining_cycles, 1),
        "remaining_days": round(remaining_days, 1),
        "cycles_per_day": round(cpd, 3),
        "limit_date": _iso(limit_date),
        "limit_driver": driver,
        "installed_date": _iso(installed),
        "replaced": replaced,
        "status": status_for(effective),
        "simulated": True,
        "provenance": PROVENANCE,
    }


def tail_parts(tail: TailInputs, as_of: datetime) -> list[dict[str, Any]]:
    return [part_state(tail, pos, key, scope, counter, as_of) for pos, key, scope, counter in _positions(tail)]


def summary(parts: Sequence[dict[str, Any]]) -> dict[str, int]:
    out = {"ok": 0, "watch": 0, "alert": 0, "expired": 0}
    for p in parts:
        out[p["status"]] += 1
    return out


def fleet_parts(tail: Optional[str] = None) -> dict[str, Any]:
    tails, _, as_of_s, basis = fleet_inputs()
    as_of = _parse(as_of_s)
    parts: list[dict[str, Any]] = []
    for t in tails:
        if tail is None or t.tail == tail:
            parts.extend(tail_parts(t, as_of))
    parts.sort(key=lambda p: (p["remaining_days"], p["tail"], p["position"]))
    return {"simulated": True, "provenance": PROVENANCE, "disclaimer": DISCLAIMER, "as_of": as_of_s, "basis": basis,
            "tail": tail, "count": len(parts), "summary": summary(parts), "parts": parts}


def tail_report(tail: str) -> Optional[dict[str, Any]]:
    tails, _, as_of_s, basis = fleet_inputs()
    t = next((x for x in tails if x.tail == tail), None)
    if t is None:
        return None
    parts = tail_parts(t, _parse(as_of_s))
    parts.sort(key=lambda p: (p["remaining_days"], p["position"]))
    return {"simulated": True, "provenance": PROVENANCE, "disclaimer": DISCLAIMER, "as_of": as_of_s, "basis": basis, "tail": tail,
            "aircraft": {"cycles": t.cycles, "cycles_per_day": round(t.cycles_per_day, 3), "location": t.location},
            "count": len(parts), "summary": summary(parts), "parts": parts}


# ---------------- EGT trend ----------------
def theil_sen(x: Sequence[float], y: Sequence[float]) -> tuple[float, float]:
    """(slope, relative sd of the slope) by Theil-Sen with a MAD-based slope standard error."""
    xa, ya = np.asarray(x, float), np.asarray(y, float)
    n = len(xa)
    if n < 3:
        return 0.0, 1.0
    i, j = np.triu_indices(n, 1)
    dx = xa[j] - xa[i]
    ok = dx != 0
    if not ok.any():
        return 0.0, 1.0
    slope = float(np.median((ya[j] - ya[i])[ok] / dx[ok]))
    resid = ya - (slope * xa + float(np.median(ya - slope * xa)))
    mad = float(np.median(np.abs(resid - np.median(resid)))) * 1.4826
    sx = float(np.std(xa)) * math.sqrt(n)
    se = mad / sx if sx > 0 else abs(slope)
    return slope, (se / abs(slope) if slope != 0 else 1.0)


def egt_trend(rows: Sequence[Any], health: Optional[float], engine_cycles: float, key: str) -> dict[str, Any]:
    """Margin now, loss rate (C/cycle), relative sd and basis. Uses ``egt_margin_c`` telemetry when it has enough points,
    else a latent prior: margin = min(prior wear from a deterministic shop-visit stagger, 15 + 55 * health)."""
    pts = [(r.cycle, r.values["egt_margin_c"]) for r in rows if "egt_margin_c" in getattr(r, "values", {})][-TREND_WINDOW:]
    prior = EGT_LOSS_PRIOR_C_PER_CYCLE
    if len(pts) >= MIN_TREND_POINTS:
        slope, rel = theil_sen([p[0] for p in pts], [p[1] for p in pts])
        loss = min(max(-slope, 0.5 * prior), 3.0 * prior)
        margin = float(np.median([p[1] for p in pts[-5:]]))
        return {"margin_c": margin, "loss_per_cycle": loss, "rel_sd": min(max(rel, 0.1), 0.6), "basis": "telemetry_theil_sen"}
    interval = (EGT_MARGIN_NEW_C - EGT_MARGIN_FLOOR_C) / prior
    since_shop = (engine_cycles + interval * _u(key, "shop_stagger")) % interval
    margin = EGT_MARGIN_NEW_C - prior * since_shop
    if health is not None:
        margin = min(margin, EGT_MARGIN_FLOOR_C + (EGT_MARGIN_NEW_C - EGT_MARGIN_FLOOR_C) * float(health))
    return {"margin_c": margin, "loss_per_cycle": prior, "rel_sd": 0.25, "basis": "latent_prior"}


# ---------------- shop-visit forecast ----------------
def forecast_engine(*, tail: str, engine: str, as_of: datetime, cycles_per_day: float, margin_c: float, loss_per_cycle: float,
                    loss_rel_sd: float, margin_basis: str, llp_remaining_cycles: float, llp_part: str,
                    rul: Optional[tuple[float, float, float]]) -> dict[str, Any]:
    """Pure forecast: drivers in flights (p10, p50, p90); earliest p50 wins; window = (min p10, min p90)."""
    drivers: dict[str, tuple[float, float, float]] = {}
    if loss_per_cycle > 0:
        room = margin_c - EGT_MARGIN_FLOOR_C
        fast = loss_per_cycle * (1.0 + 1.2816 * loss_rel_sd)
        slow = loss_per_cycle * max(1.0 - 1.2816 * loss_rel_sd, 0.2)
        drivers["egt_margin"] = (room / fast, room / loss_per_cycle, room / slow)
    r = llp_remaining_cycles
    drivers["llp"] = (r * (1 - LLP_SPREAD), r, r * (1 + LLP_SPREAD)) if r > 0 else (r, r, r)
    rul_censored = rul is None or rul[1] >= 0.99 * RUL_CAP
    if rul is not None and not rul_censored:
        drivers["rul"] = (float(rul[0]), float(rul[1]), float(rul[2]))
    driver = min(drivers, key=lambda k: drivers[k][1])
    p10 = min(d[0] for d in drivers.values())
    p50 = drivers[driver][1]
    p90 = min(d[2] for d in drivers.values())
    p10, p90 = min(p10, p50), max(p90, p50)
    cpd = max(cycles_per_day, 1e-6)

    def when(f: float) -> str:
        return _iso(as_of + timedelta(days=max(f, 0.0) / cpd))

    return {
        "tail": tail, "engine": engine, "component_id": f"{tail}/{engine}",
        "driver": driver, "driver_llp_part": llp_part if driver == "llp" else None,
        "overdue": p50 <= 0,
        "p10_flights": round(p10, 1), "p50_flights": round(p50, 1), "p90_flights": round(p90, 1),
        "p10_date": when(p10), "p50_date": when(p50), "p90_date": when(p90),
        "cycles_per_day": round(cycles_per_day, 3),
        "drivers": {k: {"p10_flights": round(v[0], 1), "p50_flights": round(v[1], 1), "p90_flights": round(v[2], 1)} for k, v in drivers.items()},
        "egt_margin": {"margin_c": round(margin_c, 1), "loss_c_per_cycle": round(loss_per_cycle, 5), "basis": margin_basis, "floor_c": EGT_MARGIN_FLOOR_C},
        "rul_censored": rul_censored,
        "simulated": True, "provenance": PROVENANCE,
    }


def _cdf(row: dict[str, Any], flights: np.ndarray) -> np.ndarray:
    p10, p50, p90 = (max(float(row[k]), 0.0) for k in ("p10_flights", "p50_flights", "p90_flights"))
    e = 1e-6
    hi = p90 + max(p90 - p50, 1.0)
    xp = np.array([0.0, p10 + e, p50 + 2 * e, p90 + 3 * e, hi + 4 * e])
    fp = np.array([0.0, 0.1, 0.5, 0.9, 1.0])
    return np.interp(flights, xp, fp)


def pipeline(rows: Sequence[dict[str, Any]], months: int = HORIZON_MONTHS) -> list[dict[str, Any]]:
    """Per month: expected shop visits (sum of per-engine probability mass, smoothed), cumulative expected, and the
    hard p50 count. Overdue engines land in month 1."""
    out: list[dict[str, Any]] = []
    prev = np.zeros(len(rows))
    cum_exp = 0.0
    cum_p50 = 0
    for m in range(1, months + 1):
        cur = np.array([float(_cdf(r, np.array([m * DAYS_PER_MONTH * r["cycles_per_day"]]))[0]) for r in rows]) if rows else np.zeros(0)
        exp = float((cur - prev).sum())
        prev = cur
        cum_exp += exp
        n50 = sum(1 for r in rows if (m - 1) * DAYS_PER_MONTH * r["cycles_per_day"] < max(r["p50_flights"], 0.0) <= m * DAYS_PER_MONTH * r["cycles_per_day"]
                  or (m == 1 and r["p50_flights"] <= 0))
        cum_p50 += n50
        out.append({"month": m, "days_from": round((m - 1) * DAYS_PER_MONTH, 1), "days_to": round(m * DAYS_PER_MONTH, 1),
                    "expected": round(exp, 3), "cumulative_expected": round(cum_exp, 3), "p50_count": n50, "cumulative_p50_count": cum_p50})
    return out


def shop_visits(tail: Optional[str] = None) -> dict[str, Any]:
    from app.insights import source
    from app.ml import health as health_mod

    tails, comps, as_of_s, basis = fleet_inputs()
    as_of = _parse(as_of_s)
    by_tail = {t.tail: t for t in tails}
    rows: list[dict[str, Any]] = []
    for c in comps:
        t = by_tail.get(c.tail)
        if t is None or (tail is not None and c.tail != tail):
            continue
        unit = c.component_id.split("/")[-1]
        window = source.telemetry(c)
        rul_est = c.rul_cycles
        h = c.health
        if rul_est is None:
            try:
                s = health_mod.score_component(c.kind, c.tail, c.component_id, window)
                rul_est, h = s.rul_cycles, s.health
            except Exception:  # noqa: BLE001 - scoring is best effort; the other drivers still stand
                rul_est = None
        parts = [part_state(t, f"{unit}:{k}", k, unit, t.engine_cycles.get(unit, t.cycles), as_of) for k in ENGINE_KEYS]
        worst = min(parts, key=lambda p: min(p["remaining_cycles"], p["remaining_days"] * p["cycles_per_day"]))
        eff = min(worst["remaining_cycles"], worst["remaining_days"] * worst["cycles_per_day"])
        trend = egt_trend(window, h, float(c.cycles), c.component_id)
        rows.append(forecast_engine(tail=c.tail, engine=unit, as_of=as_of, cycles_per_day=t.cycles_per_day, margin_c=trend["margin_c"],
                                    loss_per_cycle=trend["loss_per_cycle"], loss_rel_sd=trend["rel_sd"], margin_basis=trend["basis"],
                                    llp_remaining_cycles=eff, llp_part=worst["position"],
                                    rul=(rul_est.p10, rul_est.p50, rul_est.p90) if rul_est is not None else None))
    rows.sort(key=lambda r: (r["p50_flights"], r["tail"], r["engine"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return {"simulated": True, "provenance": PROVENANCE, "disclaimer": DISCLAIMER, "as_of": as_of_s, "basis": basis, "tail": tail,
            "horizon_months": HORIZON_MONTHS, "count": len(rows),
            "drivers": {d: sum(1 for r in rows if r["driver"] == d) for d in ("egt_margin", "llp", "rul")},
            "engines": rows, "pipeline": pipeline(rows)}
