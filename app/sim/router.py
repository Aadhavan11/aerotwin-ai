"""Mounts the simulator in the shared app. Wiring to the twin, solver and agents is Aadhavan's IntegratedSimulator (app/sim/integration.py, unchanged).
The subclass only adds what no one's module did: reset the copilot's work-order/audit/chat stores with the scenario, and complete approved work orders whose window ended on /sim/step."""
from app import settings
from app.sim.api import create_router
from app.sim.integration import IntegratedSimulator


class PlatformSimulator(IntegratedSimulator):
    def reset(self, name="golden"):
        out = super().reset(name)
        try:
            from app.copilot import reset_all

            reset_all()
        except ModuleNotFoundError:
            pass
        return out

    def step(self, hours):
        out = super().step(hours)
        try:
            from app.copilot import workorders

            done = workorders.complete_due(self.state.now)
            if done:
                self._sync()  # the twin changed (a component was maintained): refresh the simulator's copy
                out = {**out, "work_orders_completed": [w.id for w in done]}
        except ModuleNotFoundError:
            pass
        return out


simulator = PlatformSimulator(str(settings.DB_PATH), settings.SEED)
router = create_router(simulator)
