"""The plan block of GET /metrics (MetricsBundle.plan): baseline vs optimized metrics and the last replan time."""
from __future__ import annotations

from app.solver import service
from app.solver.store import STORE


def plan_block() -> dict:
    """{"baseline": PlanMetrics, "optimized": PlanMetrics, "replan_seconds": float | None} as plain dicts.
    Shamit's /metrics handler merges this with Aashirth's ML block."""
    state, source = service.get_state()
    current, baseline = service.ensure_plans(state, source)
    return {
        "baseline": baseline.metrics.model_dump(),
        "optimized": current.metrics.model_dump(),
        "replan_seconds": STORE.last_replan_seconds,
    }
