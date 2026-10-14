"""Ops-to-solver links: LLP limits and MEL deferrals as solver tasks, and the completion hooks that close the loop.

SYNTHETIC / SIMULATED. Behind ``settings.LLP_TASKS`` / ``settings.MEL_TASKS`` (default OFF) so the golden plan is untouched.
Everything from ``app.ops`` is imported lazily and every failure degrades to a note: the solver never breaks because the
optional ops package (or its database) is absent.

Task ids carry the back-reference (there is no extra field on ``Task``; ``source`` is ``"llp"`` or ``"mel"``):
    T-LLP-<tail>-<position with ':' -> '-'>    e.g. T-LLP-SIM-017-engine_1-hpt_disk, T-LLP-SIM-003-mlg_leg_l
    T-MEL-<mel id>                             e.g. T-MEL-0007 (the MEL register id, ``MEL-0007``, without its prefix)
Neither id matches ``workorders.component_of`` (it needs ``T-SIM-<n>-``), so completing one never restores a twin component.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional

from app.contracts.models import FleetState, Task
from app.solver.cards import TaskCard, rul_card_for
from app.solver.common import aware, fmt
from app.solver.skills import with_level

log = logging.getLogger(__name__)

_LLP_RE_ENGINE = re.compile(r"^T-LLP-(.+)-(engine_\d+)-([a-z0-9_]+)$")
_LLP_RE_AIRFRAME = re.compile(r"^T-LLP-(.+)-([a-z0-9_]+)$")
_MEL_RE = re.compile(r"^T-MEL-(.+)$")


@dataclass(frozen=True)
class LlpJob:
    """Synthetic job spec for replacing a life-limited part inside a ground window (a real shop visit is off-wing; here it is
    modelled as a module change so the solver can place it). Chosen to fit the longest overnight windows (max extension 3 h)."""
    title: str
    duration_h: float
    skill: str
    note: str


LLP_JOBS: dict[str, LlpJob] = {
    "engine": LlpJob("LLP module replacement (engine)", 8.0, "engine", "Synthetic: engine life-limited part change, modelled as an in-window module swap."),
    "aircraft": LlpJob("LLP replacement (landing gear)", 6.0, "gear", "Synthetic: gear life-limited part change, modelled as an in-window job."),
}

# ATA chapter (MEL item) -> component kind, whose RUL card gives skill / duration / part
_CHAPTER_KIND = {"21": "ecs", "22": "avionics", "24": "electrical", "29": "hydraulics", "31": "avionics", "32": "landing_gear",
                 "34": "avionics", "36": "ecs", "49": "apu", "72": "engine"}


def llp_task_id(tail: str, position: str) -> str:
    return f"T-LLP-{tail}-{position.replace(':', '-')}"


def mel_task_id(item_id: str) -> str:
    return f"T-MEL-{item_id.removeprefix('MEL-')}"


def parse_task_ref(task_id: str) -> Optional[dict[str, str]]:
    """``{'source': 'llp', 'tail', 'position'}`` | ``{'source': 'mel', 'id'}`` | None for any other task."""
    m = _MEL_RE.match(task_id)
    if m:
        return {"source": "mel", "id": f"MEL-{m.group(1)}"}
    m = _LLP_RE_ENGINE.match(task_id)
    if m:
        return {"source": "llp", "tail": m.group(1), "position": f"{m.group(2)}:{m.group(3)}"}
    m = _LLP_RE_AIRFRAME.match(task_id)
    if m and task_id.startswith("T-LLP-"):
        return {"source": "llp", "tail": m.group(1), "position": m.group(2)}
    return None


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


# ---------------------------------------------------------------- LLP
def llp_parts_for_state(state: FleetState, now: datetime) -> list[dict[str, Any]]:
    """LLP part states for the aircraft of ``state`` (counters from the state itself, not from the live twin)."""
    from app.ops import llp

    cpd = llp.cycles_per_day_by_tail(state.legs)
    eng: dict[str, dict[str, float]] = {}
    for c in state.components:
        if c.kind == "engine":
            eng.setdefault(c.tail, {})[c.component_id.split("/")[-1]] = float(c.cycles)
    out: list[dict[str, Any]] = []
    for a in sorted(state.aircraft, key=lambda x: x.tail):
        t = llp.TailInputs(a.tail, float(a.cycles), cpd.get(a.tail, llp.DEFAULT_CPD), eng.get(a.tail, {}), a.location)
        out.extend(llp.tail_parts(t, now))
    return out


def llp_tasks(state: FleetState, now: datetime, horizon_days: float, notes: list[str]) -> list[Task]:
    try:
        parts = llp_parts_for_state(state, now)
    except Exception as exc:  # app.ops absent or broken: advisory feature only
        notes.append(f"LLP tasks skipped: {type(exc).__name__}: {exc}")
        return []
    horizon_end = now + timedelta(days=horizon_days)
    comps = {c.component_id for c in state.components}
    tasks: list[Task] = []
    for p in sorted(parts, key=lambda x: (x["tail"], x["position"])):
        limit = aware(_parse_iso(p["limit_date"]))
        if not (p["status"] in ("alert", "expired") or (p["status"] == "watch" and limit <= horizon_end)):
            continue
        spec = LLP_JOBS["engine" if p["scope"] != "airframe" else "aircraft"]
        # A part already past its limit has no real deadline left (the aircraft should not fly): it is planned into the first
        # slot the horizon offers instead of an impossible "before now", and the basis says so.
        overdue = limit <= now
        deadline = horizon_end if overdue else limit
        cid = f"{p['tail']}/{p['scope']}" if p["scope"] != "airframe" else f"{p['tail']}/landing_gear"
        if cid not in comps:
            cid = f"{p['tail']}/{p['scope']}"
        late = f" (limit already reached: planned within the {horizon_days:g}-day horizon, to be done at the first slot)" if overdue else ""
        tasks.append(Task(
            id=llp_task_id(p["tail"], p["position"]), tail=p["tail"], component_id=cid,
            title=f"{spec.title}: {p['label']}", duration_h=spec.duration_h, earliest=now, deadline=deadline,
            required_skill=spec.skill, required_part=None, part_qty=1, source="llp", synthetic=True,
            basis=(f"LLP {p['label']} ({p['part_no']}, {p['position']}) status {p['status']}: {p['remaining_cycles']:g} cycles left "
                   f"of {p['cycle_limit']:g}, limit {fmt(limit)} ({p['limit_driver']}-driven){late}; SIMULATED priors"),
        ))
    return tasks


# ---------------------------------------------------------------- MEL
def mel_tasks(state: FleetState, now: datetime, cards: list[TaskCard], notes: list[str]) -> list[Task]:
    try:
        from app.ops import mel

        items = mel.list_items(status="open")
    except Exception as exc:
        notes.append(f"MEL tasks skipped: {type(exc).__name__}: {exc}")
        return []
    tails = {a.tail for a in state.aircraft}
    tasks: list[Task] = []
    for it in sorted(items, key=lambda i: i.id):
        if it.tail not in tails:
            continue
        kind = _CHAPTER_KIND.get(it.ata_chapter)
        if kind == "landing_gear" and it.ata_section.startswith("32-42"):
            kind = "brakes"
        elif kind == "landing_gear" and it.ata_section.startswith("32-41"):
            kind = "tires"
        card = rul_card_for(kind, cards) if kind else None
        if card is None:
            notes.append(f"{it.id}: no task card for ATA {it.ata_chapter} ({it.title}); no rectification task derived")
            continue
        comp = next((c for c in sorted(state.components, key=lambda c: c.component_id) if c.tail == it.tail and c.kind == kind), None)
        deadline = max(aware(it.expires_at), now)
        tasks.append(Task(
            id=mel_task_id(it.id), tail=it.tail, component_id=comp.component_id if comp else f"{it.tail}/{kind}",
            title=f"MEL rectification: {it.title}", duration_h=card.duration_h, earliest=now, deadline=deadline,
            required_skill=with_level(card.skill, card.min_level), required_part=card.part,
            part_qty=card.part_qty if card.part else 1, source="mel", synthetic=True,
            basis=(f"MEL {it.id} category {it.category} deferred {fmt(aware(it.deferred_at))}, expires {fmt(aware(it.expires_at))}"
                   f"{' (' + '; '.join(it.restrictions) + ')' if it.restrictions else ''}; SIMULATED register"),
        ))
    return tasks


# ---------------------------------------------------------------- completion hook
def on_task_completed(task_id: str, when: datetime, work_order_id: Optional[str] = None) -> Optional[str]:
    """Close the loop for an llp / mel task whose work order completed. Ordinary tasks: nothing. Never raises.

    Returns a short description of what was done, or None."""
    ref = parse_task_ref(task_id)
    if ref is None:
        return None
    try:
        if ref["source"] == "llp":
            from app.ops import llp

            tails, _, _, _ = llp.fleet_inputs()
            t = next((x for x in tails if x.tail == ref["tail"]), None)
            if t is None:
                return None
            counter = next((c for pos, _k, _s, c in llp._positions(t) if pos == ref["position"]), None)
            if counter is None:
                return None
            llp.record_replacement(ref["tail"], ref["position"], counter, when=when)
            return f"llp {ref['tail']} {ref['position']} replaced"
        from app.ops import mel

        mel.close_item(ref["id"], "system", reference=work_order_id or task_id)
        return f"mel {ref['id']} rectified"
    except Exception as exc:  # absent app.ops, already closed / expired item, no database: not fatal
        log.info("completion hook for %s skipped: %s: %s", task_id, type(exc).__name__, exc)
        return None
