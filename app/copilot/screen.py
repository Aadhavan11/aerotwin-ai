"""Screen-aware AI: the copilot and the agents know what the person is looking at.

The web app sends a ``ScreenContext`` with every question and every agent run: the page, the role, the aircraft on screen, the selected
system, the simulation time and the text that is visible. From it the copilot

* answers questions about the screen itself ("what is the tail number?", "what page is this?", "which engine is selected?"),
* resolves "this aircraft", "it" and "here" to the tail on screen,
* and, in LLM mode, gets the same view plus a brief of everything a pilot would know about that aircraft (status, systems, planned
  maintenance, next legs), so its answers can use it. Numbers in that brief count as legitimate sources for the number check.
"""
from __future__ import annotations

import re
from typing import Optional

from app.contracts.models import EvidenceRef, ScreenContext

_TAIL = re.compile(r"\bSIM-\d{3}\b", re.I)


def brief_for(tail: str, legs_ahead: int = 6) -> str:
    """Everything a pilot would know about one aircraft, as plain text. Best effort: any part that cannot be read is skipped."""
    lines: list[str] = []
    try:
        from app.twin import state as twin

        view = twin.get_twin(tail)
        if view is not None:
            a = view.aircraft
            lines.append(f"Aircraft {a.tail}: {a.type}, home {a.home}, now {'in flight' if a.location == 'AIR' else 'at ' + a.location}, "
                         f"status {a.status}, {a.cycles} flights, {round(a.hours)} flight hours.")
            order = {"aog": 0, "alert": 1, "watch": 2, "in_maintenance": 3, "ok": 4}
            comps = sorted(view.components, key=lambda c: (order.get(c.status, 9), c.rul_cycles.p10 if c.rul_cycles else float("inf")))
            for c in comps:
                if c.status == "ok":
                    continue
                rul = c.rul_cycles
                life = f", conservative remaining life {rul.p10:g} flights (likely {rul.p50:g}, at most {rul.p90:g})" if rul else ""
                lines.append(f"- {c.component_id.split('/')[-1].replace('_', ' ')}: {c.status}, health {round(c.health * 100)}%, anomaly score {c.anomaly_score:.2f}{life}")
            fine = [c.component_id.split("/")[-1].replace("_", " ") for c in comps if c.status == "ok"]
            if fine:
                lines.append(f"Systems with no open item: {', '.join(fine)}.")
    except Exception:  # noqa: BLE001
        pass
    try:
        from app.solver.service import get_state
        from app.solver.store import STORE

        plan = STORE.current
        if plan is not None:
            for j in plan.assignments:
                if j.tail == tail:
                    part = "part on hand" if not j.part_source or j.part_source == j.station else f"part shipped from {j.part_source}"
                    lines.append(f"Planned maintenance: at {j.station} from {j.start:%Y-%m-%d %H:%MZ} to {j.end:%Y-%m-%d %H:%MZ}, {part}.")
        state, _src = get_state()
        ahead = sorted((leg for leg in state.legs if leg.tail == tail and leg.sched_dep >= state.now), key=lambda leg: leg.sched_dep)[:legs_ahead]
        if ahead:
            lines.append("Next legs: " + "; ".join(f"{leg.dep} to {leg.arr} departs {leg.sched_dep:%Y-%m-%d %H:%MZ}" for leg in ahead))
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)


def describe(screen: Optional[ScreenContext]) -> str:
    """The screen as a block for an LLM prompt (empty when nothing is known)."""
    if screen is None or not (screen.page or screen.path):
        return ""
    parts = [f"CURRENT SCREEN (what the person is looking at right now): the '{screen.page or screen.path}' page"
             + (f" (path {screen.path})" if screen.path else "") + (f", viewing as {screen.role}" if screen.role else "") + "."]
    if screen.tail:
        parts.append(f"The aircraft on screen is {screen.tail}. 'This aircraft', 'it' and 'here' mean {screen.tail}.")
    if screen.selected:
        parts.append(f"Selected on screen: {screen.selected}.")
    if screen.sim_now:
        parts.append(f"Simulation time: {screen.sim_now}.")
    if screen.text:
        parts.append("Text visible on screen:\n" + screen.text[:2500])
    if screen.tail:
        brief = brief_for(screen.tail)
        if brief:
            parts.append(f"Everything known about {screen.tail}:\n{brief}")
    return "\n".join(parts)


