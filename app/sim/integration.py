"""Connect the simulator to the team's public twin interfaces.

The integration server is deliberately separate from Shamit's app.main.
Use one process: the team's twin, plans and runs currently use global stores.
"""
import json
import os
from datetime import timedelta
from fastapi import FastAPI
from app.contracts.models import FleetKpis, FleetOverview, TelemetryRow, TwinEvent
from app.ml import cmapss, health
from app.twin import state as twin
from app.sim.api import create_router
from app.sim.service import Simulator
from app.solver.tasks import derive_tasks

class IntegratedSimulator(Simulator):
    def __init__(self, db_path='data/aerotwin.db', seed=1234, trajectories=None):
        self._connected = False
        super().__init__(db_path,seed,trajectories=trajectories if trajectories is not None else cmapss.trajectories())
        self.source.source = cmapss.data_source() if trajectories is None else 'provided_replay'
        self._connected = True
        self._restore_twin()
        self.ingest = twin.ingest_telemetry

    def _register(self):
        for aircraft in self.state.aircraft:
            twin.register_aircraft(aircraft.model_copy(deep=True))

    def _restore_twin(self):
        twin.reset()
        twin.set_now_provider(self.clock.now)
        twin.set_ops_provider(lambda: ([l.model_copy(deep=True) for l in self.state.legs], [s.model_copy(deep=True) for s in self.state.stations]))
        self._register()
        twin.seed_components([c.model_copy(deep=True) for c in self.state.components])
        # Reconstruct scoring windows from durable rows without emitting duplicate alerts.
        for tail,cid,cycle,ts,values in self.connection.execute('SELECT tail,component_id,cycle,ts,values_json FROM telemetry ORDER BY ts,component_id,cycle'):
            twin.ingest_telemetry(TelemetryRow(tail=tail,component_id=cid,cycle=cycle,ts=ts,values=json.loads(values)),backfill=True)
        for component in self.state.components:
            twin.update_component_operations(component.component_id,component.cycles,component.hours)
        saved = self.connection.execute("SELECT value FROM kv WHERE key='sim_timeline'").fetchone()
        if saved:
            for tail,events in json.loads(saved[0]).items():
                for event in events:
                    twin.add_timeline_event(tail,TwinEvent.model_validate(event))

    def _sync(self):
        self._register()
        for component in self.state.components:
            twin.update_component_operations(component.component_id,component.cycles,component.hours)
        snapshot=twin.snapshot()
        # Public snapshot owns scored component state and aircraft risk status.
        by_id={c.component_id:c for c in snapshot.components}
        for component in self.state.components:
            if component.component_id in by_id:
                component.health=by_id[component.component_id].health
                component.anomaly_score=by_id[component.component_id].anomaly_score
                component.rul_cycles=by_id[component.component_id].rul_cycles
                component.status=by_id[component.component_id].status
                component.data_quality=by_id[component.component_id].data_quality
        self.state.aircraft=snapshot.aircraft
        timelines={a.tail:[e.model_dump(mode='json') for e in twin.get_twin(a.tail).timeline] for a in self.state.aircraft}
        self.connection.execute("INSERT OR REPLACE INTO kv VALUES ('sim_timeline',?)",(json.dumps(timelines),))
        self._save()

    def reset(self,name='golden'):
        result=super().reset(name)
        if self._connected:
            from app.solver.store import STORE
            from app.agents.runs import RUNS
            STORE.reset()
            RUNS.reset()
            self._restore_twin()
        return result

    def step(self,hours):
        with self.lock:
            old=self.state.now
            previous_hours={c.component_id:c.hours for c in self.state.components}
            try:
                result=super().step(hours)
            except Exception:
                self._restore_twin()
                raise
            completed=[l for l in self.state.legs if old<l.sched_arr<=self.state.now]
            for component in self.state.components:
                duration=sum((l.sched_arr-l.sched_dep).total_seconds()/3600 for l in completed if l.tail==component.tail)
                component.hours=previous_hours[component.component_id]+duration
            for leg in completed:
                twin.add_timeline_event(leg.tail,TwinEvent(ts=leg.sched_arr,kind='flight',summary=f'SIMULATED flight {leg.dep} → {leg.arr}',ref=leg.id))
            self._sync()
            return result

    def inject(self,event):
        with self.lock:
            try:
                result=super().inject(event)
            except Exception:
                self._restore_twin()
                raise
            stored=event.model_copy(update={'id':result['event_id']})
            changes=twin.apply_event(stored)
            result['twin_changes']=[e.model_dump(mode='json') for e in changes]
            self._sync()
            return result


def create_app(db_path=None):
    from app.twin.router import router as twin_router
    from app.solver.router import router as solver_router
    from app.whatif.router import router as whatif_router
    from app.agents.router import router as agents_router
    simulator=IntegratedSimulator(db_path or os.getenv('DB_PATH','data/aerotwin.db'),int(os.getenv('SEED','1234')))
    app=FastAPI(title='AeroTwin SIMULATED integration server')
    app.state.simulator=simulator
    for router in (create_router(simulator),twin_router,solver_router,whatif_router,agents_router):
        app.include_router(router)
    @app.get('/health')
    def status():
        return {'ok':True,'mock':False,'version':'simulator-integration-1','sim_now':simulator.state.now,'synthetic':True,'telemetry_source':simulator.source.source,'model_available':health.model_available(),'twin_connected':True}
    @app.get('/fleet',response_model=FleetOverview)
    def fleet():
        aircraft=twin.aircraft_summaries()
        return FleetOverview(aircraft=aircraft,stations=simulator.state.stations,kpis=FleetKpis(aircraft=len(aircraft),needing_attention=sum(a.status in ('watch','alert') for a in aircraft),predicted_maintenance_72h=sum(t.deadline<=simulator.state.now+timedelta(hours=72) for t in derive_tasks(twin.snapshot())),in_maintenance=sum(a.status=='in_maintenance' for a in aircraft),readiness_pct=100*sum(a.status not in ('in_maintenance','aog') for a in aircraft)/len(aircraft)))
    @app.get('/legs')
    def legs(tail: str|None=None):
        return [l for l in simulator.state.legs if tail is None or l.tail==tail]
    return app
