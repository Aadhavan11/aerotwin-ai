"""Core endpoints (Shamit): /health, /fleet, /legs, /stations, /metrics.
They read the database when it has been seeded (scripts/reset_db.py) and MOCK is off; otherwise they serve the fixtures.
The header `X-Data-Source` says which ("database" or "fixture")."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Query, Response

from .. import db, fixtures, registry, settings
from ..contracts.models import (
    Aircraft,
    AircraftSummary,
    ComponentState,
    FleetKpis,
    FleetOverview,
    Leg,
    MetricsBundle,
    Station,
    Task,
)
from .mockstate import STATE

router = APIRouter(tags=["core"])


def _use_db() -> bool:
    """Database mode needs: MOCK off, the DB seeded, AND the real simulator (app.sim, Aadhavan) running, because the simulator is what changes
    the fleet. While `sim` is still a mock, /fleet must follow the mock state, otherwise injected anomalies would not show up on the Fleet screen."""
    return (not settings.MOCK) and registry.MODULES.get("sim") == "real" and db.is_seeded()


def _ml_data() -> str:
    """Which engine data the health model replays: 'nasa-cmapss-fd001', or the synthetic stand-in when the files are missing."""
    try:
        from app.ml import cmapss

        return cmapss.data_source()
    except Exception:  # noqa: BLE001
        return "unknown"


@router.get("/health")
def health():
    return {"ok": True, "mock": settings.MOCK, "version": settings.VERSION, "sim_now": (STATE.now if not _use_db() else db.sim_now()),
            "data_source": "database" if _use_db() else "fixture", "modules": registry.MODULES, "errors": registry.ERRORS,
            "llm_key_present": settings.HAS_LLM_KEY, "llm_model": settings.LLM_MODEL, "ml_data": _ml_data()}


def fleet_from_db() -> FleetOverview:
    aircraft = db.all_("aircraft", Aircraft, "tail")
    comps = [ComponentState.model_validate_json(r["state_json"]) for r in _rows("SELECT state_json FROM components")]
    worst: dict[str, ComponentState] = {}
    for c in comps:
        sev = {"aog": 0, "alert": 1, "watch": 2, "in_maintenance": 3, "ok": 4}
        key = lambda x: (sev.get(x.status, 9), x.rul_cycles.p10 if x.rul_cycles else float("inf"))  # noqa: E731  worst status first, then shortest life
        if c.tail not in worst or key(c) < key(worst[c.tail]):
            worst[c.tail] = c
    tasks = db.all_("tasks", Task)
    now = db.sim_now()
    summaries = [AircraftSummary(tail=a.tail, type=a.type, home=a.home, location=a.location, status=a.status,
                                 worst_component=worst[a.tail].component_id if a.tail in worst else None,
                                 rul_p10_min=worst[a.tail].rul_cycles.p10 if a.tail in worst and worst[a.tail].rul_cycles else None) for a in aircraft]
    kpis = FleetKpis(aircraft=len(aircraft), needing_attention=sum(1 for a in aircraft if a.status in ("watch", "alert")),
                     predicted_maintenance_72h=sum(1 for t in tasks if t.deadline <= now + timedelta(hours=72)),
                     in_maintenance=sum(1 for a in aircraft if a.status == "in_maintenance"),
                     readiness_pct=round(100 * sum(1 for a in aircraft if a.status not in ("in_maintenance", "aog")) / max(1, len(aircraft)), 1))
    return FleetOverview(kpis=kpis, aircraft=summaries, stations=db.all_("stations", Station, "iata"))


def _rows(sql: str):
    with db.conn() as c:
        return c.execute(sql).fetchall()


def fleet_from_twin() -> FleetOverview:
    """Aircraft status and worst-component RUL from the twin, stations from the simulator, task deadlines from the planner."""
    from app.twin import state as twin

    summaries = twin.aircraft_summaries()
    snap = twin.snapshot()
    due72 = 0
    try:
        from app.solver.service import get_state
        from app.solver.tasks import derive_tasks

        state, _src = get_state()
        due72 = sum(1 for t in derive_tasks(state) if t.deadline <= state.now + timedelta(hours=72))
    except Exception:  # noqa: BLE001  (planner not ready: show 0 rather than fail the page)
        pass
    n = len(summaries)
    kpis = FleetKpis(aircraft=n, needing_attention=sum(1 for a in summaries if a.status in ("watch", "alert")), predicted_maintenance_72h=due72,
                     in_maintenance=sum(1 for a in summaries if a.status == "in_maintenance"),
                     readiness_pct=round(100 * sum(1 for a in summaries if a.status not in ("in_maintenance", "aog")) / max(1, n), 1))
    return FleetOverview(kpis=kpis, aircraft=summaries, stations=snap.stations)


@router.get("/fleet", response_model=FleetOverview)
def fleet(response: Response):
    if registry.MODULES.get("twin") == "real" and registry.MODULES.get("sim") == "real":
        response.headers["X-Data-Source"] = "twin"
        return fleet_from_twin()
    if _use_db():
        response.headers["X-Data-Source"] = "database"
        return fleet_from_db()
    response.headers["X-Data-Source"] = "fixture"
    return STATE.fleet_overview()


@router.get("/stations", response_model=list[Station])
def stations(response: Response):
    if _use_db():
        response.headers["X-Data-Source"] = "database"
        return db.all_("stations", Station, "iata")
    response.headers["X-Data-Source"] = "fixture"
    return fixtures.load("stations", list[Station])


@router.get("/legs", response_model=list[Leg])
def legs(response: Response, tail: Optional[str] = Query(None)):
    if _use_db():
        response.headers["X-Data-Source"] = "database"
        with db.conn() as c:
            rows = c.execute("SELECT * FROM legs" + (" WHERE tail=?" if tail else "") + " ORDER BY tail, sched_dep", (tail,) if tail else ()).fetchall()
        return [Leg(id=r["id"], tail=r["tail"], dep=r["dep"], arr=r["arr"], sched_dep=r["sched_dep"], sched_arr=r["sched_arr"]) for r in rows]
    response.headers["X-Data-Source"] = "fixture"
    all_legs = fixtures.load("legs_golden", list[Leg])
    return [lg for lg in all_legs if not tail or lg.tail == tail]


@router.get("/metrics", response_model=MetricsBundle)
def metrics(response: Response):
    """ML block: app.ml.health.evaluate() (Aashirth) when real. Plan block: app.whatif.metrics.plan_block() (Pardhu) when real; the mock plan state when the
    solver is mock; otherwise fixtures. The header X-Data-Source says which parts are real."""
    out = fixtures.load("metrics", MetricsBundle)
    parts = []
    if registry.MODULES.get("whatif") == "real":
        try:
            from app.whatif.metrics import plan_block

            out.plan = plan_block()
            parts.append("plan:real")
        except Exception:  # noqa: BLE001
            pass
    if not parts and (settings.MOCK or str(registry.MODULES.get("solver", "mock")).startswith("mock")):
        out.plan = {"baseline": STATE.plan_baseline.metrics, "optimized": STATE.plan_current.metrics, "replan_seconds": 1.1, "note": "mock plan state, not results"}
        parts.append("plan:mock-state")
    if not settings.MOCK and registry.MODULES.get("twin") == "real":
        try:
            from app.ml.health import evaluate  # type: ignore

            out.ml = evaluate()
            parts.append("ml:real")
        except Exception:  # noqa: BLE001
            pass
    response.headers["X-Data-Source"] = "+".join(parts) or "fixture"
    return out
