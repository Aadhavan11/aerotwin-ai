"""Mount router in the shared app; standalone server is for simulator development."""
import os
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field
from app.contracts.models import ScenarioEvent
from app.sim.service import Simulator

class StepRequest(BaseModel):
    hours: float = Field(ge=0,le=120,allow_inf_nan=False)

class ResetRequest(BaseModel):
    name: str = 'golden'

def create_router(simulator):
    router=APIRouter()
    def call(fn,*args):
        try:
            return fn(*args)
        except ValueError as exc:
            raise HTTPException(status_code=422,detail=str(exc)) from exc
    @router.post('/sim/step')
    def step(request: StepRequest):
        return call(simulator.step,request.hours)
    @router.post('/scenario/inject')
    def inject(event: ScenarioEvent):
        return call(simulator.inject,event)
    @router.post('/scenario/reset')
    def reset(request: ResetRequest):
        return call(simulator.reset,request.name)
    return router

def create_app():
    from importlib.util import find_spec
    ingest, trajectories = None, None
    try:
        twin_available = find_spec('app.twin.state') is not None
    except ModuleNotFoundError:
        twin_available = False
    if twin_available:
        from app.twin.state import ingest_telemetry
        ingest = ingest_telemetry
    try:
        cmapss_available = find_spec('app.ml.cmapss') is not None
    except ModuleNotFoundError:
        cmapss_available = False
    if cmapss_available:
        from app.ml.cmapss import trajectories as load_trajectories
        trajectories = load_trajectories()
    simulator=Simulator(os.getenv('DB_PATH','data/aerotwin.db'),int(os.getenv('SEED','1234')),ingest=ingest,trajectories=trajectories)
    app=FastAPI(title='AeroTwin SIMULATED data simulator')
    app.include_router(create_router(simulator))
    @app.get('/health')
    def health():
        return {'ok':True,'mock':False,'version':'simulator-1','sim_now':simulator.state.now,'synthetic':True,'twin_connected':simulator.ingest is not None}
    return app
