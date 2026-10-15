"""Per-unit operating context for one flight cycle, derived from the v1 FlightProfile (physics reused, not duplicated)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, fields
from typing import Sequence

import numpy as np

from app.sim.profile import FlightProfile
from app.sim.constants import FLIGHTS_PER_DAY

PHASES = ("taxi_out", "takeoff", "climb", "cruise", "descent", "landing", "taxi_in")
TAXI_SPEED_KMH = 20.0
TURNAROUND_MIN = 35.0
# Single calendar constant: flown legs (cycles) per aircraft per calendar day at world utilisation 1.0. The runner, World and
# reliability pack all use it, so 365 * LEGS_PER_DAY = 2190 cycles == 365 calendar days for every tail.
LEGS_PER_DAY = FLIGHTS_PER_DAY  # app.sim.v2.constants is the single source
BASE_CYCLES_PER_DAY = LEGS_PER_DAY

# station -> (humidity %, dust index 0..1)
STATION_ENV = {
    "ATL": (70.0, 0.15), "DFW": (60.0, 0.35), "JFK": (65.0, 0.10), "ORD": (68.0, 0.10),
    "LAX": (60.0, 0.30), "SFO": (75.0, 0.10), "DEN": (40.0, 0.40), "MIA": (80.0, 0.12),
}
# arrival runway length ft -> station via profile climate is internal to profile.py; wetness comes from the profile.

_ARRAYS = (
    "flight_hours", "taxi_out_min", "takeoff_min", "climb_min", "cruise_min", "descent_min", "landing_min", "taxi_in_min",
    "oat_c", "field_elev_ft", "density_alt_ft", "wet_runway", "payload_frac", "landing_weight_frac", "brake_applications",
    "brake_energy_mj", "sink_rate_fps", "taxi_distance_km", "apu_starts", "apu_hours", "thrust_derate", "humidity_pct",
    "dust_index", "cycle_index", "calendar_days", "flight_cycles", "total_flight_hours", "total_apu_starts", "total_apu_hours",
)


@dataclass(frozen=True)
class OperatingContext:
    """Per-unit arrays (shape (n,)). Per-cycle values plus cumulative per-unit counters (flight cycles/hours, APU, calendar days)."""

    flight_hours: np.ndarray
    taxi_out_min: np.ndarray
    takeoff_min: np.ndarray
    climb_min: np.ndarray
    cruise_min: np.ndarray
    descent_min: np.ndarray
    landing_min: np.ndarray
    taxi_in_min: np.ndarray
    oat_c: np.ndarray
    field_elev_ft: np.ndarray
    density_alt_ft: np.ndarray
    wet_runway: np.ndarray
    payload_frac: np.ndarray
    landing_weight_frac: np.ndarray
    brake_applications: np.ndarray
    brake_energy_mj: np.ndarray
    sink_rate_fps: np.ndarray
    taxi_distance_km: np.ndarray
    apu_starts: np.ndarray
    apu_hours: np.ndarray
    thrust_derate: np.ndarray
    humidity_pct: np.ndarray
    dust_index: np.ndarray
    cycle_index: np.ndarray       # per-cycle: index of this cycle (== flight_cycles after accumulation)
    calendar_days: np.ndarray     # cumulative calendar days
    flight_cycles: np.ndarray     # cumulative counters
    total_flight_hours: np.ndarray
    total_apu_starts: np.ndarray
    total_apu_hours: np.ndarray
    utilisation: np.ndarray

    @property
    def n(self) -> int:
        return int(self.flight_hours.shape[0])

    def take(self, idx: np.ndarray) -> "OperatingContext":
        return OperatingContext(**{f.name: getattr(self, f.name)[idx] for f in fields(self)})


def tail_utilisation(seed: int, tail: str) -> float:
    """Deterministic per-tail utilisation multiplier in [0.7, 1.3]."""
    h = int.from_bytes(hashlib.sha256(f"util|{seed}|{tail}".encode()).digest()[:8], "big")
    return 0.7 + 0.6 * (h / 2**64)


def _derive(p: FlightProfile) -> dict[str, float]:
    ph = dict(p.phases)
    taxi_km = (ph["taxi_out"] + ph["taxi_in"]) * TAXI_SPEED_KMH / 60.0
    short_rwy = max(0.0, (8000.0 - p.runway_len_ft) / 8000.0)
    brake_apps = 1.0 + taxi_km * 1.5 + (4.0 if p.wet_runway else 0.0) * 0.5 + 3.0 * short_rwy + 2.0 * p.landing_weight_frac
    apu_h = (0.5 * ph["taxi_in"] + TURNAROUND_MIN + 0.5 * ph["taxi_out"]) / 60.0
    return {"taxi_km": taxi_km, "brake_apps": float(round(brake_apps)) + (1.0 if p.wet_runway else 0.0), "apu_h": apu_h,
            "flight_h": sum(ph.values()) / 60.0}


def build_context(
    profiles: Sequence[FlightProfile],
    tails: Sequence[str],
    seed: int,
    arr_stations: Sequence[str],
    utilisation: float = 1.0,
    prev: OperatingContext | None = None,
) -> OperatingContext:
    """Build one cycle's context for len(tails) units (profiles[i] is unit i's flight).

    `arr_stations[i]` selects humidity/dust. Pass the previous context as `prev` to accumulate cumulative counters.
    """
    n = len(tails)
    if not (len(profiles) == n == len(arr_stations)):
        raise ValueError("profiles, tails and arr_stations must have equal length")
    util = np.array([tail_utilisation(seed, t) for t in tails]) * float(utilisation)
    d = [_derive(p) for p in profiles]

    def arr(f, dtype=float):
        return np.array([f(p) for p in profiles], dtype=dtype)

    def ph(name):
        return arr(lambda p: dict(p.phases)[name])

    flight_h = np.array([x["flight_h"] for x in d])
    apu_h = np.array([x["apu_h"] for x in d])
    apu_starts = np.array([1 + (1 if x["apu_h"] > 0.75 else 0) for x in d], dtype=np.int64)
    zeros = np.zeros(n)
    pc = prev if prev is not None and prev.n == n else None
    cyc = (pc.flight_cycles if pc is not None else np.zeros(n, dtype=np.int64)) + 1
    env = [STATION_ENV[s] for s in arr_stations]
    # fleet cycles advance in lockstep, so calendar time is uniform: per-tail utilisation scatter does not stretch the clock
    day_inc = np.full(n, 1.0 / (BASE_CYCLES_PER_DAY * float(utilisation)))
    return OperatingContext(
        flight_hours=flight_h,
        taxi_out_min=ph("taxi_out"), takeoff_min=ph("takeoff"), climb_min=ph("climb"), cruise_min=ph("cruise"),
        descent_min=ph("descent"), landing_min=ph("landing"), taxi_in_min=ph("taxi_in"),
        oat_c=arr(lambda p: p.oat_arr_c),
        field_elev_ft=arr(lambda p: p.field_elev_arr_ft),
        density_alt_ft=arr(lambda p: p.density_alt_arr_ft),
        wet_runway=arr(lambda p: p.wet_runway, bool),
        payload_frac=arr(lambda p: p.payload_frac),
        landing_weight_frac=arr(lambda p: p.landing_weight_frac),
        brake_applications=np.array([x["brake_apps"] for x in d], dtype=np.int64),
        brake_energy_mj=arr(lambda p: p.brake_energy_mj),
        sink_rate_fps=arr(lambda p: p.sink_rate_fps),
        taxi_distance_km=np.array([x["taxi_km"] for x in d]),
        apu_starts=apu_starts, apu_hours=apu_h,
        thrust_derate=arr(lambda p: p.takeoff_derate),
        humidity_pct=np.array([e[0] for e in env]), dust_index=np.array([e[1] for e in env]),
        cycle_index=cyc.copy(),
        calendar_days=(pc.calendar_days if pc is not None else zeros) + day_inc,
        flight_cycles=cyc,
        total_flight_hours=(pc.total_flight_hours if pc is not None else zeros) + flight_h,
        total_apu_starts=(pc.total_apu_starts if pc is not None else np.zeros(n, dtype=np.int64)) + apu_starts,
        total_apu_hours=(pc.total_apu_hours if pc is not None else zeros) + apu_h,
        utilisation=util,
    )
