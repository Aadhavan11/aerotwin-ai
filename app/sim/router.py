"""Mounts the simulator in the shared app. Wiring to the twin, solver and agents is Aadhavan's IntegratedSimulator (app/sim/integration.py, unchanged).
The subclass only adds what no one's module did: reset the copilot's work-order/audit/chat stores with the scenario, and complete approved work orders whose window ended on /sim/step."""
from app import settings
from app.sim.api import create_router
from app.sim.integration import IntegratedSimulator
from app.sim.v2.runs_api import router as runs_router


class PlatformSimulator(IntegratedSimulator):
    def reset(self, name="golden"):
        out = super().reset(name)
        try:
            from app.copilot import reset_all

            reset_all()
        except ModuleNotFoundError:
            pass
        return out

    def _restart_series(self, component_id):
        """A maintained component starts a new series: counters back to 0, no carried fault, wear back to new."""
        self.source.restore(component_id)
        for component in self.state.components:
            if component.component_id == component_id:
                component.cycles, component.hours, component.last_maintenance = 0, 0.0, self.state.now

    def step(self, hours):
        out = super().step(hours)
        try:
            from app.copilot import workorders

            done = workorders.complete_due(self.state.now)
            if done:
                for w in done:
                    found = workorders.component_of(w.task_id)
                    if found:
                        self._restart_series(found[1])
                self._sync()  # the twin changed (a component was maintained): refresh the simulator's copy
                out = {**out, "work_orders_completed": [w.id for w in done]}
        except ModuleNotFoundError:
            pass
        return out


simulator = PlatformSimulator(str(settings.DB_PATH), settings.SEED)
router = create_router(simulator)
router.include_router(runs_router)  # POST/GET /sim/runs: v2 background backfill (SIMULATED), see app/sim/v2/runner.py
