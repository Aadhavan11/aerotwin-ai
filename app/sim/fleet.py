"""Seeded fictional operations data, with guaranteed golden constraints."""
import random
from datetime import timedelta
from pathlib import Path
from app.contracts.models import Aircraft, ComponentState, DataQuality, FleetState, Leg, PartStock, RulEstimate, Station, Technician
from app.sim.clock import START

STATIONS = {'ATL': (33.64, -84.43), 'DFW': (32.90, -97.04), 'JFK': (40.64, -73.78), 'ORD': (41.98, -87.90), 'LAX': (33.94, -118.41), 'SFO': (37.62, -122.38), 'DEN': (39.86, -104.67), 'MIA': (25.80, -80.29)}
KINDS = {'engine_1': 'engine', 'engine_2': 'engine', 'brakes': 'brakes', 'landing_gear': 'landing_gear', 'hydraulics': 'hydraulics', 'avionics': 'avionics'}

def generate_fleet(seed=1234):
    rng = random.Random(seed)
    stations = []
    for code, (lat, lon) in STATIONS.items():
        techs = [Technician(id=f'{code}-{skill}-{day}', skills=[skill], shift_start=START+timedelta(days=day), shift_end=START+timedelta(days=day+1)) for day in range(5) for skill in ('engine', 'gear', 'hydraulics', 'avionics')]
        parts = [PartStock(part=part, qty=0 if code=='ATL' and part=='BORESCOPE-KIT' else 2, eta_hours=18 if code=='ATL' and part=='BORESCOPE-KIT' else 0, ship_from='DFW' if code=='ATL' else None) for part in ('BORESCOPE-KIT', 'BRAKE-ASSY', 'HYD-PUMP', 'AVIONICS-LRU')]
        stations.append(Station(iata=code, hangar_slots=2, technicians=techs, parts=parts, lat=lat, lon=lon))
    aircraft, components, legs = [], [], []
    codes = list(STATIONS)
    for index in range(1,25):
        tail = f'SIM-{index:03d}'
        home = 'DFW' if index==17 else codes[(index-1)%8]
        cycles = rng.randint(500,2500)
        hours = cycles * 2.0
        aircraft.append(Aircraft(tail=tail, type='A320-class' if index<=16 else 'B738-class', home=home, location=home, status='ok', cycles=cycles, hours=hours))
        for name, kind in KINDS.items():
            components.append(ComponentState(tail=tail, component_id=f'{tail}/{name}', kind=kind, cycles=cycles, hours=hours, health=.95, anomaly_score=.02, rul_cycles=RulEstimate(p10=120,p50=150,p90=180), data_quality=DataQuality(status='degraded', reasons=['SIMULATED initial state; not an ML estimate']), status='ok', updated=START))
        current = home
        for day in range(5):
            for flight in range(4):
                destination = home if flight==3 else rng.choice([code for code in codes if code!=current and (flight!=2 or code!=home)])
                departure = START+timedelta(days=day,hours=flight*4)
                legs.append(Leg(id=f'L-{tail}-{day*4+flight+1:03d}', tail=tail, dep=current, arr=destination, sched_dep=departure, sched_arr=departure+timedelta(hours=2)))
                current = destination
    return FleetState(now=START, aircraft=aircraft, components=components, legs=legs, stations=stations)

def dump_fixtures(state, folder='fixtures'):
    import json
    from app.contracts.models import AircraftSummary, FleetKpis, FleetOverview
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    overview = FleetOverview(stations=state.stations, aircraft=[AircraftSummary(**a.model_dump()) for a in state.aircraft], kpis=FleetKpis(aircraft=24,needing_attention=0,predicted_maintenance_72h=0,in_maintenance=0,readiness_pct=100))
    for name, value in [('fleet',overview), ('stations',state.stations), ('legs_golden',[leg for leg in state.legs if leg.tail=='SIM-017'])]:
        data = value.model_dump(mode='json') if hasattr(value,'model_dump') else [item.model_dump(mode='json') for item in value]
        (folder/f'{name}.json').write_text(json.dumps(data,indent=2)+'\n')
