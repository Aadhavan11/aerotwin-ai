"""LLP and shop-visit routes (SIMULATED). GET only, open to every signed-in role (no GUARDS rule needed).

    GET /ops/llp?tail=SIM-001
    GET /ops/llp/{tail}
    GET /ops/shop-visits?tail=SIM-001
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.ops import llp

router = APIRouter(tags=["ops"])


def _unknown(tail: str) -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": "not_found", "detail": f"no aircraft {tail!r}"})


def _known(tail: str) -> bool:
    tails, _, _, _ = llp.fleet_inputs()
    return any(t.tail == tail for t in tails)


@router.get("/ops/llp")
def list_llp(tail: Optional[str] = None):
    if tail is not None and not _known(tail):
        return _unknown(tail)
    return llp.fleet_parts(tail)


@router.get("/ops/llp/{tail}")
def tail_llp(tail: str):
    out = llp.tail_report(tail)
    return out if out is not None else _unknown(tail)


@router.get("/ops/shop-visits")
def shop_visit_pipeline(tail: Optional[str] = None):
    if tail is not None and not _known(tail):
        return _unknown(tail)
    return llp.shop_visits(tail)