def sources(screen: Optional[ScreenContext]) -> list[str]:
    """Texts whose numbers are legitimate to quote (what is on screen and the aircraft brief)."""
    if screen is None:
        return []
    out = [screen.text, screen.sim_now or "", screen.selected or "", screen.path]
    if screen.tail:
        out.append(brief_for(screen.tail))
    return [s for s in out if s]


def _evidence(screen: ScreenContext) -> list[EvidenceRef]:
    return [EvidenceRef(kind="event", ref="screen", note=f"{screen.page or screen.path}" + (f" / {screen.tail}" if screen.tail else ""))]


def answer(message: str, screen: Optional[ScreenContext]) -> Optional[tuple[str, list[EvidenceRef]]]:
    """Answers about the screen itself, or "this aircraft" questions, without calling the planner. None = not a screen question."""
    if screen is None or not (screen.page or screen.path):
        return None
    m = message.lower().strip().rstrip("?.! ")
    page = screen.page or screen.path
    tail = screen.tail
    # Only clear questions about the screen: "tail number?", "what aircraft is this?", "which plane am I looking at?". A fleet question such as
    # "which aircraft needs attention?" must go to the normal copilot.
    asks_tail = (re.search(r"\b(tail\s*(number|no\.?|#|id)|registration|aircraft number)\b", m)
                 or re.search(r"\b(which|what) (aircraft|plane|airplane|jet|tail)\b.*\b(this|am i|on (my |the )?screen|looking at|viewing|here)\b", m)
                 or re.search(r"\bwhat am i (looking at|viewing)\b", m))
    mentions_other = {t.upper() for t in _TAIL.findall(message)} - ({tail.upper()} if tail else set())
    if asks_tail and not mentions_other and not re.search(r"\b(prioriti|compare|window|parts?)\b", m):
        if tail:
            brief = brief_for(tail).split("\n")[0]
            return (f"You are on the {page} page, looking at tail {tail}." + (f" {brief}" if brief else ""), _evidence(screen))
        return (f"You are on the {page} page, which is not about a single aircraft. Open one from the fleet list, or ask me about a tail number.", _evidence(screen))
    if re.search(r"\b(what|which) (page|screen|view)\b|\bwhere am i\b|\bwhat can you see\b|\bwhat do you see\b|\bcan you see (my|the) screen\b|\bwhat'?s on (my|the) screen\b", m):
        shown = " ".join(line.strip() for line in (screen.text or "").splitlines() if line.strip())[:260]
        return (f"I can see the {page} page" + (f" as {screen.role}" if screen.role else "") + (f", for {tail}" if tail else "") + "."
                + (f" It shows: {shown}" if shown else ""), _evidence(screen))
    if re.search(r"\b(selected|which|what)\b.*\b(system|component|engine|part)\b.*\b(selected|open|showing|looking|on screen)\b|\bwhat('?s| is) selected\b", m):
        return ((f"{screen.selected} is selected on the {page} page." if screen.selected else f"Nothing is selected on the {page} page."), _evidence(screen))
    if re.search(r"\b(my role|viewing as|who am i)\b", m):
        return ((f"You are viewing as {screen.role}." if screen.role else "I do not know which role you are viewing as."), _evidence(screen))
    if re.search(r"\b(what('?s| is)? the (sim|simulation) (time|date|clock)|what time is it|simulation time)\b", m):
        return ((f"The simulation clock reads {screen.sim_now}." if screen.sim_now else "I cannot see the simulation clock."), _evidence(screen))
    # "this aircraft" questions: no explicit tail in the message, a tail on screen, and a plain status-type question.
    if tail and not _TAIL.search(message) and re.search(r"\b(this|that|the)\s+(aircraft|plane|airplane|jet|tail)\b|\b(is|does) it\b|\bwhat'?s wrong\b|\bany (issues?|problems?|faults?)\b", m) \
            and not re.search(r"\b(compare|window|windows|prioriti[sz]e|draft|work orders?)\b", m):
        brief = brief_for(tail)
        if brief:
            return (f"Here is what I know about {tail}, the aircraft on your screen.\n{brief}", _evidence(screen))
    return None
