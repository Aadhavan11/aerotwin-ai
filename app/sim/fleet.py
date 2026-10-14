"""Seeded fictional operations data, with guaranteed golden constraints.

Everything here is SIMULATED. The operator is fictional and the numbers are modelled on a mid-size
US narrow-body airline so that the demo does not look like a spreadsheet of identical rows:

* stations differ in size: bays, crew per 12 h shift and spare-part depth follow the station's role
  (three main bases, a few outstations);
* technicians work 12 h day and night shifts with fewer people on weekends and a few absences;
* spares differ by part and station; stations without a part ship it from the nearest base, and the
  lead time and courier cost follow the distance;
* airframes have different ages; component counters are "since last restoration" and agree with the
  simulated wear level of that component.

Golden facts the story relies on (see ``tests/test_simulator.py``): SIM-017 sits at DFW for ~10 h
every night, DFW holds 2 BORESCOPE-KIT and an engine technician on the night shift, ATL has none
and gets one from DFW in 18 h.
"""
import math
import random
from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from app.contracts.models import Aircraft, ComponentState, DataQuality, FleetState, Leg, PartStock, Station, Technician
from app.sim.clock import START
from app.sim.telemetry import wear_profile

STATIONS = {
    'ATL': (33.64, -84.43), 'DFW': (32.90, -97.04), 'JFK': (40.64, -73.78), 'ORD': (41.98, -87.90),
    'LAX': (33.94, -118.41), 'SFO': (37.62, -122.38), 'DEN': (39.86, -104.67), 'MIA': (25.80, -80.29),
}
KINDS = {'engine_1': 'engine', 'engine_2': 'engine', 'brakes': 'brakes', 'landing_gear': 'landing_gear',
         'hydraulics': 'hydraulics', 'avionics': 'avionics', 'apu': 'apu', 'tires': 'tires', 'ecs': 'ecs', 'electrical': 'electrical'}
HOURS_PER_CYCLE = 2.0          # the schedule flies 2 h legs; the twin uses the same figure for component hours

# tz: local offset from UTC in October; bays: simultaneous maintenance jobs; crew: technicians per 12 h shift
# by skill; spares: line-replaceable stock depth; kits: borescope kits on hand; base: carries the deep stock.
PROFILES = {
    'DFW': dict(tz=-5, bays=3, crew=dict(engine=3, gear=2, hydraulics=2, avionics=2), spares=3, kits=2, base=True),
    'ATL': dict(tz=-4, bays=3, crew=dict(engine=3, gear=2, hydraulics=2, avionics=2), spares=3, kits=0, base=True),
    'ORD': dict(tz=-5, bays=2, crew=dict(engine=2, gear=2, hydraulics=1, avionics=2), spares=3, kits=1, base=True),
    'JFK': dict(tz=-4, bays=2, crew=dict(engine=2, gear=1, hydraulics=1, avionics=2), spares=2, kits=1, base=False),
    'LAX': dict(tz=-7, bays=2, crew=dict(engine=2, gear=1, hydraulics=1, avionics=1), spares=2, kits=1, base=False),
    'DEN': dict(tz=-6, bays=1, crew=dict(engine=1, gear=1, hydraulics=1, avionics=1), spares=1, kits=0, base=False),
    'SFO': dict(tz=-7, bays=1, crew=dict(engine=1, gear=1, hydraulics=1, avionics=1), spares=1, kits=0, base=False),
    'MIA': dict(tz=-4, bays=1, crew=dict(engine=1, gear=1, hydraulics=1, avionics=1), spares=1, kits=0, base=False),
}
SKILLS = ('engine', 'gear', 'hydraulics', 'avionics')
PARTS = ('BORESCOPE-KIT', 'BRAKE-ASSY', 'HYD-PUMP', 'AVIONICS-LRU', 'APU-STARTER', 'TIRE-ASSY', 'ECS-PACK-VALVE', 'BATTERY-PACK')
NEW_KINDS = ('apu', 'tires', 'ecs', 'electrical')
COURIER_WEIGHT = {'BORESCOPE-KIT': 0.5, 'BRAKE-ASSY': 1.6, 'HYD-PUMP': 1.1, 'AVIONICS-LRU': 0.6, 'APU-STARTER': 0.8, 'TIRE-ASSY': 1.2, 'ECS-PACK-VALVE': 0.7, 'BATTERY-PACK': 0.9}
WEEKEND_FACTOR = 0.65          # share of the weekday crew on duty on Saturday and Sunday
SERVICE_DAYS = {'engine': 45, 'brakes': 40, 'hydraulics': 30, 'avionics': 60, 'landing_gear': 120, 'apu': 90, 'tires': 40, 'ecs': 60, 'electrical': 90}   # routine-check spacing per kind


