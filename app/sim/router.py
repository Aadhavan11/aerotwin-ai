"""Shared app entry point: bind the simulator to Shamit's database."""
from app import settings
from app.sim.api import create_router
from app.sim.integration import IntegratedSimulator

SIMULATOR = IntegratedSimulator(str(settings.DB_PATH),settings.SEED)
router = create_router(SIMULATOR)
