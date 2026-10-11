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

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

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


@router.get("/aircraft/{tail}/maintenance")
def get_aircraft_maintenance(tail: str):
    """Planned work, work orders, parts and technicians, suggestions and history for one aircraft (read only)."""
    from app.copilot import maintenance

    out = maintenance.build(tail)
    if out is None:
        return JSONResponse(status_code=404, content={"error": "not_found", "detail": f"no aircraft {tail!r}"})
    return out


@router.get("/maintenance/queue")
def get_maintenance_queue():
    """Every open task in the fleet with its planned slot, deadline margin and work-order status (read only)."""
    from app.copilot import maintenance

    return maintenance.queue()


@router.get("/audit", response_model=list[AuditEntry])
def get_audit():
    """Newest first, like the mock API."""
    return audit.list_entries()