def _km(a, b):
    (la1, lo1), (la2, lo2) = STATIONS[a], STATIONS[b]
    p1, p2 = math.radians(la1), math.radians(la2)
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def _shifts(tz):
    """12 h day (07:00-19:00 local) and night (19:00-07:00) shifts that have not ended at the sim start."""
    midnight = START.replace(hour=0, minute=0, second=0, microsecond=0)
    shifts = []
    for day in range(-1, 7):
        start = midnight + timedelta(days=day, hours=7 - tz)
        for index, name in enumerate(('day', 'night')):
            begin = start + timedelta(hours=12 * index)
            end = begin + timedelta(hours=12)
            if end > START:
                shifts.append((name, begin, end))
    return shifts


def _roster(code, profile, rng):
    """One ``Technician`` per person per shift, numbered per skill in time order (``DFW-engine-0``...)."""
    people = []
    counters = {skill: 0 for skill in SKILLS}
    for name, begin, end in _shifts(profile['tz']):
        local = begin + timedelta(hours=profile['tz'])
        weekend = local.weekday() >= 5
        for skill in SKILLS:
            crew = profile['crew'][skill]
            # Night shifts run one short of the day crew at the larger stations.
            if name == 'night' and crew >= 3:
                crew -= 1
            if weekend:
                crew = max(1, round(crew * WEEKEND_FACTOR))
            for _ in range(crew):
                absent = rng.random() < 0.06 and not (code == 'DFW' and skill == 'engine')
                people.append(Technician(id=f'{code}-{skill}-{counters[skill]}', skills=[skill], shift_start=begin, shift_end=end, available=not absent))
                counters[skill] += 1
    return people


def _stock(code, profile, rng, parts=PARTS[:4]):
    stock = {}
    for part in parts:
        if part == 'BORESCOPE-KIT':
            qty = profile['kits']
        elif part == 'BRAKE-ASSY':
            qty = profile['spares'] + (1 if profile['base'] else 0) + rng.choice((-1, 0, 0, 1))
        elif part == 'HYD-PUMP':
            qty = max(profile['spares'] - 1, 0) + rng.choice((-1, 0, 0, 1))
        else:
            qty = profile['spares'] + rng.choice((-1, 0, 0, 1))
        qty = max(qty, 1 if profile['base'] and part != 'BORESCOPE-KIT' else 0)
        stock[part] = qty
    return stock


@lru_cache(maxsize=16)
def ops_v2_data(seed: int) -> tuple[dict[str, dict[str, int]], dict[str, tuple[float, float, float]]]:
    """Separate new stock, wear, life and service draws from the original operations stream."""
    from app.ml.sensors import GENERIC
    ops = random.Random(f'{seed}:ops:v2')
    quantity = {code: _stock(code, PROFILES[code], ops, PARTS[4:]) for code in STATIONS}
    profiles = {}
    for index in range(1, 25):
        for kind in NEW_KINDS:
            profiles[f'SIM-{index:03d}/{kind}'] = (
                ops.uniform(.05, .35), GENERIC[kind].life_cycles * ops.uniform(.8, 1.25),
                ops.uniform(1.5, .97 * SERVICE_DAYS[kind]),
            )
    return quantity, profiles


