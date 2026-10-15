"""Spares pool sizing routes (SIMULATED). GET only.

    GET /ops/spares?target_fill=0.95&budget_usd=500000&validate=false
    GET /ops/spares/demand
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.ops import spares

router = APIRouter(tags=["ops"])


@router.get("/ops/spares")
def spares_plan(target_fill: Optional[float] = Query(None, gt=0.0, lt=1.0), budget_usd: Optional[float] = Query(None, ge=0.0), validate: bool = False):
    # with no parameter the plan targets 0.95 per station; with only a budget it spends the budget by marginal analysis
    tf = 0.95 if target_fill is None and budget_usd is None else target_fill
    try:
        return spares.from_simulator(target_fill=tf, budget_usd=budget_usd, validate_trials=200 if validate else 0)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"error": "unavailable", "detail": str(exc), "simulated": True})


@router.get("/ops/spares/demand")
def spares_demand():
    try:
        return spares.demand_report()
    except Exception as exc:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"error": "unavailable", "detail": str(exc), "simulated": True})
