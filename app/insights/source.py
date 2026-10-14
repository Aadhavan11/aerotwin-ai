"""Where the insights read their inputs: the live twin when it holds data, otherwise the seeded SIMULATED fleet.

Everything here is read-only. The fallback is deterministic (seed ``settings.SEED``): the generated fleet plus a
30-cycle stochastic telemetry window per component produced by the simulator's own telemetry source.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

from app import registry, settings
from app.contracts.models import ComponentState, Leg, TelemetryRow
from app.sim.fleet import generate_fleet
from app.sim.sensor_faults import faults_active

WINDOW = 30
BASIS_TWIN = "twin"
BASIS_SEED = "seeded simulated fleet"


@lru_cache(maxsize=4)
def _generated(seed: int):
    return generate_fleet(seed)


@lru_cache(maxsize=4)
def _synthetic(seed: int) -> tuple[dict[str, list[TelemetryRow]], dict[str, list[dict]]]:
    """A deterministic recent telemetry window per component, with the sensor-fault state that produced it."""
    from app.sim.telemetry import TelemetrySource

    source = TelemetrySource(seed, None, True)
    rows: dict[str, list[TelemetryRow]] = {}
    for c in _generated(seed).components:
        rows[c.component_id] = source.advance(c.tail, c.component_id, WINDOW)
    faults = {cid: faults_active(state) for cid, state in source.sensor_faults.items()}
    return rows, faults


def _twin_has_data() -> bool:
    try:
        from app.twin import state

        return not state.is_empty()
    except Exception:  # noqa: BLE001
        return False


def components() -> list[ComponentState]:
    if _twin_has_data():
        from app.twin import state

        return state.snapshot().components
    return list(_generated(settings.SEED).components)


def legs() -> list[Leg]:
    if _twin_has_data():
        from app.twin import state

        found = state.snapshot().legs
        if found:
            return list(found)
    return list(_generated(settings.SEED).legs)


def basis() -> str:
    return BASIS_TWIN if _twin_has_data() else BASIS_SEED


def telemetry(component: ComponentState) -> list[TelemetryRow]:
    """Latest rows (oldest first) of one component."""
    if _twin_has_data():
        from app.twin import state

        rows = [r for r in state.get_telemetry(component.tail, component.component_id, WINDOW) if r.component_id == component.component_id]
        return sorted(rows, key=lambda r: r.cycle)[-WINDOW:]
    return list(_synthetic(settings.SEED)[0].get(component.component_id, []))


def fault_state(component_id: str) -> list[dict]:
    """Sensor faults the simulator holds for this component (``[{"key","mode"}]``); empty when it is not stochastic."""
    if registry.MODULES.get("sim") == "real":
        try:
            from app.sim.router import simulator

            if simulator.source.stochastic:
                state = simulator.source.sensor_faults.get(component_id)
                return faults_active(state) if state else []
            return []
        except Exception:  # noqa: BLE001
            return []
    if _twin_has_data():
        return []
    return list(_synthetic(settings.SEED)[1].get(component_id, []))


def as_of() -> str:
    from app.copilot.persist import iso, sim_now

    return iso(sim_now())


def find_leg(leg_id: str) -> Optional[Leg]:
    return next((lg for lg in legs() if lg.id == leg_id), None)
