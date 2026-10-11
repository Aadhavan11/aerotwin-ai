"""Shared helpers for the scheduler and what-if code: UTC handling, integer-minute time, defaults.

Everything is UTC. CP-SAT only sees integer minutes since ``state.now``; datetimes are converted at the edges.
"""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone

UTC = timezone.utc

SEED = int(os.getenv("SEED", "1234"))

# --- tunables (all synthetic demo values) ---
DEFAULT_MAX_EXT_H = 3.0          # max ground-time extension per job; beyond this a slot is not allowed
DEFAULT_HORIZON_H = 120.0        # planning horizon for utilisation metrics (5 days)
DEFAULT_LEGS_PER_DAY = 4.0       # used only when an aircraft's known rotation is too short to estimate a rate
RUL_THRESHOLD_CYCLES = 60.0      # components with RUL p10 below this get a task
UNSCHEDULED_PENALTY = 10_000.0   # added to objective_value per unscheduled task (so infeasible plans rank worse)


def aware(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC (the contract says UTC everywhere); never mix naive and aware."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def minutes_between(later: datetime, earlier: datetime) -> float:
    return (aware(later) - aware(earlier)).total_seconds() / 60.0


def floor_min(dt: datetime, now: datetime) -> int:
    """Minutes since ``now``, rounded DOWN (use for deadlines and ends: never rounds a bound later)."""
    return math.floor(minutes_between(dt, now) + 1e-9)


def ceil_min(dt: datetime, now: datetime) -> int:
    """Minutes since ``now``, rounded UP (use for starts)."""
    return math.ceil(minutes_between(dt, now) - 1e-9)


def hours_to_min(hours: float) -> int:
    """Duration in whole minutes, rounded UP so a job is never planned shorter than it is."""
    return math.ceil(hours * 60.0 - 1e-9)


def at_min(now: datetime, minutes: int) -> datetime:
    return aware(now) + timedelta(minutes=minutes)


def fmt(dt: datetime) -> str:
    return aware(dt).strftime("%b %d %H:%MZ")
