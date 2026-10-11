"""Fleet-aware maintenance scheduling (feature 3.3). Owner: Pardhu.

Contract entry points (CONTRACTS.md section 5):
    ground_windows(legs, now) -> list[GroundWindow]
    derive_tasks(state)       -> list[Task]
    solve(state, tasks, weights, mode, time_limit_s=5.0, previous=None) -> Plan
"""
from app.solver.engine import solve
from app.solver.tasks import derive_tasks
from app.solver.windows import ground_windows

__all__ = ["ground_windows", "derive_tasks", "solve"]
