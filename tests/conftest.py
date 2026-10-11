"""Shared fixtures. Every test starts with an empty plan store so plan ids and the current plan never leak between tests."""
from __future__ import annotations

import os

import pytest

# tests never depend on a real twin or database; the golden stand-in is used, and the real CP-SAT solver (no mock flags)
os.environ.pop("MOCK", None)
os.environ.pop("MOCK_SOLVER", None)

from app.solver.golden import golden_state  # noqa: E402
from app.solver.store import STORE  # noqa: E402
from app.solver.tasks import derive_tasks  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_store():
    STORE.reset()
    yield
    STORE.reset()


@pytest.fixture(scope="session")
def golden():
    """The golden FleetState. Treat as read-only: tests that change it use apply_event_to_state or model_copy(deep=True)."""
    return golden_state()


@pytest.fixture(scope="session")
def golden_tasks(golden):
    return derive_tasks(golden)