def generate_fleet(seed=1234):
    rng = random.Random(seed)
    ops = random.Random(f'{seed}:ops')
    quantity = {code: _stock(code, PROFILES[code], ops) for code in STATIONS}
    new_quantity, new_profiles = ops_v2_data(seed)
    for code in STATIONS:
        quantity[code].update(new_quantity[code])
    stations = []
    for code, (lat, lon) in STATIONS.items():
        profile = PROFILES[code]
        techs = _roster(code, profile, ops)
        parts = []
        for part in PARTS:
            qty = quantity[code][part]
            sources = sorted((_km(code, other), other) for other in STATIONS if other != code and quantity[other][part] > 0 and PROFILES[other]['base' if part != 'BORESCOPE-KIT' else 'kits'])
            source = sources[0] if sources else None
            km = source[0] if source else 0
            if code == 'ATL' and part == 'BORESCOPE-KIT':
                source = (_km('ATL', 'DFW'), 'DFW')      # golden fact: no kit at ATL, one arrives from DFW in 18 h
                km = source[0]
            parts.append(PartStock(
                part=part, qty=qty,
                eta_hours=18 if code == 'ATL' and part == 'BORESCOPE-KIT' else 0,
                ship_from=source[1] if source else None,
                ship_hours=round(max(4.0, 3.0 + km / 700), 1) if source else 12.0,
                ship_cost_usd=round((220 + 0.42 * km) * COURIER_WEIGHT[part], -1) if source else 800.0,
            ))
        stations.append(Station(iata=code, hangar_slots=profile['bays'], technicians=techs, parts=parts, lat=lat, lon=lon))
    aircraft, components, legs = [], [], []
    codes = list(STATIONS)
    from app.ml.sensors import GENERIC
    for index in range(1, 25):
        tail = f'SIM-{index:03d}'
        home = 'DFW' if index == 17 else codes[(index - 1) % 8]
        rng.randint(500, 2500)                        # keeps the leg sequence below identical to earlier versions
        age_years = ops.uniform(3.0, 19.0)
        cycles = round(age_years * ops.uniform(1250, 1650))
        hours = cycles * HOURS_PER_CYCLE
        aircraft.append(Aircraft(tail=tail, type='A320-class' if index <= 16 else 'B738-class', home=home, location=home, status='ok', cycles=cycles, hours=hours))
        for name, kind in KINDS.items():
            component_id = f'{tail}/{name}'
            if kind == 'engine':
                since = round(ops.uniform(.15, 1.0) * min(cycles, 8200))
                health = .95
            else:
                start, life = wear_profile(seed, component_id, GENERIC[kind])
                since = round(start * life)
                health = round(max(1 - start, .05), 3)
            days = new_profiles[component_id][2] if kind in NEW_KINDS else ops.uniform(1.5, .97 * SERVICE_DAYS[kind])
            serviced = START - timedelta(days=days)
            components.append(ComponentState(
                tail=tail, component_id=component_id, kind=kind, cycles=since, hours=since * HOURS_PER_CYCLE,
                health=health, anomaly_score=.02, rul_cycles=None,
                data_quality=DataQuality(status='degraded', reasons=['SIMULATED initial state; not an ML estimate']),
                last_maintenance=serviced, status='ok', updated=START,
            ))
        current = home
        for day in range(5):
            for flight in range(4):
                destination = home if flight == 3 else rng.choice([code for code in codes if code != current and (flight != 2 or code != home)])
                departure = START + timedelta(days=day, hours=flight * 4)
                legs.append(Leg(id=f'L-{tail}-{day*4+flight+1:03d}', tail=tail, dep=current, arr=destination, sched_dep=departure, sched_arr=departure + timedelta(hours=2)))
                current = destination
    return FleetState(now=START, aircraft=aircraft, components=components, legs=legs, stations=stations)

def dump_fixtures(state, folder='fixtures'):
    import json
    from app.contracts.models import AircraftSummary, FleetKpis, FleetOverview
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    overview = FleetOverview(
        stations=state.stations,
        aircraft=[AircraftSummary(**a.model_dump()) for a in state.aircraft],
        kpis=FleetKpis(aircraft=24, needing_attention=0, predicted_maintenance_72h=0, in_maintenance=0, readiness_pct=100),
    )
    for name, value in [('fleet',overview), ('stations',state.stations), ('legs_golden',[leg for leg in state.legs if leg.tail=='SIM-017'])]:
        data = value.model_dump(mode='json') if hasattr(value,'model_dump') else [item.model_dump(mode='json') for item in value]
        (folder/f'{name}.json').write_text(json.dumps(data,indent=2)+'\n')
