"""HTTP API for a simplified fictional register, NOT an MMEL/MEL or regulatory guidance.

All register payloads are simulated. Authentication follows the shared AuthGate.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field

from app.ops import mel


class _SimulatedRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()

        async def handle(request: Request) -> Response:
            try:
                return await handler(request)
            except RequestValidationError as exc:
                errors = [{key: value for key, value in error.items() if key != "ctx"} for error in exc.errors()]
                return JSONResponse(status_code=422, content=jsonable_encoder({"simulated": True, "error": "validation", "detail": errors}))

        return handle


router = APIRouter(prefix="/ops/mel", tags=["mel"], route_class=_SimulatedRoute)


class CreateRequest(BaseModel):
    tail: str = Field(min_length=1)
    component_kind: str
    fault_mode: str
    category: mel.Category | None = None
    limit_days: int | None = Field(default=None, gt=0, strict=True)
    reason: str = Field(min_length=1)
    notes: str = ""


class ExtendRequest(BaseModel):
    justification: str = Field(min_length=1)


def _actor(request: Request) -> str:
    return request.scope.get("state", {}).get("auth_name") or "local-user"


def _error(exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=exc.status if isinstance(exc, mel.MelError) else 422,
                        content={"simulated": True, "error": "mel", "detail": str(exc)})


@router.post("", response_model=mel.MelItem)
def create(body: CreateRequest, request: Request) -> mel.MelItem | JSONResponse:
    try:
        return mel.create_item(**body.model_dump(), deferred_by=_actor(request))
    except ValueError as exc:
        return _error(exc)


@router.get("", response_model=mel.MelList)
def list_register(tail: str | None = None, status: mel.Status | None = None) -> mel.MelList:
    return mel.MelList(items=mel.list_items(tail, status))


@router.get("/summary", response_model=mel.MelSummary)
def get_summary() -> mel.MelSummary:
    return mel.summary()


@router.get("/{item_id}", response_model=mel.MelItem)
def get(item_id: str) -> mel.MelItem | JSONResponse:
    try:
        return mel.get_item(item_id)
    except mel.MelError as exc:
        return _error(exc)


@router.post("/{item_id}/close", response_model=mel.MelItem)
def close(item_id: str, request: Request) -> mel.MelItem | JSONResponse:
    try:
        return mel.close_item(item_id, _actor(request))
    except mel.MelError as exc:
        return _error(exc)


@router.post("/{item_id}/extend", response_model=mel.MelItem)
def extend(item_id: str, body: ExtendRequest, request: Request) -> mel.MelItem | JSONResponse:
    try:
        return mel.extend_item(item_id, _actor(request), body.justification)
    except ValueError as exc:
        return _error(exc)
