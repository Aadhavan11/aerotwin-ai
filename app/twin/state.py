"""Digital twin state engine (feature 3.2 backend).

Keeps every aircraft's components up to date from telemetry and scenario
events, and serves the views other modules read:

    ingest_telemetry(row)          -> ComponentState     (stores, scores, updates history + status)
    apply_event(event)             -> list[TwinEvent]    (scenario events land on the timeline)
    apply_maintenance(tail, cid, at) -> ComponentState   (a completed work order resets health)
    get_twin(tail)                 -> TwinView | None    (GET /aircraft/{tail})
    snapshot()                     -> FleetState         (for the scheduler and what-if)
    status_from(...)               -> AircraftStatus     (the one place statuses are decided)

State is held in memory (the API runs one process). Persisting to Shamit's
SQLite tables is a thin layer to add once ``app/db.py`` exists; nothing here
depends on it.

Time: every timestamp comes from the telemetry rows or from ``event.at``,
never from the wall clock. ``snapshot().now`` comes from ``set_now_provider``
(the SimClock) or, failing that, the newest row seen.

Units: cycles = flights. ``HOURS_PER_CYCLE`` is an ASSUMPTION used only to
fill ``ComponentState.hours`` from cycles.
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from threading import RLock
from typing import Callable, Deque, Iterable, Optional

from app.contracts.models import (
    Aircraft, AircraftSummary, ComponentState, DataQuality, FleetState, HealthScore, HistoryPoint, Leg,
    PredictedPoint, ScenarioEvent, Station, TelemetryRow, TwinComponent, TwinEvent, TwinView,
)
from app.ml import health as ml_health
from app.ml.sensors import kind_from_component_id

log = logging.getLogger(__name__)

UTC = timezone.utc
SIM_START = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)   # SimClock start (CONTRACTS / work/01)
HOURS_PER_CYCLE = 2.0                                   # assumption: block hours per flight; equals the simulator's 2 h legs
WINDOW = 30
HISTORY_MAX = 600
TELEMETRY_MAX = 400
PREDICT_MAX_POINTS = 60

_RANK = {"ok": 0, "watch": 1, "alert": 2}
_LOCK = RLock()


# --------------------------------------------------------------------------
# Status rule: the single place it lives (INTEGRATION.md section 4.1)
# --------------------------------------------------------------------------
def status_from(health: float, is_anomaly: bool, rul_p10: Optional[float]) -> str:
    """``alert`` if anomalous or p10 < 30 cycles; ``watch`` if p10 < 60 or health < 0.5; else ``ok``.

    ``in_maintenance`` and ``aog`` are never returned here: they are set only by
    plan execution and scenario events (see ``set_aircraft_status``).
    """
    if is_anomaly or (rul_p10 is not None and rul_p10 < 30):
        return "alert"
    if (rul_p10 is not None and rul_p10 < 60) or health < 0.5:
        return "watch"
    return "ok"


# --------------------------------------------------------------------------
# Internal records
# --------------------------------------------------------------------------
class _Comp:
    __slots__ = ("state", "window", "telemetry", "history", "sensors_last", "score", "cycle0")

    def __init__(self, state: ComponentState):
        self.state = state
        self.window: Deque[TelemetryRow] = deque(maxlen=WINDOW)
        self.telemetry: Deque[TelemetryRow] = deque(maxlen=TELEMETRY_MAX)
        self.history: list[HistoryPoint] = []
        self.sensors_last: dict[str, float] = {}
        self.score: Optional[HealthScore] = None
        # Cycle counter just before the first row of the current series. The RUL model was trained on engine age
        # (cycles since the start of a run), not on the airframe's lifetime counter, so it is fed ``row.cycle - cycle0``.
        self.cycle0: Optional[int] = None


_comps: dict[str, _Comp] = {}
_aircraft: dict[str, Aircraft] = {}
_timeline: dict[str, list[TwinEvent]] = {}
_forced_status: dict[str, str] = {}
_newest_ts: Optional[datetime] = None
_now_provider: Optional[Callable[[], datetime]] = None
_ops_provider: Optional[Callable[[], tuple[list[Leg], list[Station]]]] = None


def reset() -> None:
    """Forget everything (used by POST /scenario/reset and tests)."""
    global _newest_ts
    with _LOCK:
        _comps.clear()
        _aircraft.clear()
        _timeline.clear()
        _forced_status.clear()
        _newest_ts = None


def set_now_provider(fn: Optional[Callable[[], datetime]]) -> None:
    """Wire the SimClock: ``set_now_provider(clock.now)``."""
    global _now_provider
    _now_provider = fn


def set_ops_provider(fn: Optional[Callable[[], tuple[list[Leg], list[Station]]]]) -> None:
    """Where ``snapshot()`` gets (legs, stations): the fleet generator / DB."""
    global _ops_provider
    _ops_provider = fn


# --------------------------------------------------------------------------
# Registration and seeding
# --------------------------------------------------------------------------
def register_aircraft(aircraft: Aircraft) -> None:
    with _LOCK:
        _aircraft[aircraft.tail] = aircraft
        _timeline.setdefault(aircraft.tail, [])


def seed_components(states: Iterable[ComponentState]) -> None:
    """Load component states directly (fixtures, or a generator that has no telemetry yet)."""
    with _LOCK:
        for s in states:
            _comps[s.component_id] = _Comp(s)
            if s.tail not in _aircraft:
                _aircraft[s.tail] = _stub_aircraft(s.tail)
                _timeline.setdefault(s.tail, [])


def update_component_operations(component_id: str, cycles: int, hours: float) -> None:
    """Simulator supplies observed cycle/block-hour counters without clearing health history."""
    with _LOCK:
        comp = _comps.get(component_id)
        if comp is not None:
            comp.state.cycles = cycles
            comp.state.hours = hours


def _stub_aircraft(tail: str) -> Aircraft:
    log.warning("telemetry for unregistered aircraft %s: creating a stub record", tail)
    return Aircraft(tail=tail, type="unknown", home="---", location="---", status="ok", cycles=0, hours=0.0)


def _short(component_id: str) -> str:
    return component_id.split("/")[-1]


def _emit(tail: str, ev: TwinEvent) -> None:
    _timeline.setdefault(tail, []).append(ev)


def add_timeline_event(tail: str, event: TwinEvent) -> None:
    """For other modules: flights, plan changes, work-order milestones."""
    with _LOCK:
        _emit(tail, event)


# --------------------------------------------------------------------------
# Telemetry
# --------------------------------------------------------------------------
def _new_state(row: TelemetryRow, kind: str) -> ComponentState:
    return ComponentState(
        tail=row.tail, component_id=row.component_id, kind=kind, cycles=row.cycle, hours=0.0, health=1.0,
        anomaly_score=0.0, rul_cycles=None, data_quality=DataQuality(status="ok"), last_maintenance=None,
        status="ok", updated=row.ts,
    )


def ingest_telemetry(row: TelemetryRow, *, backfill: bool = False) -> ComponentState:
    """Store one row, re-score the component from its last 30 rows, update state.

    ``backfill=True`` is for priming a component with history before the
    simulation starts: state and history update, but no timeline events fire.
    """
    global _newest_ts
    with _LOCK:
        kind = kind_from_component_id(row.component_id)
        comp = _comps.get(row.component_id)
        if comp is None:
            comp = _Comp(_new_state(row, kind))
            _comps[row.component_id] = comp
        if row.tail not in _aircraft:
            _aircraft[row.tail] = _stub_aircraft(row.tail)
            _timeline.setdefault(row.tail, [])

        comp.window.append(row)
        comp.telemetry.append(row)
        comp.sensors_last = {k: float(v) for k, v in row.values.items()}
        if _newest_ts is None or row.ts > _newest_ts:
            _newest_ts = row.ts

        if comp.cycle0 is None:
            comp.cycle0 = row.cycle - 1
        rebased = [r.model_copy(update={"cycle": r.cycle - comp.cycle0}) for r in comp.window] if comp.cycle0 else list(comp.window)
        score = ml_health.score_component(kind, row.tail, row.component_id, rebased)
        comp.score = score
        prev = comp.state
        prev_status = prev.status

        if score.data_quality.status == "bad":
            # Cannot score: keep the last good numbers, flag the quality, do not change the status.
            new = prev.model_copy(update={
                "cycles": row.cycle, "hours": round(row.cycle * HOURS_PER_CYCLE, 1),
                "data_quality": score.data_quality, "updated": row.ts,
            })
        else:
            new = prev.model_copy(update={
                "cycles": row.cycle, "hours": round(row.cycle * HOURS_PER_CYCLE, 1),
                "health": score.health, "anomaly_score": score.anomaly_score, "rul_cycles": score.rul_cycles,
                "data_quality": score.data_quality, "updated": row.ts,
                "status": status_from(score.health, score.is_anomaly, score.rul_cycles.p10 if score.rul_cycles else None),
            })
            comp.history.append(HistoryPoint(
                cycle=row.cycle, ts=row.ts, health=score.health, anomaly_score=score.anomaly_score,
                rul_p50=score.rul_cycles.p50 if score.rul_cycles else None,
            ))
            if len(comp.history) > HISTORY_MAX:
                del comp.history[: len(comp.history) - HISTORY_MAX]
        comp.state = new

        if not backfill and new.status != prev_status and prev_status in _RANK and new.status in _RANK:
            _emit(row.tail, _status_event(row.ts, new, prev_status, score))
        return new


def ingest_many(rows: Iterable[TelemetryRow], *, backfill: bool = False) -> int:
    n = 0
    for r in rows:
        ingest_telemetry(r, backfill=backfill)
        n += 1
    return n


def _status_event(ts: datetime, new: ComponentState, prev_status: str, score: HealthScore) -> TwinEvent:
    name = _short(new.component_id)
    worse = _RANK[new.status] > _RANK[prev_status]
    sig = ", ".join(score.top_signals[:2])
    rul = f"RUL p50 {score.rul_cycles.p50:g} cycles" if score.rul_cycles else "RUL unknown"
    if worse:
        text = f"{name} {prev_status} -> {new.status}: abnormal {sig} trend ({rul})" if sig else f"{name} {prev_status} -> {new.status} ({rul})"
    else:
        text = f"{name} {prev_status} -> {new.status} ({rul})"
    return TwinEvent(ts=ts, kind="anomaly", summary=text, ref=new.component_id)


# --------------------------------------------------------------------------
# Events and maintenance
# --------------------------------------------------------------------------
def apply_event(event: ScenarioEvent) -> list[TwinEvent]:
    """Record a scenario event on the twin timeline.

    For ``anomaly`` and ``accelerated_degradation`` the telemetry source moves
    the engine's data and then calls ``ingest_telemetry``; this adds the
    ``fault_injected`` entry and re-scores the component from its current
    window. Station events carry no tail and do not touch an aircraft here.
    """
    out: list[TwinEvent] = []
    with _LOCK:
        if event.type in ("anomaly", "accelerated_degradation") and event.tail:
            target = _short(event.component_id) if event.component_id else "component"
            sev = f" (severity {event.severity:g})" if event.severity is not None else ""
            what = "engine anomaly" if event.type == "anomaly" else "accelerated degradation"
            ev = TwinEvent(ts=event.at, kind="fault_injected", summary=f"Injected {what} on {target}{sev}",
                           ref=event.id or None)
            _emit(event.tail, ev)
            out.append(ev)
            if event.component_id:
                _rescore(event.component_id, event.at)
        elif event.tail:
            ev = TwinEvent(ts=event.at, kind="plan_change", summary=f"{event.type.replace('_', ' ')}: {event.note or 'event recorded'}",
                           ref=event.id or None)
            _emit(event.tail, ev)
            out.append(ev)
    return out


def _rescore(component_id: str, at: datetime) -> None:
    comp = _comps.get(component_id)
    if comp is None or not comp.window:
        return
    score = ml_health.score_component(comp.state.kind, comp.state.tail, component_id, list(comp.window))
    if score.data_quality.status == "bad":
        return
    comp.score = score
    comp.state = comp.state.model_copy(update={
        "health": score.health, "anomaly_score": score.anomaly_score, "rul_cycles": score.rul_cycles,
        "data_quality": score.data_quality, "updated": at,
        "status": status_from(score.health, score.is_anomaly, score.rul_cycles.p10 if score.rul_cycles else None),
    })


def apply_maintenance(tail: str, component_id: str, at: datetime) -> ComponentState:
    """A completed work order: health back to new, cycles since maintenance 0, timeline entry.

    The telemetry source must restart the component's series (cycle 1, fresh
    unit) or the old degradation keeps arriving. The scoring window is cleared.
    """
    with _LOCK:
        comp = _comps.get(component_id)
        if comp is None:
            kind = kind_from_component_id(component_id)
            comp = _Comp(ComponentState(
                tail=tail, component_id=component_id, kind=kind, cycles=0, hours=0.0, health=1.0, anomaly_score=0.0,
                rul_cycles=None, data_quality=DataQuality(status="ok"), last_maintenance=at, status="ok", updated=at))
            _comps[component_id] = comp
            _aircraft.setdefault(tail, _stub_aircraft(tail))
        prev_status = comp.state.status
        comp.window.clear()
        comp.telemetry.clear()
        comp.history.clear()
        comp.sensors_last = {}
        comp.score = None
        comp.cycle0 = None
        comp.state = comp.state.model_copy(update={
            "health": 1.0, "anomaly_score": 0.0, "cycles": 0, "hours": 0.0, "rul_cycles": None,
            "data_quality": DataQuality(status="ok"), "last_maintenance": at, "status": "ok", "updated": at,
        })
        _emit(tail, TwinEvent(ts=at, kind="maintenance",
                              summary=f"{_short(component_id)} maintenance completed (was {prev_status}); health reset",
                              ref=component_id))
        return comp.state


def set_aircraft_status(tail: str, status: Optional[str]) -> None:
    """Plan execution only: mark an aircraft ``in_maintenance`` or ``aog``. ``None`` clears it."""
    with _LOCK:
        if status is None:
            _forced_status.pop(tail, None)
        else:
            _forced_status[tail] = status


# --------------------------------------------------------------------------
# Views
# --------------------------------------------------------------------------
def _aircraft_status(tail: str) -> str:
    if tail in _forced_status:
        return _forced_status[tail]
    worst = "ok"
    for c in _comps.values():
        if c.state.tail == tail and _RANK.get(c.state.status, 0) > _RANK[worst]:
            worst = c.state.status
    return worst


def _aircraft_view(tail: str) -> Optional[Aircraft]:
    a = _aircraft.get(tail)
    return a.model_copy(update={"status": _aircraft_status(tail)}) if a else None


def _predicted(state: ComponentState) -> list[PredictedPoint]:
    """Health falling to about 0 at RUL p50, band from p10 (lower) to p90 (upper)."""
    r = state.rul_cycles
    if r is None or state.health <= 0:
        return []
    n = int(min(r.p90 + 5, PREDICT_MAX_POINTS))
    h0, c0 = state.health, state.cycles
    eps = 1e-6

    def at(k: int, rul: float) -> float:
        return round(h0 * max(0.0, 1.0 - k / max(rul, eps)), 4)

    return [
        PredictedPoint(cycle=c0 + k, health_p50=at(k, r.p50), health_lo=at(k, r.p10), health_hi=at(k, r.p90))
        for k in range(0, n + 1)
    ]


def _twin_component(c: _Comp) -> TwinComponent:
    return TwinComponent(**c.state.model_dump(), history=list(c.history), predicted=_predicted(c.state),
                         sensors_last=dict(c.sensors_last))


def get_twin(tail: str) -> Optional[TwinView]:
    """The full twin of one aircraft, or None if unknown. Timeline is newest first."""
    with _LOCK:
        a = _aircraft_view(tail)
        if a is None:
            return None
        comps = sorted((c for c in _comps.values() if c.state.tail == tail), key=lambda c: c.state.component_id)
        timeline = sorted(_timeline.get(tail, []), key=lambda e: e.ts, reverse=True)
        return TwinView(aircraft=a, components=[_twin_component(c) for c in comps], timeline=timeline)


def get_telemetry(tail: str, component_id: Optional[str] = None, last: int = 60) -> list[TelemetryRow]:
    with _LOCK:
        rows: list[TelemetryRow] = []
        for cid, c in _comps.items():
            if c.state.tail == tail and (component_id is None or cid == component_id or _short(cid) == component_id):
                rows.extend(c.telemetry)
        rows.sort(key=lambda r: (r.component_id, r.cycle))
        if component_id is not None:
            return rows[-last:]
        return rows[-last * 6:] if last else rows


def top_signals(component_id: str) -> list[str]:
    """The sensors that drove the latest score of a component (explainability)."""
    with _LOCK:
        c = _comps.get(component_id)
        return list(c.score.top_signals) if c is not None and c.score is not None else []


def list_aircraft() -> list[Aircraft]:
    with _LOCK:
        return [_aircraft_view(t) for t in sorted(_aircraft)]  # type: ignore[misc]


_SEVERITY = {"aog": 0, "alert": 1, "watch": 2, "in_maintenance": 3, "ok": 4}


def aircraft_summaries() -> list[AircraftSummary]:
    """Per aircraft: status and the component with the lowest RUL p10 (for the fleet overview)."""
    with _LOCK:
        out: list[AircraftSummary] = []
        for tail in sorted(_aircraft):
            a = _aircraft_view(tail)
            assert a is not None
            # Worst status first (alert before watch before ok), then the shortest conservative remaining life. Two engines can tie on life
            # (the estimate is capped), and the one in alert must win the tie.
            chosen, chosen_key = None, None
            for c in _comps.values():
                if c.state.tail != tail:
                    continue
                p10 = c.state.rul_cycles.p10 if c.state.rul_cycles is not None else float("inf")
                key = (_SEVERITY.get(c.state.status, 9), p10)
                if chosen_key is None or key < chosen_key:
                    chosen, chosen_key = c, key
            worst = chosen.state.component_id if chosen is not None else None
            best = chosen.state.rul_cycles.p10 if chosen is not None and chosen.state.rul_cycles is not None else None
            out.append(AircraftSummary(tail=a.tail, type=a.type, home=a.home, location=a.location, status=a.status,
                                       worst_component=worst, rul_p10_min=best))
        return out


def now() -> datetime:
    if _now_provider is not None:
        return _now_provider()
    return _newest_ts or SIM_START


def is_empty() -> bool:
    with _LOCK:
        return not _aircraft and not _comps


def snapshot() -> FleetState:
    """Immutable view for the scheduler and what-if.

    Aircraft and components come from the twin. Legs and stations come from
    the ops provider (fleet generator / DB). An empty twin returns an empty
    state: it never substitutes another fleet, so a plan can always be traced
    to what the twin really holds.
    """
    with _LOCK:
        legs: list[Leg] = []
        stations: list[Station] = []
        if _ops_provider is not None:
            legs, stations = _ops_provider()
        return FleetState(
            now=now(),
            aircraft=[_aircraft_view(t) for t in sorted(_aircraft)],  # type: ignore[misc]
            components=[c.state.model_copy(deep=True) for _, c in sorted(_comps.items())],
            legs=legs, stations=stations,
        )


def stand_in_state() -> Optional[FleetState]:
    """Explicit opt-in: the scheduler's golden stand-in fleet (Pardhu's ``app.solver.golden``), or None.

    For scripts and demos only; ``snapshot()`` never calls this.
    """
    try:
        from app.solver.golden import golden_state  # type: ignore[import-not-found]
    except Exception:
        return None
    return golden_state()
