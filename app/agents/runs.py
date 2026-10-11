"""In-memory store of agent runs. Steps are appended as they happen so ``GET /agents/runs/{id}`` shows progress.

``RUNS.add_listener(fn)`` lets the platform persist every change (``fn(run)``) without this module importing the database.
"""
from __future__ import annotations

import threading
from typing import Callable, Optional

from app.contracts.models import AgentRun, AgentStep, WorkOrder


class RunStore:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._runs: dict[str, AgentRun] = {}
        self._n = 0
        self._wo = 0
        self._listeners: list[Callable[[AgentRun], None]] = []

    def create(self, goal: str, mode: str = "scripted") -> AgentRun:
        with self._lock:
            self._n += 1
            run = AgentRun(id=f"R-{self._n}", goal=goal, mode=mode, status="running", steps=[])  # type: ignore[arg-type]
            self._runs[run.id] = run
            return self._emit(run)

    def next_work_order_id(self) -> str:
        with self._lock:
            self._wo += 1
            return f"WO-{self._wo}"

    def append(self, run_id: str, step: AgentStep) -> None:
        with self._lock:
            self._runs[run_id].steps.append(step)
            self._emit(self._runs[run_id])

    def update(self, run_id: str, **fields) -> AgentRun:
        with self._lock:
            run = self._runs[run_id]
            for k, v in fields.items():
                setattr(run, k, v)
            return self._emit(run)

    def get(self, run_id: str) -> Optional[AgentRun]:
        with self._lock:
            run = self._runs.get(run_id)
            return run.model_copy(deep=True) if run else None

    def add_work_orders(self, run_id: str, orders: list[WorkOrder]) -> None:
        with self._lock:
            self._runs[run_id].work_orders = list(orders)
            self._emit(self._runs[run_id])

    def add_listener(self, fn: Callable[[AgentRun], None]) -> None:
        self._listeners.append(fn)

    def reset(self) -> None:
        with self._lock:
            self._runs, self._n, self._wo = {}, 0, 0

    def _emit(self, run: AgentRun) -> AgentRun:
        for fn in list(self._listeners):
            fn(run.model_copy(deep=True))
        return run


RUNS = RunStore()
