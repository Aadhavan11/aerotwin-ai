"""Fleet insights on SIMULATED data (CONTRACTS.md section 4b). Advisory only; nothing here is a certified analysis.

    GET    /insights/data-quality
    GET    /insights/reliability
    GET    /insights/reliability/pack
    GET    /insights/ata
    POST   /insights/weather            {station,start,duration_h,severity}
    GET    /insights/disruptions
    DELETE /insights/weather/{id}
    GET    /insights/legs/{leg_id}/profile
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app import settings
from app.insights import disruptions, quality, reliability, source
from app.sim.fleet import STATIONS

router = APIRouter(tags=["insights"])


class WeatherRequest(BaseModel):
    station: str
    start: datetime
    duration_h: float = Field(gt=0, le=72, allow_inf_nan=False)
    severity: float = Field(ge=0, le=1, allow_inf_nan=False)


def _err(status: int, error: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "detail": detail})


@router.get("/insights/data-quality")
def data_quality():
    return quality.report()


@router.get("/insights/reliability")
def reliability_report():
    return reliability.report()


@router.post("/insights/weather")
def post_weather(req: WeatherRequest):
    station = req.station.strip().upper()
    if station not in STATIONS:
        return _err(422, "unknown_station", f"unknown station {req.station!r}")
    start = req.start if req.start.tzinfo else req.start.replace(tzinfo=timezone.utc)
    event = disruptions.add_event(station, start, req.duration_h, req.severity)
    return {"simulated": True, **event}


@router.get("/insights/disruptions")
def get_disruptions():
    return disruptions.report()


@router.delete("/insights/weather/{event_id}")
def delete_weather(event_id: str):
    if not disruptions.remove_event(event_id):
        return _err(404, "not_found", f"no disruption {event_id!r}")
    return {"simulated": True, "deleted": event_id}


@router.get("/insights/legs/{leg_id}/profile")
def leg_profile(leg_id: str):
    from app.sim.profile import profile_for

    leg = source.find_leg(leg_id)
    if leg is None:
        return _err(404, "not_found", f"no leg {leg_id!r}")
    try:
        profile = profile_for(settings.SEED, leg)
    except ValueError as exc:
        return _err(422, "invalid_leg", str(exc))
    return {"simulated": True, "leg_id": leg.id, "tail": leg.tail, "dep": leg.dep, "arr": leg.arr, **asdict(profile)}


@router.get("/insights/reliability/pack")
def reliability_pack_report():
    from app.insights import reliability_pack

    return reliability_pack.pack()


@router.get("/insights/ata")
def ata_table():
    from app.taxonomy import ata

    return ata.table()
