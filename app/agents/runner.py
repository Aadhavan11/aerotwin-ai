"""run_workflow(goal, scope, mode) -> AgentRun (CONTRACTS.md section 5).

``background=True`` starts the run in a thread and returns immediately with ``status="running"`` (the UI polls
``GET /agents/runs/{id}``); the default runs to completion. ``mode="llm"`` is not built here (piece 12): the scripted workflow runs
and the AgentRun records ``mode="scripted"`` plus a note in ``missing_info`` (the contract says: record what actually ran).
"""
from __future__ import annotations

import logging
import threading
from typing import Literal, Optional

from app.agents.runs import RUNS
from app.agents.scripted import run_scripted
from app.contracts.models import AgentRun

log = logging.getLogger(__name__)
LLM_NOTE = "LLM mode is not available in this build; the scripted workflow ran instead"


def _execute(run_id: str, goal: str, scope: dict) -> None:
    try:
        run_scripted(run_id, goal, scope)
    except Exception as exc:  # noqa: BLE001  a run must end in a visible state, never hang as "running"
        log.exception("agent run %s failed", run_id)
        RUNS.update(run_id, status="error", explanation=f"The run stopped because of an error: {type(exc).__name__}: {exc}")


def run_workflow(goal: str, scope: Optional[dict] = None, mode: Literal["llm", "scripted"] = "scripted",
                 background: bool = False) -> AgentRun:
    run = RUNS.create(goal, "scripted")
    if mode == "llm":
        RUNS.update(run.id, missing_info=[LLM_NOTE])
    if background:
        threading.Thread(target=_execute, args=(run.id, goal, scope or {}), daemon=True, name=run.id).start()
        return RUNS.get(run.id)  # type: ignore[return-value]
    _execute(run.id, goal, scope or {})
    final = RUNS.get(run.id)
    if mode == "llm" and final and LLM_NOTE not in final.missing_info:
        final = RUNS.update(run.id, missing_info=[LLM_NOTE] + list(final.missing_info))
    return final  # type: ignore[return-value]
