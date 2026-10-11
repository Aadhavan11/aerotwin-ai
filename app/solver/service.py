"""Glue shared by the solver and what-if routers: where the FleetState comes from, and keeping the current / baseline plans.

State source (in order): MOCK=1 or MOCK_SOLVER=1 -> the golden stand-in; else Aashirth's ``app.twin.state.snapshot()``;
if that module does not exist yet -> the golden stand-in, with a warning and a note on every plan made from it.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from app.contracts.models import EvidenceRef, FleetState, ObjectiveWeights, Plan
from app.solver.engine import solve
from app.solver.store import STORE
from app.solver.tasks import derive_tasks

log = logging.getLogger(__name__)
SOLVE_LOCK = threading.RLock()     # one solve at a time: plan ids and the current plan stay coherent

STAND_IN_NOTE = "state source: golden stand-in (app.twin.state.snapshot is not available)"


def _snapshot() -> tuple[FleetState, str]:
    from app.solver.golden import golden_state
    if os.getenv("MOCK") == "1" or os.getenv("MOCK_SOLVER") == "1":
        return golden_state(), "golden stand-in (MOCK)"
    try:
        from app.twin.state import snapshot  # type: ignore[import-not-found]
    except ImportError:
        log.warning("app.twin.state.snapshot not available: using the golden stand-in state")
        return golden_state(), "golden stand-in (twin snapshot not available)"
    state = snapshot()
    try:
        from app.twin.state import list_aircraft  # type: ignore[import-not-found]
        if not list_aircraft():    # the twin is empty: its snapshot() hands back OUR golden stand-in; label it as such
            return state, "golden stand-in (twin is empty)"
    except ImportError:
        pass
    return state, "twin.snapshot"


def get_state() -> tuple[FleetState, str]:
    """(snapshot with the disruptions applied through POST /plan/replan, source label)."""
    from app.whatif.events import EventError, apply_event
    state, source = _snapshot()
    for ev in STORE.world_events:
        try:
            state, _ = apply_event(state, ev)
        except EventError as exc:   # the snapshot changed under us (e.g. a reset); skip the stale event
            log.warning("dropping stored event %s: %s", ev.id or ev.type, exc)
    return state, source


def _tag(plan: Plan, source: str) -> Plan:
    if source.startswith("golden"):
        return plan.model_copy(update={"evidence": list(plan.evidence) + [EvidenceRef(kind="plan", ref=plan.id, note=f"state source: {source}")]})
    return plan


def solve_and_save(state: FleetState, source: str, mode: str = "cpsat", weights: Optional[ObjectiveWeights] = None,
                   time_limit_s: float = 5.0) -> Plan:
    with SOLVE_LOCK:
        plan = _tag(solve(state, derive_tasks(state), weights, mode, time_limit_s), source)  # type: ignore[arg-type]
        return STORE.save(plan)


def ensure_plans(state: FleetState, source: str) -> tuple[Plan, Plan]:
    """(current optimized plan, static baseline), computing whichever is missing."""
    with SOLVE_LOCK:
        cur = STORE.current or solve_and_save(state, source, "cpsat")
        base = STORE.baseline or solve_and_save(state, source, "static_baseline")
        return cur, base
