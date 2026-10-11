"""derive_tasks: turn component state into maintenance Tasks with HARD deadlines.

RUL tasks     component with ``rul_cycles.p10 < threshold`` (default 60). The deadline is the time the aircraft will
              have flown ``floor(p10)`` more cycles (1 flight = 1 cycle): we walk its scheduled legs and take that leg's
              scheduled arrival; beyond the known rotation we extrapolate with the aircraft's own average legs/day.
              The CONSERVATIVE quantile p10 is used, never p50, and we round the cycle count DOWN (safety rule,
              CONTRACTS.md section 1). If p10 < 1 cycle the aircraft may not fly again, so the deadline is its next departure.
Scheduled     routine cards (task_cards.yaml, ``trigger: scheduled``) whose due date
              ``last_maintenance + interval_days`` falls inside the next ``scheduled_horizon_days``. A few at most
              (``max_scheduled``) so the fleet has background work without drowning the demo. Overdue items are not
              planned automatically; they are reported in ``notes`` for a human decision.

``derive_tasks_ex`` also returns ``notes`` (components it could not turn into a task and why), so nothing is skipped silently.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from app.contracts.models import ComponentState, FleetState, Leg, Task
from app.solver.cards import TaskCard, load_cards, rul_card_for, scheduled_cards_for
from app.solver.skills import with_level
from app.solver.common import DEFAULT_LEGS_PER_DAY, RUL_THRESHOLD_CYCLES, aware, fmt

log = logging.getLogger(__name__)


@dataclass
class DeriveResult:
    tasks: list[Task]
    notes: list[str] = field(default_factory=list)


def _short(component_id: str) -> str:
    return component_id.split("/", 1)[-1]


def _future_legs(legs: list[Leg], tail: str, now: datetime) -> list[Leg]:
    """Legs of this aircraft that have not arrived yet (an in-progress leg still adds a cycle)."""
    mine = [l for l in legs if l.tail == tail and aware(l.sched_arr) > now]
    return sorted(mine, key=lambda l: (aware(l.sched_dep), l.id))


def cycle_deadline(future: list[Leg], cycles: int, now: datetime) -> tuple[datetime, str]:
    """Time by which the aircraft will have flown ``cycles`` more cycles, plus a note on how it was found."""
    now = aware(now)
    if cycles <= 0:
        if future:
            return max(now, aware(future[0].sched_dep)), "no cycles left: must be done before the next departure"
        return now, "no cycles left and no known departure: deadline is now"
    if cycles <= len(future):
        return aware(future[cycles - 1].sched_arr), f"arrival of its {cycles}{_ord(cycles)} scheduled flight"
    last = aware(future[-1].sched_arr) if future else now
    span_days = (last - now).total_seconds() / 86400.0
    if len(future) >= 2 and span_days >= 0.5:
        rate, how = len(future) / span_days, "its own average legs/day"
    else:
        rate, how = DEFAULT_LEGS_PER_DAY, "the default legs/day (known rotation too short)"
    extra = cycles - len(future)
    return last + timedelta(days=extra / rate), f"beyond the known rotation, extrapolated at {rate:.1f} legs/day ({how})"


def _ord(n: int) -> str:
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def _rul_task(comp: ComponentState, card: TaskCard, state: FleetState, now: datetime, seq: int) -> Optional[Task]:
    p10 = comp.rul_cycles.p10  # type: ignore[union-attr]  (checked by caller)
    future = _future_legs(state.legs, comp.tail, now)
    n = int(p10 + 1e-9)  # floor: fly at most this many more cycles
    deadline, how = cycle_deadline(future, n, now)
    days = (deadline - now).total_seconds() / 86400.0
    basis = (f"{_short(comp.component_id)} RUL p10 = {p10:g} cycles = about {days:.1f} days at this aircraft's rotation "
             f"({how}); deadline {fmt(deadline)} (conservative p10, hard constraint)")
    return Task(
        id=f"T-{comp.tail}-{_short(comp.component_id)}-{seq:03d}",
        tail=comp.tail, component_id=comp.component_id, title=card.title,
        duration_h=card.duration_h, earliest=now, deadline=deadline,
        required_skill=with_level(card.skill, card.min_level), required_part=card.part,
        part_qty=card.part_qty if card.part else 1,
        source="rul", basis=basis, synthetic=True,
    )


def derive_tasks_ex(
    state: FleetState,
    rul_threshold: float = RUL_THRESHOLD_CYCLES,
    scheduled_horizon_days: float = 5.0,
    max_scheduled: int = 3,
    cards: Optional[list[TaskCard]] = None,
) -> DeriveResult:
    now = aware(state.now)
    cards = cards or load_cards()
    notes: list[str] = []
    rul_tasks: list[Task] = []
    sched: list[tuple[datetime, str, str, TaskCard, ComponentState]] = []

    for comp in sorted(state.components, key=lambda c: (c.tail, c.component_id)):
        if comp.status == "in_maintenance":
            notes.append(f"{comp.component_id}: already in maintenance; no task derived")
            continue
        if comp.rul_cycles is None:
            if comp.status in ("watch", "alert"):
                notes.append(f"{comp.component_id}: status {comp.status} but no RUL estimate "
                             f"(data quality {comp.data_quality.status}); no deadline can be derived, needs human review")
            continue
        if comp.rul_cycles.p10 < rul_threshold:
            card = rul_card_for(comp.kind, cards)
            if card is None:
                notes.append(f"{comp.component_id}: no task card for component kind {comp.kind}")
                continue
            rul_tasks.append(_rul_task(comp, card, state, now, seq=1))

    # routine checks, from the component's own last maintenance date
    horizon_end = now + timedelta(days=scheduled_horizon_days)
    for comp in state.components:
        if comp.last_maintenance is None or comp.status == "in_maintenance":
            continue
        for card in scheduled_cards_for(comp.kind, cards):
            due = aware(comp.last_maintenance) + timedelta(days=card.interval_days or 0)
            if due < now:
                notes.append(f"{comp.component_id}: {card.title} was due {fmt(due)} (overdue); "
                             f"not planned automatically, needs a human decision")
            elif due <= horizon_end:
                sched.append((due, comp.tail, comp.component_id, card, comp))
    sched.sort(key=lambda t: (t[0], t[1], t[2], t[3].id))
    scheduled_tasks: list[Task] = []
    for due, tail, cid, card, comp in sched[:max_scheduled]:
        idx = scheduled_cards_for(comp.kind, cards).index(card)
        days = (due - now).total_seconds() / 86400.0
        scheduled_tasks.append(Task(
            id=f"T-{tail}-{_short(cid)}-{2 + idx:03d}",
            tail=tail, component_id=cid, title=card.title, duration_h=card.duration_h,
            earliest=now, deadline=due, required_skill=with_level(card.skill, card.min_level), required_part=card.part,
            part_qty=card.part_qty if card.part else 1, source="scheduled",
            basis=(f"{card.title} every {card.interval_days:g} days after the last maintenance "
                   f"({fmt(aware(comp.last_maintenance))}): due {fmt(due)}, in about {days:.1f} days (synthetic interval)"),
            synthetic=True,
        ))
    if len(sched) > max_scheduled:
        notes.append(f"{len(sched) - max_scheduled} further scheduled checks fall inside the horizon but "
                     f"only {max_scheduled} are planned (max_scheduled)")

    tasks = sorted(rul_tasks + scheduled_tasks, key=lambda t: (aware(t.deadline), t.id))
    ids = [t.id for t in tasks]
    assert len(ids) == len(set(ids)), "duplicate task ids"
    return DeriveResult(tasks=tasks, notes=notes)


def derive_tasks(state: FleetState) -> list[Task]:
    """Contract entry point: ``derive_tasks(state) -> list[Task]``. Notes are logged; use derive_tasks_ex to read them."""
    res = derive_tasks_ex(state)
    for n in res.notes:
        log.info("derive_tasks: %s", n)
    return res.tasks
