"""HTTP endpoints for the copilot backend (CONTRACTS.md section 4).

    POST /copilot/chat                  ChatRequest -> ChatResponse
    GET  /workorders                    -> WorkOrder[]
    POST /workorders/{id}/decision      {decision: approve|reject, user, note} -> WorkOrder
    GET  /audit                         -> AuditEntry[] (newest first)
    GET  /aircraft/{tail}/maintenance   -> planned work, orders, parts, suggestions, history for one aircraft (additive)
    GET  /maintenance/queue             -> every open task in the fleet with slot, margin, order status (additive)

Mount with ``app.include_router(app.copilot.router.router)``. Errors are
``{"error": ..., "detail": ...}``. Call ``app.copilot.reset_all()`` on
``POST /scenario/reset``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.contracts.models import AuditEntry, ChatRequest, ChatResponse, WorkOrder
from app.contracts.requests import DecisionRequest
from app.copilot import audit, chat as chat_mod, persist, workorders

router = APIRouter()


@router.post("/copilot/chat", response_model=ChatResponse)
def post_chat(req: ChatRequest):
    return chat_mod.chat(req)


@router.get("/copilot/status")
def get_copilot_status():
    """Is LLM mode available, and what produced the last answer. Only booleans and the model name; never the key."""
    from app import settings
    from app.copilot import llm

    return {"llm_key_present": settings.HAS_LLM_KEY or llm.llm_available(), "llm_available": llm.llm_available(),
            "model": settings.LLM_MODEL, "last_mode": chat_mod.LAST_MODE["mode"], "last_note": chat_mod.LAST_MODE["note"]}


@router.get("/copilot/quick-prompts")
def get_quick_prompts():
    return {"prompts": chat_mod.QUICK_PROMPTS}


@router.get("/workorders", response_model=list[WorkOrder])
def get_workorders():
    return workorders.list_orders()


@router.post("/workorders/{work_order_id}/decision", response_model=WorkOrder)
def post_decision(work_order_id: str, body: DecisionRequest, request: Request):
    # With sign-in on, the audit log records the signed-in person, whatever the request body says.
    actor = request.scope.get("state", {}).get("auth_name") or body.user
    try:
        return workorders.decide(work_order_id, body.decision, actor, body.note, persist.sim_now())
    except workorders.WorkOrderError as exc:
        return JSONResponse(status_code=exc.status, content={"error": exc.code, "detail": exc.message})


class _EditBody(BaseModel):
    user: str
    title: Optional[str] = None
    work: Optional[str] = None
    description: Optional[str] = None
    part: Optional[str] = None
    part_qty: Optional[int] = None
    order_part: Optional[bool] = None
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    note: str = ""


class _NewBody(BaseModel):
    user: str
    tail: str
    station: str
    start: datetime
    end: datetime
    title: str
    work: str = ""
    part: Optional[str] = None
    part_qty: int = 0
    order_part: bool = False
    why: str = ""


@router.post("/workorders", response_model=WorkOrder)
def post_work_order(body: _NewBody, request: Request):
    """Enter a maintenance request by hand. It waits for approval like the ones the agents draft."""
    actor = request.scope.get("state", {}).get("auth_name") or body.user
    try:
        return workorders.create_manual(actor, tail=body.tail, station=body.station, start=body.start, end=body.end, title=body.title, work=body.work,
                                        part=body.part, part_qty=body.part_qty, order_part=body.order_part, why=body.why)
    except workorders.WorkOrderError as exc:
        return JSONResponse(status_code=exc.status, content={"error": exc.code, "detail": exc.message})


@router.patch("/workorders/{work_order_id}", response_model=WorkOrder)
def patch_work_order(work_order_id: str, body: _EditBody, request: Request):
    """Edit a pending request: name, work, part, whether the part must be ordered, and the time (checked against the deadline, the aircraft's ground time and the technician's shift)."""
    actor = request.scope.get("state", {}).get("auth_name") or body.user
    try:
        return workorders.edit(work_order_id, actor, title=body.title, work=body.work, description=body.description, part=body.part, part_qty=body.part_qty,
                               order_part=body.order_part, start=body.start, end=body.end, note=body.note)
    except workorders.WorkOrderError as exc:
        return JSONResponse(status_code=exc.status, content={"error": exc.code, "detail": exc.message})


@router.get("/workorders/{work_order_id}/options")
def get_work_order_options(work_order_id: str):
    """What can be changed on this request, and the limits (ground windows, deadline, technician shift, part stock)."""
    try:
        return workorders.options(work_order_id)
    except workorders.WorkOrderError as exc:
        return JSONResponse(status_code=exc.status, content={"error": exc.code, "detail": exc.message})


@router.get("/aircraft/{tail}/maintenance")
def get_aircraft_maintenance(tail: str):
    """Planned work, work orders, parts and technicians, suggestions and history for one aircraft (read only)."""
    from app.copilot import maintenance

    out = maintenance.build(tail)
    if out is None:
        return JSONResponse(status_code=404, content={"error": "not_found", "detail": f"no aircraft {tail!r}"})
    return out


@router.get("/maintenance/suggestions")
def get_maintenance_suggestions(limit: int = 8):
    """Work that is not pending or required now but is better planned ahead (advice, not tasks), each with a ground window that fits."""
    from app.copilot import maintenance

    return maintenance.fleet_suggestions(max(1, min(limit, 20)))


@router.get("/maintenance/queue")
def get_maintenance_queue():
    """Every open task in the fleet with its planned slot, deadline margin and work-order status (read only)."""
    from app.copilot import maintenance

    return maintenance.queue()


@router.get("/audit", response_model=list[AuditEntry])
def get_audit():
    """Newest first, like the mock API."""
    return audit.list_entries()
