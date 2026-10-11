"""HTTP endpoints owned by the twin and ML modules (CONTRACTS.md section 4).

    GET /aircraft/{tail}                              -> TwinView
    GET /aircraft/{tail}/telemetry?component=&last=60 -> TelemetryRow[]
    GET /metrics/ml                                   -> the "ml" block of GET /metrics

Mount with ``app.include_router(app.twin.router.router)``. Errors are
``{"error": ..., "detail": ...}`` as the contract requires.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.contracts.models import TelemetryRow, TwinView
from app.ml import health
from app.twin import state

router = APIRouter()


def _err(status: int, error: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "detail": detail})


@router.get("/aircraft/{tail}", response_model=TwinView)
def get_aircraft(tail: str):
    twin = state.get_twin(tail)
    if twin is None:
        return _err(404, "not_found", f"no aircraft {tail!r} in the twin")
    return twin


@router.get("/aircraft/{tail}/telemetry", response_model=list[TelemetryRow])
def get_aircraft_telemetry(tail: str, component: Optional[str] = None, last: int = Query(60, ge=1, le=400)):
    if state.get_twin(tail) is None:
        return _err(404, "not_found", f"no aircraft {tail!r} in the twin")
    return state.get_telemetry(tail, component, last)


@router.get("/metrics/ml")
def get_ml_metrics():
    return health.ml_metrics_block()
