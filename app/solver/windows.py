"""Ground windows: the time an aircraft sits at an airport between an arrival and its next departure.

``ground_windows(legs, now)`` is the contract signature. ``aircraft`` is an optional extra: with it we also add the
window an aircraft has right now (parked at the station of its next departure), which the leg list alone cannot show.
Rotation breaks (an arrival at X followed by a departure from Y) give no window; they are reported as data issues.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional

from app.contracts.models import Aircraft, GroundWindow, Leg
from app.solver.common import aware

log = logging.getLogger(__name__)


def ground_windows_checked(
    legs: list[Leg], now: datetime, aircraft: Optional[list[Aircraft]] = None
) -> tuple[list[GroundWindow], list[str]]:
    """Return (windows, data_issues). Windows that end at or before ``now`` are dropped; one that
    straddles ``now`` is clipped to start at ``now``."""
    now = aware(now)
    by_tail: dict[str, list[Leg]] = defaultdict(list)
    for leg in legs:
        by_tail[leg.tail].append(leg)
    location = {a.tail: a.location for a in (aircraft or [])}

    windows: list[GroundWindow] = []
    issues: list[str] = []
    for tail in sorted(by_tail):
        seq = sorted(by_tail[tail], key=lambda l: (aware(l.sched_dep), l.id))
        for a, b in zip(seq, seq[1:]):
            if a.arr != b.dep:
                issues.append(f"{tail}: leg {a.id} arrives {a.arr} but next leg {b.id} departs {b.dep}; no ground window")
                continue
            start, end = aware(a.sched_arr), aware(b.sched_dep)
            if end < start:
                issues.append(f"{tail}: leg {b.id} departs before leg {a.id} arrives; no ground window")
                continue
            if end <= now:
                continue
            start = max(start, now)
            if end > start:
                windows.append(GroundWindow(tail=tail, station=a.arr, start=start, end=end, before_leg=b.id))

        # the window the aircraft has right now, before its first leg that has not yet arrived
        upcoming = [l for l in seq if aware(l.sched_arr) > now]
        if upcoming and tail in location:
            first = upcoming[0]
            already = any(w.tail == tail and w.before_leg == first.id for w in windows)
            if not already and aware(first.sched_dep) > now and location[tail] == first.dep:
                windows.append(GroundWindow(tail=tail, station=first.dep, start=now,
                                            end=aware(first.sched_dep), before_leg=first.id))

    windows.sort(key=lambda w: (w.tail, w.start, w.station))
    return windows, issues


def ground_windows(legs: list[Leg], now: datetime, aircraft: Optional[list[Aircraft]] = None) -> list[GroundWindow]:
    windows, issues = ground_windows_checked(legs, now, aircraft)
    for msg in issues:
        log.warning("rotation data issue: %s", msg)
    return windows
