"""Integration tests use deterministic scoring so no local dataset is required."""
import pytest
from app.contracts.models import DataQuality, HealthScore, RulEstimate, ScenarioEvent
from app.sim.integration import IntegratedSimulator
from app.twin import state as twin
from app.solver.store import STORE
from app.agents.runs import RUNS

@pytest.fixture(autouse=True)
def clean_twin(monkeypatch):
    twin.reset()
    STORE.reset()
    RUNS.reset()
    def score(kind,tail,cid,rows):
        fault=rows[-1].values.get('T50',0)>1100
        return HealthScore(tail=tail,component_id=cid,health=.3 if fault else .95,anomaly_score=.9 if fault else .02,is_anomaly=fault,rul_cycles=RulEstimate(p10=22 if fault else 120,p50=25 if fault else 150,p90=30 if fault else 180),data_quality=DataQuality(status='ok'),model_version='integration-test',top_signals=['T50'] if kind=='engine' else [])
    monkeypatch.setattr('app.ml.health.score_component',score)
    yield
    twin.reset()
    twin.set_now_provider(None)
    twin.set_ops_provider(None)
    STORE.reset()
    RUNS.reset()


def replay():
    return {1:[{'cycle':i+1,'values':{'T50':1000.+i},'true_rul':159-i} for i in range(160)]}


def test_live_snapshot_reset_and_restart(tmp_path):
    path=str(tmp_path/'sim.db')
    sim=IntegratedSimulator(path,trajectories=replay())
    assert len(twin.snapshot().aircraft)==24
    assert len(twin.snapshot().legs)==480
    assert sim.step(24)['ingested']==576
    sim.inject(ScenarioEvent(type='anomaly',at=sim.state.now,tail='SIM-017',component_id='SIM-017/engine_2'))
    view=twin.get_twin('SIM-017')
    assert view.aircraft.status=='alert'
    component=next(c for c in view.components if c.component_id=='SIM-017/engine_2')
    assert component.rul_cycles.p10==22
    assert any(e.kind=='fault_injected' for e in view.timeline)
    sim.inject(ScenarioEvent(type='part_delay',at=sim.state.now,station='DFW',part='BORESCOPE-KIT',delay_hours=12))
    assert next(p for s in twin.snapshot().stations if s.iata=='DFW' for p in s.parts if p.part=='BORESCOPE-KIT').eta_hours==12
    timeline=view.timeline
    restarted=IntegratedSimulator(path,trajectories=replay())
    assert twin.snapshot().now==sim.state.now
    assert twin.get_twin('SIM-017').timeline==timeline
    assert next(c for c in twin.snapshot().components if c.component_id==component.component_id).hours==component.hours
    assert restarted.source.units==sim.source.units
    restarted.reset()
    assert not twin.get_twin('SIM-017').timeline
    assert not RUNS.get('R-1') and STORE.current is None
    assert all(c.rul_cycles is None for c in twin.snapshot().components)


def test_agents_and_replan_use_live_state():
    from app.agents.runner import run_workflow
    from app.whatif.runner import replan_and_store
    sim=IntegratedSimulator(':memory:',trajectories=replay())
    sim.step(24)
    sim.inject(ScenarioEvent(type='anomaly',at=sim.state.now,tail='SIM-017',component_id='SIM-017/engine_2'))
    run=run_workflow('Investigate SIM-017',{'tails':['SIM-017']})
    assert run.status=='done' and run.work_orders
    assert all(w.status=='pending_approval' and w.evidence for w in run.work_orders)
    assert STORE.current.feasible
    event=ScenarioEvent(type='part_delay',at=sim.state.now,station='DFW',part='BORESCOPE-KIT',delay_hours=36)
    sim.inject(event)
    result=replan_and_store(event,already_applied=True)
    assert not STORE.world_events  # do not apply the same part delay twice
    assert result.diff.moved or result.diff.added or result.diff.dropped


def test_integration_server_mounts_team_routes(monkeypatch):
    from fastapi.testclient import TestClient
    from app.sim.integration import create_app
    monkeypatch.setattr('app.ml.cmapss.trajectories',replay)
    app=create_app(':memory:')
    with TestClient(app) as client:
        assert client.get('/health').json()['twin_connected'] is True
        assert client.get('/fleet').json()['kpis']['aircraft']==24
        assert len(client.get('/legs?tail=SIM-017').json())==20
        assert len(client.get('/aircraft/SIM-017').json()['components'])==6
        assert client.get('/aircraft/unknown').status_code==404
        assert client.post('/scenario/reset',json={'name':'golden'}).status_code==200
        paths=client.get('/openapi.json').json()['paths']
        assert all(p in paths for p in ('/sim/step','/scenario/inject','/plan','/plan/replan','/agents/run'))
