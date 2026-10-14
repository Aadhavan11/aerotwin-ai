"""Deterministic flight phases and operating conditions for simulated flights."""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass

from app.contracts.models import Leg
from app.sim.fleet import STATIONS


@dataclass(frozen=True)
class FlightProfile:
    phases: tuple[tuple[str, float], ...]
    oat_dep_c: float
    oat_arr_c: float
    field_elev_dep_ft: float
    field_elev_arr_ft: float
    runway_len_ft: float
    wet_runway: bool
    payload_frac: float
    takeoff_derate: float
    cruise_fl: int
    landing_weight_frac: float
    brake_energy_mj: float
    sink_rate_fps: float
    density_alt_arr_ft: float


_CLIMATE = {
    "ATL": (1026.0, 19.0, 9000.0, 0.15),
    "DFW": (607.0, 22.0, 9000.0, 0.10),
    "JFK": (13.0, 14.0, 8400.0, 0.20),
    "ORD": (680.0, 11.0, 7500.0, 0.20),
    "LAX": (125.0, 19.0, 10000.0, 0.05),
    "SFO": (13.0, 17.0, 9000.0, 0.12),
    "DEN": (5434.0, 13.0, 12000.0, 0.10),
    "MIA": (8.0, 29.0, 8600.0, 0.30),
}


def brake_energy_mj(
    landing_weight_frac: float, runway_len_ft: float, wet: bool
) -> float:
    """Estimate braking energy from weight, arrival runway length and wetness."""
    if runway_len_ft <= 0:
        raise ValueError("Runway length must be positive")
    return 22.0 * landing_weight_frac * (1.35 if wet else 1.0) * (8000.0 / runway_len_ft)


def _rng(key: str) -> random.Random:
    return random.Random(int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big"))


def _distance_km(dep: str, arr: str) -> float:
    lat1, lon1 = map(math.radians, STATIONS[dep])
    lat2, lon2 = map(math.radians, STATIONS[arr])
    haversine = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(min(1.0, haversine)))


def _build(rng: random.Random, dep: str, arr: str, duration_min: float) -> FlightProfile:
    km = _distance_km(dep, arr)
    taxi_out = 11.0 + rng.uniform(-2, 2)
    takeoff = 1.0 + rng.uniform(0, 0.5)
    climb = min(20.0, 6.0 + km / 200) + rng.uniform(-1, 1)
    descent = min(25.0, 8.0 + km / 180) + rng.uniform(-1, 1)
    landing = 1.0
    taxi_in = 7.0 + rng.uniform(-1.5, 1.5)
    fixed = taxi_out + takeoff + landing + taxi_in
    cruise = duration_min - fixed - climb - descent
    if cruise < 5.0:
        scale = (duration_min - fixed - 5.0) / (climb + descent)
        climb *= scale
        descent *= scale
        cruise = 5.0

    names = ("taxi_out", "takeoff", "climb", "cruise", "descent", "landing", "taxi_in")
    minutes = [round(value, 2) for value in (
        taxi_out, takeoff, climb, cruise, descent, landing, taxi_in
    )]
    # Absorb rounding error into the largest phase, preserving a five-minute cruise.
    largest = max(range(len(minutes)), key=minutes.__getitem__)
    minutes[largest] = round(minutes[largest] + duration_min - sum(minutes), 2)

    elev_dep, oat_dep, _, _ = _CLIMATE[dep]
    elev_arr, oat_arr, runway, wet_probability = _CLIMATE[arr]
    oat_dep = round(oat_dep + rng.uniform(-2, 2), 3)
    oat_arr = round(oat_arr + rng.uniform(-2, 2), 3)
    wet = rng.random() < wet_probability
    payload = rng.uniform(0.55, 0.95)
    derate = rng.uniform(0.0, 0.15)
    cruise_fl = rng.choice(range(240, 391, 10))
    weight = round(min(0.98, max(0.60, 0.60 + 0.35 * payload + rng.uniform(-0.02, 0.02))), 3)
    energy = brake_energy_mj(weight, runway, wet)
    sink_rate = rng.uniform(2.0, 4.0)
    isa_c = 15.0 - 1.98 * elev_arr / 1000.0
    density_alt = elev_arr + 120 * (oat_arr - isa_c)
    return FlightProfile(
        phases=tuple(zip(names, minutes)),
        oat_dep_c=oat_dep,
        oat_arr_c=oat_arr,
        field_elev_dep_ft=elev_dep,
        field_elev_arr_ft=elev_arr,
        runway_len_ft=runway,
        wet_runway=wet,
        payload_frac=round(payload, 3),
        takeoff_derate=round(derate, 3),
        cruise_fl=cruise_fl,
        landing_weight_frac=weight,
        brake_energy_mj=round(energy, 3),
        sink_rate_fps=round(sink_rate, 3),
        density_alt_arr_ft=round(density_alt, 3),
    )


def profile_for(seed: int, leg: Leg) -> FlightProfile:
    """Build repeatable operating conditions keyed by a seed and scheduled leg."""
    if leg.dep not in STATIONS or leg.arr not in STATIONS or leg.dep == leg.arr:
        raise ValueError("Departure and arrival must be distinct known stations")
    duration_min = (leg.sched_arr - leg.sched_dep).total_seconds() / 60.0
    if duration_min < 30.0:
        raise ValueError("Leg duration must be at least 30 minutes")
    key = (
        f"profile|{seed}|{leg.id}|{leg.tail}|{leg.dep}|{leg.arr}|"
        f"{leg.sched_dep.isoformat()}|{leg.sched_arr.isoformat()}"
    )
    return _build(_rng(key), leg.dep, leg.arr, duration_min)


def profile_for_cycle(seed: int, component_id: str, cycle: int) -> FlightProfile:
    """Build a repeatable two-hour synthetic flight for a component cycle."""
    rng = _rng(f"cycle|{seed}|{component_id}|{cycle}")
    codes = sorted(STATIONS)
    dep = rng.choice(codes)
    arr = rng.choice([code for code in codes if code != dep])
    return _build(rng, dep, arr, 120.0)
