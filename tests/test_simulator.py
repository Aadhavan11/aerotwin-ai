from datetime import timedelta
import pytest
from app.contracts.models import ScenarioEvent
from app.sim.clock import START
from app.sim.fleet import generate_fleet, dump_fixtures
from app.sim.service import Simulator


def test_seed_and_rotations():
    state=generate_fleet()
    assert state==generate_fleet()
    assert state!=generate_fleet(99)
    assert len(state.aircraft)==24 and len(state.components)==144 and len(state.legs)==480
    for aircraft in state.aircraft:
        legs=[l for l in state.legs if l.tail==aircraft.tail]
        assert legs[0].dep==aircraft.home
        assert all(a.arr==b.dep and a.sched_arr<b.sched_dep for a,b in zip(legs,legs[1:]))
    legs=[l for l in state.legs if l.tail=='SIM-017']
    windows=[(a.sched_arr,b.sched_dep) for a,b in zip(legs,legs[1:]) if a.arr=='DFW' and b.sched_dep-a.sched_arr>=timedelta(hours=6)]
    assert windows
    dfw=next(s for s in state.stations if s.iata=='DFW')
    atl=next(s for s in state.stations if s.iata=='ATL')
    assert next(p for p in dfw.parts if p.part=='BORESCOPE-KIT').qty==2
    stock=next(p for p in atl.parts if p.part=='BORESCOPE-KIT')
    assert atl.hangar_slots>0 and stock.qty==0 and stock.eta_hours==18
    assert any(t.shift_start<=start and t.shift_end>=end and 'engine' in t.skills for start,end in windows for t in dfw.technicians)


def test_step_reset_and_resume(tmp_path):
    sim=Simulator(str(tmp_path/'sim.db'))
    original=sim.state.model_dump_json()
    assert sim.step(24)['ingested']==24*4*6
    assert sim.step(0)['ingested']==0
    resumed=Simulator(str(tmp_path/'sim.db'))
    assert resumed.state==sim.state
    assert resumed.source.positions==sim.source.positions
    assert resumed.step(1)['ingested']==0
    assert resumed.step(1)['ingested']==144
    resumed.reset()
    assert resumed.state.model_dump_json()==original
    assert resumed.clock.now()==START


def test_events_and_validation():
    rows=[]
    sim=Simulator(':memory:',ingest=lambda row:rows.append(row))
    cid='SIM-017/engine_2'
    sim.step(24)
    before=[r.values['T50'] for r in rows if r.component_id==cid][-1]
    result=sim.inject(ScenarioEvent(type='anomaly',at=sim.state.now,tail='SIM-017',component_id=cid))
    assert result['twin_changes'][0]['ref']==result['event_id']
    assert [r.values['T50'] for r in rows if r.component_id==cid][-1]>before+100
    sim.inject(ScenarioEvent(type='part_delay',at=sim.state.now,station='DFW',part='BORESCOPE-KIT',delay_hours=12))
    assert next(p for s in sim.state.stations if s.iata=='DFW' for p in s.parts if p.part=='BORESCOPE-KIT').eta_hours==12
    sim.inject(ScenarioEvent(type='facility_closed',at=sim.state.now,station='ATL'))
    assert next(s for s in sim.state.stations if s.iata=='ATL').hangar_slots==0
    before=sim.state.model_dump_json()
    with pytest.raises(ValueError):
        sim.inject(ScenarioEvent(type='part_delay',at=sim.state.now,station='DFW',part='bad',delay_hours=-1))
    assert before==sim.state.model_dump_json()
    for hours in (-1,float('nan'),float('inf'),121):
        with pytest.raises(ValueError): sim.step(hours)


def test_fixtures(tmp_path):
    from app.contracts.models import FleetOverview, Station, Leg
    import json
    dump_fixtures(generate_fleet(),tmp_path)
    FleetOverview.model_validate_json((tmp_path/'fleet.json').read_text())
    for record in json.loads((tmp_path/'stations.json').read_text()): Station.model_validate(record)
    for record in json.loads((tmp_path/'legs_golden.json').read_text()): Leg.model_validate(record)


def test_http_endpoints():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.sim.api import create_router
    sim=Simulator(':memory:')
    app=FastAPI()
    app.include_router(create_router(sim))
    with TestClient(app) as client:
        assert client.post('/sim/step',json={'hours':24}).json()['ingested']==576
        assert client.post('/sim/step',json={'hours':-1}).status_code==422
        response=client.post('/scenario/inject',json={'type':'anomaly','at':sim.state.now.isoformat(),'tail':'SIM-017','component_id':'SIM-017/engine_2'})
        assert response.status_code==200
        assert client.post('/scenario/reset',json={'name':'unknown'}).status_code==422
        assert client.post('/scenario/reset',json={'name':'golden'}).json()=={'ok':True}


def test_replay_restart_and_faults(tmp_path):
    from app.sim.telemetry import TelemetrySource
    trajectories={1:[{'cycle':i+1,'values':{'T50':1000.+i},'true_rul':99-i} for i in range(100)]}
    sim=Simulator(str(tmp_path/'replay.db'),trajectories=trajectories)
    sim.step(2)
    cid='SIM-017/engine_2'
    sim.inject(ScenarioEvent(type='anomaly',at=sim.state.now,tail='SIM-017',component_id=cid))
    assert sim.source.source=='cmapss_replay'
    restarted=Simulator(str(tmp_path/'replay.db'),trajectories=trajectories)
    assert restarted.source.faults==sim.source.faults
    assert restarted.source.advance('SIM-017',cid,1)==sim.source.advance('SIM-017',cid,1)
    source=TelemetrySource()
    a=source.advance('SIM-001','SIM-001/brakes',2)
    source.inject('SIM-001/brakes','accelerated_degradation')
    b=source.advance('SIM-001','SIM-001/brakes',1)
    assert b[0].values['pad_wear_mm']>a[-1].values['pad_wear_mm']
    sim.inject(ScenarioEvent(type='tech_unavailable',at=sim.state.now,station='DFW',technician_id='DFW-engine-0'))
    assert not next(t for s in sim.state.stations if s.iata=='DFW' for t in s.technicians if t.id=='DFW-engine-0').available


def test_failed_ingestion_rolls_back_simulator():
    def failed(row):
        raise RuntimeError('twin failed')
    sim=Simulator(':memory:',ingest=failed)
    initial=sim.state.model_dump_json()
    positions=dict(sim.source.positions)
    with pytest.raises(RuntimeError): sim.step(2)
    assert sim.state.model_dump_json()==initial
    assert sim.source.positions==positions
    assert sim.connection.execute('SELECT count(*) FROM telemetry').fetchone()[0]==0
    sim.ingest=None
    assert sim.step(2)['ingested']==144
