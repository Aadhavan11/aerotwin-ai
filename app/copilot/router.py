"""HTTP endpoints for the copilot backend (CONTRACTS.md section 4).

    POST /copilot/chat                  ChatRequest -> ChatResponse
    GET  /workorders                    -> WorkOrder[]
    POST /workorders/{id}/decision      {decision: approve|reject, user, note} -> WorkOrder
    GET  /audit                         -> AuditEntry[] (newest first)

Mount with ``app.include_router(app.copilot.router.router)``. Errors are
``{"error": ..., "detail": ...}``. Call ``app.copilot.reset_all()`` on
``POST /scenario/reset``.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.contracts.models import AuditEntry, ChatRequest, ChatResponse, WorkOrder
from app.contracts.requests import DecisionRequest
from app.copilot import audit, chat as chat_mod, persist, workorders

router = APIRouter()


@router.post("/copilot/chat", response_model=ChatResponse)
def post_chat(req: ChatRequest):
    return chat_mod.chat(req)


@router.get("/copilot/quick-prompts")
def get_quick_prompts():
    return {"prompts": chat_mod.QUICK_PROMPTS}


@router.get("/workorders", response_model=list[WorkOrder])
def get_workorders():
    return workorders.list_orders()


@router.post("/workorders/{work_order_id}/decision", response_model=WorkOrder)
def post_decision(work_order_id: str, body: DecisionRequest):
    try:
        return workorders.decide(work_order_id, body.decision, body.user, body.note, persist.sim_now())
    except workorders.WorkOrderError as exc:
        return JSONResponse(status_code=exc.status, content={"error": exc.code, "detail": exc.message})


@router.get("/audit", response_model=list[AuditEntry])
def get_audit():
    """Newest first, like the mock API."""
    return audit.list_entries()
