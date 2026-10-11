"""Deterministic stand-in "golden" FleetState for the scheduler and what-if code.

STAND-IN until Shamit's ``sim/fleet.py`` and Aashirth's ``twin.snapshot()`` exist. It follows the golden constraints in
FEATURES-3.1-3.6.md / work/01: 24 fictional aircraft (SIM-001..SIM-024), 8 stations, 4 legs a day for 5 days, SIM-017 with an
abnormal engine_2 (RUL p10 = 18 cycles) and a long ground window at DFW, ATL with a hangar but NO borescope kit on hand, DFW with 2 kits.

Everything here is SIMULATED. Tail numbers are fictional. Same seed -> same state.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from typing import Optional

from app.contracts.models import (
    Aircraft, ComponentState, DataQuality, FleetState, Leg, PartStock, RulEstimate, ScenarioEvent, Station, Technician,
)
from app.solver.common import SEED, UTC

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)          # SimClock start (CONTRACTS / work/01)
GOLDEN_TAIL = "SIM-017"
GOLDEN_COMPONENT = "SIM-017/engine_2"

STATION_ORDER = ["ATL", "DFW", "ORD", "DEN", "LAX", "JFK", "MIA", "SEA"]
COORDS = {  # (lat, lon): public airport coordinates, rounded
    "ATL": (33.64, -84.43), "DFW": (32.90, -97.04), "ORD": (41.98, -87.90), "DEN": (39.86, -104.67),
    "LAX": (33.94, -118.41), "JFK": (40.64, -73.78), "MIA": (25.80, -80.29), "SEA": (47.45, -122.31),
}
HUBS = ["ATL", "DFW", "ORD"]
COMPONENT_KINDS = [("engine_1", "engine"), ("engine_2", "engine"), ("brakes", "brakes"),
                   ("landing_gear", "landing_gear"), ("hydraulics", "hydraulics"), ("avionics", "avionics")]


def _km(a: str, b: str) -> float:
    (la1, lo1), (la2, lo2) = map(lambda s: tuple(map(math.radians, COORDS[s])), (a, b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def _block(a: str, b: str) -> timedelta:
    minutes = (_km(a, b) / 800.0 + 0.5) * 60.0
    return timedelta(minutes=5 * round(minutes / 5))


def _pick(rng: random.Random, here: str) -> str:
    pool = [s for s in STATION_ORDER if s != here]
    weights = [3.0 if s in HUBS else 1.0 for s in pool]
    return rng.choices(pool, weights=weights, k=1)[0]


def _rotation(rng: random.Random, tail: str, start: str, first_dep: datetime, n_legs: int = 20,
              forced: Optional[list[str]] = None, dep_override: Optional[dict[int, datetime]] = None) -> list[Leg]:
    """4 legs a day: short turnarounds, then an 8 to 10.5 h overnight (at the home station about half the time).
    Arrives where the next leg departs."""
    legs: list[Leg] = []
    home = here = start
    t = first_dep
    for k in range(n_legs):
        if dep_override and k in dep_override:
            t = dep_override[k]
        if forced and k < len(forced):
            dest = forced[k]
        elif k % 4 == 3 and here != home and rng.random() < 0.5:
            dest = home                      # end the day at the home base
        else:
            dest = _pick(rng, here)
        arr = t + _block(here, dest)
        legs.append(Leg(id=f"L-{tail[4:]}-{k + 1:02d}", tail=tail, dep=here, arr=dest, sched_dep=t, sched_arr=arr))
        here = dest
        gap_h = rng.uniform(8.0, 10.5) if k % 4 == 3 else rng.uniform(1.5, 2.5)
        t = arr + timedelta(minutes=5 * round(gap_h * 12))
    return legs


def _stations(now: datetime) -> list[Station]:
    far = now + timedelta(days=6)

    def tech(iata: str, n: int, skills: list[str], end: Optional[datetime] = None) -> Technician:
        return Technician(id=f"{iata}-T{n}", skills=skills, shift_start=now - timedelta(hours=6), shift_end=end or far)

    techs = {
        "ATL": [tech("ATL", 1, ["engine", "gear"], now + timedelta(days=4)), tech("ATL", 2, ["hydraulics", "avionics"])],
        "DFW": [tech("DFW", 1, ["engine"]), tech("DFW", 2, ["engine", "avionics"]), tech("DFW", 3, ["gear", "hydraulics"])],
        "ORD": [tech("ORD", 1, ["engine", "gear"]), tech("ORD", 2, ["hydraulics", "avionics"])],
        "DEN": [tech("DEN", 1, ["engine", "gear", "hydraulics"]), tech("DEN", 2, ["avionics"])],
        "LAX": [tech("LAX", 1, ["engine", "gear"]), tech("LAX", 2, ["hydraulics", "avionics"])],
        "JFK": [tech("JFK", 1, ["engine", "gear"]), tech("JFK", 2, ["hydraulics", "avionics"])],
        "MIA": [tech("MIA", 1, ["engine", "gear"]), tech("MIA", 2, ["hydraulics", "avionics"])],
        "SEA": [tech("SEA", 1, ["engine", "gear", "hydraulics", "avionics"])],
    }
    slots = {"ATL": 2, "DFW": 2, "ORD": 2, "DEN": 1, "LAX": 2, "JFK": 1, "MIA": 1, "SEA": 1}
    kits = {"DFW": 2, "ORD": 1}                       # BORESCOPE-KIT on hand; ATL has none (golden constraint)
    pumps = {"ATL": 1, "DFW": 1, "ORD": 1}
    brakes = {s: 1 for s in STATION_ORDER}
    lru = {s: (2 if s == "LAX" else 1) for s in STATION_ORDER}

    out: list[Station] = []
    for iata in STATION_ORDER:
        parts = [
            PartStock(part="BORESCOPE-KIT", qty=kits.get(iata, 0), eta_hours=0.0,
                      ship_from=None if iata == "DFW" else "DFW", ship_hours=18.0 if iata == "ATL" else 24.0, ship_cost_usd=800.0),
            PartStock(part="BRAKE-ASSY", qty=brakes[iata], ship_from=None if iata == "ORD" else "ORD", ship_hours=12.0, ship_cost_usd=400.0),
            PartStock(part="HYD-PUMP", qty=pumps.get(iata, 0), ship_from=None if iata == "ATL" else "ATL", ship_hours=12.0, ship_cost_usd=600.0),
            PartStock(part="AVIONICS-LRU", qty=lru[iata], ship_from=None if iata == "DFW" else "DFW", ship_hours=12.0, ship_cost_usd=500.0),
        ]
        lat, lon = COORDS[iata]
        out.append(Station(iata=iata, hangar_slots=slots[iata], technicians=techs[iata], parts=parts, lat=lat, lon=lon))
    return out


# (tail, component) -> (p10, p50, p90, health, anomaly_score, status)
SPECIAL_RUL = {
    ("SIM-017", "engine_2"): (18, 22, 25, 0.18, 0.91, "alert"),     # the golden anomaly (the doc's 18-25 cycles)
    ("SIM-012", "engine_1"): (47, 55, 63, 0.46, 0.30, "watch"),     # competes with SIM-017 for the kit and the engine technician
    ("SIM-005", "hydraulics"): (35, 42, 50, 0.40, 0.22, "watch"),
    ("SIM-021", "avionics"): (52, 58, 66, 0.52, 0.18, "watch"),
}
SCHEDULED_DUE = {("SIM-008", "brakes"): 3}   # tire/wheel change (40-day synthetic interval) due in 3 days


def _components(rng: random.Random, tail: str, now: datetime, wheel_interval_days: float) -> list[ComponentState]:
    comps: list[ComponentState] = []
    for name, kind in COMPONENT_KINDS:
        cycles = rng.randint(120, 2400)
        sp = SPECIAL_RUL.get((tail, name))
        if sp:
            p10, p50, p90, health, anomaly, status = sp
            cycles = 231 if (tail, name) == ("SIM-017", "engine_2") else cycles
        else:
            p50 = rng.uniform(150, 450)
            p10, p90 = round(p50 * 0.8, 1), round(p50 * 1.15, 1)
            p50 = round(p50, 1)
            health, anomaly, status = round(rng.uniform(0.72, 0.95), 2), round(rng.uniform(0.04, 0.2), 2), "ok"
        days_ago = rng.uniform(1.0, 9.0)           # recent service: no routine check falls inside the 5-day horizon by accident
        if (tail, name) in SCHEDULED_DUE:
            days_ago = wheel_interval_days - SCHEDULED_DUE[(tail, name)]
        comps.append(ComponentState(
            tail=tail, component_id=f"{tail}/{name}", kind=kind, cycles=cycles, hours=round(cycles * 1.77, 1),
            health=health, anomaly_score=anomaly, rul_cycles=RulEstimate(p10=p10, p50=p50, p90=p90),
            data_quality=DataQuality(status="ok"), last_maintenance=now - timedelta(days=days_ago),
            status=status, updated=now,
        ))
    return comps


def golden_state(seed: Optional[int] = None, now: datetime = NOW) -> FleetState:
    seed = SEED if seed is None else seed
    aircraft: list[Aircraft] = []
    components: list[ComponentState] = []
    legs: list[Leg] = []
    from app.solver.cards import load_cards  # local import: golden depends on the cards' wheel interval
    wheel = next(c.interval_days for c in load_cards() if c.id == "TC-WHEEL")

    for i in range(1, 25):
        tail = f"SIM-{i:03d}"
        rng = random.Random(seed * 1000 + i)
        home = STATION_ORDER[(i - 1) % 8]
        first_dep = now + timedelta(minutes=30 + (i * 37) % 240)
        forced = dep_override = None
        if tail == GOLDEN_TAIL:
            # ATL -> DFW with a long stop at DFW (14.5 h, the golden window), then ORD and back; later it overnights
            # at DFW again, at its home base ATL (day 2) and so on. Deadline (18th flight) falls on day 5.
            forced = ["DFW", "ORD", "ATL", "DFW", "ORD", "DFW", "ORD", "ATL",
                      "DFW", "ORD", "ATL", "DFW", "ATL", "ORD", "ATL", "JFK"]
            first_dep = now + timedelta(minutes=30)
            dep_override = {1: datetime(2026, 10, 13, 5, 0, tzinfo=UTC)}
        legs.extend(_rotation(rng, tail, home, first_dep, forced=forced, dep_override=dep_override))
        comps = _components(rng, tail, now, wheel)
        components.extend(comps)
        worst = min(("alert", "watch", "ok"), key=lambda s: ["alert", "watch", "ok"].index(s) if any(c.status == s for c in comps) else 9)
        aircraft.append(Aircraft(
            tail=tail, type="B738-class" if i % 3 == 0 else "A320-class", home=home, location=home, status=worst,
            cycles=8123 if tail == GOLDEN_TAIL else rng.randint(3000, 22000),
            hours=14210.5 if tail == GOLDEN_TAIL else round(rng.uniform(5000, 38000), 1),
        ))
    return FleetState(now=now, aircraft=aircraft, components=components, legs=legs, stations=_stations(now))


def golden_part_delay(hours: float = 12.0, at: Optional[datetime] = None) -> ScenarioEvent:
    """The golden disruption: the DFW borescope kit is delayed (CONTRACTS.md section 4 example)."""
    return ScenarioEvent(id="E-2", type="part_delay", at=at or (NOW + timedelta(hours=2)), station="DFW",
                         part="BORESCOPE-KIT", delay_hours=hours, note="golden disruption: DFW kit delayed")
