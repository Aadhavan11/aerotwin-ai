"""run_workflow(goal, scope, mode) -> AgentRun (CONTRACTS.md section 5).

``background=True`` starts the run in a thread and returns immediately with ``status="running"`` (the UI polls
``GET /agents/runs/{id}``); the default runs to completion.

``mode="llm"`` runs ``app.agents.llm_mode`` (Aashirth, piece 12) when an API key is present. If it is not available, or anything
goes wrong (API error, a number no tool returned), the steps are cleared and the scripted workflow runs on the same run id; the
AgentRun records ``mode="scripted"`` and says why in ``missing_info`` (the contract: record what actually ran).
"""
from __future__ import annotations

import logging
import threading
from typing import Literal, Optional

from app.agents.runs import RUNS
from app.agents.scripted import run_scripted
from app.contracts.models import AgentRun

log = logging.getLogger(__name__)
LLM_NOTE = "LLM mode needs ANTHROPIC_API_KEY; the scripted workflow ran instead"


def _execute(run_id: str, goal: str, scope: dict, mode: str = "scripted") -> None:
    note: Optional[str] = None
    if mode == "llm":
        try:
            from app.agents.llm_mode import llm_ready, run_llm

            if llm_ready():
                run_llm(run_id, goal, scope)
                return
            note = LLM_NOTE
        except Exception as exc:  # noqa: BLE001  fall back to scripted on the same run, and say why
            log.warning("LLM mode failed for %s (%s: %s); running the scripted workflow", run_id, type(exc).__name__, exc)
            note = f"LLM mode did not finish ({type(exc).__name__}); the scripted workflow ran instead"
            RUNS.update(run_id, steps=[])
        RUNS.update(run_id, mode="scripted", status="running")
    try:
        run_scripted(run_id, goal, scope)
        if note:   # run_scripted sets missing_info itself, so add the note afterwards
            cur = RUNS.get(run_id)
            RUNS.update(run_id, missing_info=[note] + [m for m in (cur.missing_info if cur else []) if m != note])
    except Exception as exc:  # noqa: BLE001  a run must end in a visible state, never hang as "running"
        log.exception("agent run %s failed", run_id)
        RUNS.update(run_id, status="error", explanation=f"The run stopped because of an error: {type(exc).__name__}: {exc}")


def run_workflow(goal: str, scope: Optional[dict] = None, mode: Literal["llm", "scripted"] = "scripted",
                 background: bool = False) -> AgentRun:
    run = RUNS.create(goal, "llm" if mode == "llm" else "scripted")
    if background:
        threading.Thread(target=_execute, args=(run.id, goal, scope or {}, mode), daemon=True, name=run.id).start()
        return RUNS.get(run.id)  # type: ignore[return-value]
    _execute(run.id, goal, scope or {}, mode)
    return RUNS.get(run.id)  # type: ignore[return-value]
