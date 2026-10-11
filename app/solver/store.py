"""In-memory plan store: the current (optimized) plan, the static baseline, and plan ids.

The scheduler functions (solve, replan) are PURE: they never write here. The routers do, and Shamit's tool wrappers can call
``STORE.save(plan)`` so ``GET /plan`` shows what the agents produced. ``world_events`` remembers the disruptions applied through
``POST /plan/replan`` (the snapshot itself is not written back), so later plans, the baseline and the what-if scenarios are all
computed in the same changed world. ``STORE.add_listener(fn)`` lets his db/audit layer
persist every saved plan (``fn(plan)``) without this module importing the database.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from app.contracts.models import Plan, ScenarioEvent


class PlanStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._n = 0
        self.current: Optional[Plan] = None
        self.baseline: Optional[Plan] = None
        self.history: list[Plan] = []
        self.last_replan_seconds: Optional[float] = None
        self.world_events: list[ScenarioEvent] = []     # disruptions applied through POST /plan/replan
        self.greedy_cache: dict[str, Plan] = {}         # current plan id -> greedy plan on the same world, for GET /plan
        self._listeners: list[Callable[[Plan], None]] = []

    def next_plan_id(self) -> str:
        with self._lock:
            self._n += 1
            return f"P-{self._n}"

    def save(self, plan: Plan) -> Plan:
        """static_baseline plans become ``baseline``; everything else becomes ``current``."""
        with self._lock:
            if plan.mode == "static_baseline":
                self.baseline = plan
            else:
                self.current = plan
            self.history.append(plan)
            listeners = list(self._listeners)
        for fn in listeners:
            fn(plan)
        return plan

    def add_listener(self, fn: Callable[[Plan], None]) -> None:
        self._listeners.append(fn)

    def reset(self) -> None:
        """Forget plans and restart ids (call on POST /scenario/reset and between tests)."""
        with self._lock:
            self._n = 0
            self.current = None
            self.baseline = None
            self.history = []
            self.last_replan_seconds = None
            self.world_events = []
            self.greedy_cache = {}


STORE = PlanStore()
