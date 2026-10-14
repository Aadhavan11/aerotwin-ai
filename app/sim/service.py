"""Simulator service with SQLite persistence and an optional twin ingestion hook.

Only simulation-owned tables are initialized here. The serialized snapshot and
telemetry source checkpoint live in kv; shared tables follow CONTRACTS.md.
"""
import json
import math
import sqlite3
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import RLock
from app.contracts.models import FleetState, ScenarioEvent, TwinEvent
from app.sim.clock import SimClock
from app.sim.fleet import generate_fleet
from app.sim.telemetry import TelemetrySource

SOURCE_ATTRS = ('positions', 'origins', 'units', 'wear', 'faults', 'damage', 'modes', 'sensor_faults')

class Simulator:
    def __init__(self, db_path='data/aerotwin.db', seed=1234, ingest=None, trajectories=None, stochastic=False):
        if db_path != ':memory:':
            Path(db_path).parent.mkdir(parents=True,exist_ok=True)
        self.connection = sqlite3.connect(db_path, check_same_thread=False)
        self.lock = RLock()
        self.seed, self.ingest = seed, ingest
        self.source = TelemetrySource(seed,trajectories,stochastic)
        self.connection.executescript('''
        CREATE TABLE IF NOT EXISTS aircraft (tail TEXT PRIMARY KEY,type TEXT,home TEXT,location TEXT,status TEXT,cycles INTEGER,hours REAL,json TEXT);
        CREATE TABLE IF NOT EXISTS components (tail TEXT,component_id TEXT,kind TEXT,state_json TEXT,PRIMARY KEY(tail,component_id));
        CREATE TABLE IF NOT EXISTS legs (id TEXT PRIMARY KEY,tail TEXT,dep TEXT,arr TEXT,sched_dep TEXT,sched_arr TEXT);
        CREATE TABLE IF NOT EXISTS stations (iata TEXT PRIMARY KEY,json TEXT);
        CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY,type TEXT,at TEXT,json TEXT);
        CREATE TABLE IF NOT EXISTS audit (id TEXT PRIMARY KEY,ts TEXT,actor TEXT,action TEXT,ref TEXT,detail TEXT);
        CREATE TABLE IF NOT EXISTS telemetry (id TEXT PRIMARY KEY,tail TEXT,component_id TEXT,cycle INTEGER,ts TEXT,values_json TEXT);
        CREATE INDEX IF NOT EXISTS telemetry_component ON telemetry(tail,component_id,cycle);
        ''')
        self.clock = SimClock(self.connection)
        saved = self.connection.execute("SELECT value FROM kv WHERE key='sim_snapshot'").fetchone()
        if saved:
            self.state = FleetState.model_validate_json(saved[0])
            checkpoint = json.loads(self.connection.execute("SELECT value FROM kv WHERE key='sim_source'").fetchone()[0])
            for attr in SOURCE_ATTRS:
                setattr(self.source,attr,checkpoint.get(attr, {}))
        else:
            self.reset()
        self.source.now = self.state.now

    @contextmanager
    def _atomic(self):
        with self.lock:
            state = self.state.model_copy(deep=True)
            source = deepcopy({attr:getattr(self.source,attr) for attr in (*SOURCE_ATTRS,'now')})
            try:
                yield
            except Exception:
                self.connection.rollback()
                self.state = state
                for attr,value in source.items():
                    setattr(self.source,attr,value)
                raise

    def _save(self):
        db = self.connection
        for aircraft in self.state.aircraft:
            db.execute('INSERT OR REPLACE INTO aircraft VALUES (?,?,?,?,?,?,?,?)',(
                aircraft.tail,aircraft.type,aircraft.home,aircraft.location,aircraft.status,aircraft.cycles,aircraft.hours,aircraft.model_dump_json()))
        for c in self.state.components:
            db.execute('INSERT OR REPLACE INTO components VALUES (?,?,?,?)',(c.tail,c.component_id,c.kind,c.model_dump_json()))
        for leg in self.state.legs:
            db.execute('INSERT OR REPLACE INTO legs VALUES (?,?,?,?,?,?)',(leg.id,leg.tail,leg.dep,leg.arr,leg.sched_dep.isoformat(),leg.sched_arr.isoformat()))
        for station in self.state.stations:
            db.execute('INSERT OR REPLACE INTO stations VALUES (?,?)',(station.iata,station.model_dump_json()))
        db.execute("INSERT OR REPLACE INTO kv VALUES ('sim_snapshot',?)",(self.state.model_dump_json(),))
        db.execute("INSERT OR REPLACE INTO kv VALUES ('sim_source',?)",(json.dumps({attr:getattr(self.source,attr) for attr in SOURCE_ATTRS}),))
        db.execute("INSERT OR REPLACE INTO kv VALUES ('sim_now',?)",(self.state.now.isoformat(),))
        db.commit()

    def reset(self, name='golden'):
        if name!='golden':
            raise ValueError('only the golden scenario is supported')
        with self.lock:
            # Clear derived team state too, when those tables exist.
            tables = {r[0] for r in self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ('aircraft','components','legs','stations','telemetry','events','audit','health_history','tasks','plans','agent_runs','work_orders','kv'):
                if table in tables:
                    self.connection.execute(f'DELETE FROM {table}')
            self.state = generate_fleet(self.seed)
            self.source.now = self.state.now
            for attr in SOURCE_ATTRS:
                getattr(self.source,attr).clear()
            self.source.positions.update({c.component_id:c.cycles for c in self.state.components})
            self.source.origins.update(self.source.positions)
            self.connection.execute("INSERT INTO kv VALUES ('scenario_name',?)",(name,))
            self._save()
            return {'ok':True}

    def _ingest(self, row):
        result = self.ingest(row) if self.ingest else None
        self.connection.execute('DELETE FROM telemetry WHERE tail=? AND component_id=? AND cycle=?',(row.tail,row.component_id,row.cycle))
        self.connection.execute('INSERT INTO telemetry(tail,component_id,cycle,ts,values_json) VALUES (?,?,?,?,?)',(row.tail,row.component_id,row.cycle,row.ts.isoformat(),json.dumps(row.values)))
        for index, component in enumerate(self.state.components):
            if component.component_id==row.component_id:
                if result is not None:
                    self.state.components[index] = result
                else:
                    component.cycles = row.cycle
                    component.updated = row.ts
                break

    def step(self, hours):
        if not math.isfinite(hours) or not 0<=hours<=120:
            raise ValueError('hours must be between 0 and 120')
        from datetime import timedelta
        with self._atomic():
            old = self.state.now
            end = old+timedelta(hours=hours)
            completed = sorted([lg for lg in self.state.legs if old<lg.sched_arr<=end],key=lambda lg:(lg.sched_arr,lg.id))
            ingested = 0
            for leg in completed:
                self.source.now = leg.sched_arr
                components = [c for c in self.state.components if c.tail==leg.tail]
                for row in self.source.advance_leg(leg,[c.component_id for c in components]):
                    self._ingest(row)
                    ingested += 1
                for component in components:
                    component.hours += (leg.sched_arr-leg.sched_dep).total_seconds()/3600
                aircraft = next(a for a in self.state.aircraft if a.tail==leg.tail)
                aircraft.cycles += 1
                aircraft.hours += (leg.sched_arr-leg.sched_dep).total_seconds()/3600
            for aircraft in self.state.aircraft:
                past = [lg for lg in self.state.legs if lg.tail==aircraft.tail and lg.sched_arr<=end]
                aircraft.location = max(past,key=lambda lg:lg.sched_arr).arr if past else aircraft.home
                if any(lg.tail==aircraft.tail and lg.sched_dep<=end<lg.sched_arr for lg in self.state.legs):
                    aircraft.location='AIR'
            self.state.now=end
            self.source.now=end
            self._save()
            return {'sim_now':end,'ingested':ingested,'telemetry_source':self.source.source,'twin_connected':self.ingest is not None}

    def inject(self, event: ScenarioEvent):
        with self._atomic():
            if event.at!=self.state.now:
                raise ValueError('event.at must equal the current simulation time')
            if event.id and self.connection.execute('SELECT 1 FROM events WHERE id=?',(event.id,)).fetchone():
                raise ValueError('event id already exists')
            rows=[]
            changes=[]
            if event.type in ('anomaly','accelerated_degradation'):
                component=next((c for c in self.state.components if c.tail==event.tail and c.component_id==event.component_id),None)
                if component is None:
                    raise ValueError('unknown aircraft/component pair')
                severity=event.severity if event.severity is not None else 1.
                if not math.isfinite(severity) or not 0<=severity<=1:
                    raise ValueError('severity must be between 0 and 1')
                self.source.inject(component.component_id,event.type,severity)
                self.source.now=self.state.now
                rows=self.source.advance(component.tail,component.component_id,8)
                for row in rows:
                    self._ingest(row)
                changes=[TwinEvent(ts=self.state.now,kind='fault_injected',summary=f'SIMULATED {event.type} on {component.component_id}',ref=event.id or None)]
            else:
                station=next((s for s in self.state.stations if s.iata==event.station),None)
                if station is None:
                    raise ValueError('unknown station')
                if event.type=='part_delay':
                    stock=next((p for p in station.parts if p.part==event.part),None)
                    if stock is None or event.delay_hours is None or not math.isfinite(event.delay_hours) or event.delay_hours<0:
                        raise ValueError('valid part and nonnegative delay_hours required')
                    stock.eta_hours+=event.delay_hours
                elif event.type=='facility_closed':
                    station.hangar_slots=0
                elif event.type=='tech_unavailable':
                    tech=next((t for t in station.technicians if t.id==event.technician_id),None)
                    if tech is None:
                        raise ValueError('unknown technician')
                    tech.available=False
            event=event.model_copy(update={'id':event.id or f'E-{self.connection.execute("SELECT count(*) FROM events").fetchone()[0]+1}'})
            for change in changes:
                change.ref=event.id
            self.connection.execute('INSERT INTO events VALUES (?,?,?,?)',(event.id,event.type,event.at.isoformat(),event.model_dump_json()))
            self.connection.execute('INSERT INTO audit(ts,actor,action,ref,detail) VALUES (?,?,?,?,?)',(event.at.isoformat(),'simulator','scenario.inject',event.id,event.model_dump_json()))
            self._save()
            return {'event_id':event.id,'effects':[f'SIMULATED {event.type} applied'],'twin_changes':[c.model_dump(mode='json') for c in changes],'twin_connected':self.ingest is not None}
