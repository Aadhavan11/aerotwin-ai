"""apply_event_to_state: PURE application of a scenario event to a FleetState snapshot (feature 3.5).

Returns a modified deep copy; the input is never touched. ``apply_event`` also returns the human-readable effects, which
Shamit's ``POST /scenario/inject`` can show.

Event semantics
  part_delay            the part at (station, part) becomes available ``delay_hours`` later.
                        stock already in transit (eta_hours > 0): eta_hours += delay_hours.
                        stock on hand: it is unavailable until the event time + delay (eta_hours = hours from now to the event
                        + delay_hours); the units stay in the count. (CONTRACTS-era note: work/03 said "qty -> 0"; that
                        would delete the stock for good, a delay is not a loss.)
  facility_closed       hangar_slots = 0 at the station (no job may be planned there).
  tech_unavailable      that technician (technician_id) gets available = False.
  anomaly / accelerated_degradation
                        the component's RUL quantiles are multiplied by (1 - severity) (severity defaults to 0.5), so
                        ``derive_tasks`` creates an earlier deadline. If the event carries an id that is already recorded
                        in the component's data-quality reasons, it is not applied twice.

DOUBLE-APPLICATION WARNING: if the platform already wrote the event into the database (so ``snapshot()`` contains it),
call ``replan(..., already_applied=True)`` instead of applying it again.
"""
from __future__ import annotations

from app.contracts.models import ComponentState, DataQuality, FleetState, RulEstimate, ScenarioEvent
from app.solver.common import aware, minutes_between


class EventError(ValueError):
    """The event is malformed (missing station, part, delay...). ``replan`` turns this into an infeasible result."""


def _need(event: ScenarioEvent, *fields: str) -> None:
    missing = [f for f in fields if getattr(event, f) in (None, "")]
    if missing:
        raise EventError(f"{event.type} event needs {', '.join(missing)}")


def _status_from(p10: float, health: float) -> str:
    """Same thresholds as twin/state.py status_from (INTEGRATION.md 4.1) for the RUL-driven part."""
    if p10 < 30:
        return "alert"
    if p10 < 60 or health < 0.5:
        return "watch"
    return "ok"


def apply_event(state: FleetState, event: ScenarioEvent) -> tuple[FleetState, list[str]]:
    new = state.model_copy(deep=True)
    effects: list[str] = []
    now = aware(new.now)

    if event.type == "part_delay":
        _need(event, "station", "part", "delay_hours")
        st = next((s for s in new.stations if s.iata == event.station), None)
        if st is None:
            raise EventError(f"unknown station {event.station}")
        ps = next((p for p in st.parts if p.part == event.part), None)
        if ps is None:
            effects.append(f"{event.station} has no stock record for {event.part}; nothing to delay")
        else:
            before = ps.eta_hours
            if ps.eta_hours > 0:
                ps.eta_hours = ps.eta_hours + float(event.delay_hours)  # type: ignore[arg-type]
            else:
                lead = max(0.0, minutes_between(event.at, now) / 60.0)
                ps.eta_hours = lead + float(event.delay_hours)  # type: ignore[arg-type]
            effects.append(f"{event.station} {event.part}: available in {ps.eta_hours:g} h (was {before:g} h), "
                           f"{ps.qty} unit(s) held until then")

    elif event.type == "facility_closed":
        _need(event, "station")
        st = next((s for s in new.stations if s.iata == event.station), None)
        if st is None:
            raise EventError(f"unknown station {event.station}")
        effects.append(f"{event.station} hangar closed ({st.hangar_slots} slot(s) -> 0)")
        st.hangar_slots = 0

    elif event.type == "tech_unavailable":
        _need(event, "technician_id")
        found = [(s, t) for s in new.stations for t in s.technicians if t.id == event.technician_id]
        if not found:
            raise EventError(f"unknown technician {event.technician_id}")
        s, t = found[0]
        t.available = False
        effects.append(f"technician {t.id} ({s.iata}, {'/'.join(t.skills)}) unavailable")

    elif event.type in ("anomaly", "accelerated_degradation"):
        if not event.component_id and not event.tail:
            raise EventError(f"{event.type} event needs component_id (or tail)")
        comps = [c for c in new.components if c.component_id == event.component_id] if event.component_id else []
        if not comps:
            raise EventError(f"unknown component {event.component_id or event.tail}")
        comp = comps[0]
        effects.extend(_shorten_rul(comp, event))

    else:  # pragma: no cover - guarded by the EventType Literal
        raise EventError(f"unsupported event type {event.type}")
    return new, effects


def _shorten_rul(comp: ComponentState, event: ScenarioEvent) -> list[str]:
    marker = f"event {event.id}: RUL scaled" if event.id else None
    if marker and any(r.startswith(marker) for r in comp.data_quality.reasons):
        return [f"{comp.component_id}: event {event.id} already applied; unchanged"]
    if comp.rul_cycles is None:
        return [f"{comp.component_id} has no RUL estimate; cannot shorten it (needs human review)"]
    severity = 0.5 if event.severity is None else min(max(float(event.severity), 0.0), 1.0)
    f = 1.0 - severity
    old = comp.rul_cycles
    comp.rul_cycles = RulEstimate(p10=round(old.p10 * f, 2), p50=round(old.p50 * f, 2), p90=round(old.p90 * f, 2))
    comp.anomaly_score = max(comp.anomaly_score, min(1.0, 0.5 + severity / 2))
    comp.health = min(comp.health, max(0.0, 1.0 - severity))
    comp.status = _status_from(comp.rul_cycles.p10, comp.health)  # type: ignore[assignment]
    if marker:
        comp.data_quality = DataQuality(status=comp.data_quality.status,
                                        reasons=list(comp.data_quality.reasons) + [f"{marker} by {f:.2f}"])
    return [f"{comp.component_id}: RUL p10 {old.p10:g} -> {comp.rul_cycles.p10:g} cycles (severity {severity:g}), status {comp.status}"]


def apply_event_to_state(state: FleetState, event: ScenarioEvent) -> FleetState:
    """Contract entry point. Raises EventError for a malformed event; ``replan`` catches it."""
    return apply_event(state, event)[0]


def describe_event(event: ScenarioEvent) -> str:
    """One line for the UI / diff reasons."""
    t = event.type
    if t == "part_delay":
        return f"{event.part} at {event.station} delayed {event.delay_hours:g} h"
    if t == "facility_closed":
        return f"{event.station} hangar closed"
    if t == "tech_unavailable":
        return f"technician {event.technician_id} unavailable"
    return f"{t} on {event.component_id or event.tail}" + (f" (severity {event.severity:g})" if event.severity is not None else "")
