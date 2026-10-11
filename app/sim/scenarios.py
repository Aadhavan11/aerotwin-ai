"""Scenario entry point for callers that already have a simulator instance."""
from app.contracts.models import ScenarioEvent
from app.sim.service import Simulator

def inject(event: ScenarioEvent, simulator: Simulator):
    return simulator.inject(event)
