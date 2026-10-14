"""Explainable copilot (feature 3.6): ``chat(ChatRequest) -> ChatResponse``.

Two modes, same tools, same response shape:

* **scripted**: keyword intents for the six example questions of the concept
  doc call the matching tools and fill a template from their output. Always
  available; this is what the demo relies on.
* **llm**: Claude picks tools and writes the answer. Any numeral the tools did
  not return replaces the answer with the scripted one. Any API error falls
  back to scripted.

An answer the data cannot support returns ``missing_info`` rather than a guess.
Work orders it proposes are always ``pending_approval``.
"""
from __future__ import annotations

import logging
import re
from collections import deque
from typing import Optional

from app.contracts.models import ChatRequest, ChatResponse, EvidenceRef
from app.copilot import audit, llm
from app.copilot.tools import Toolbox, normalize_tail

log = logging.getLogger(__name__)

SESSIONS: dict[str, deque] = {}
HISTORY_TURNS = 6

QUICK_PROMPTS = [
    "Which aircraft are most likely to require maintenance in the next 72 hours?",
    "Why is aircraft SIM-017 prioritized over SIM-012?",
    "Find the best feasible maintenance windows for tomorrow.",
    "Which parts and technicians are required for these maintenance jobs?",
    "What changes if a maintenance facility becomes unavailable?",
    "Compare the current schedule with the optimized schedule.",
]


def reset() -> None:
    SESSIONS.clear()


# --------------------------------------------------------------------------
# Intent
# --------------------------------------------------------------------------
_TAILS = re.compile(r"\b(?:SIM-?\s?\d{1,3}|A\s?\d{1,3})\b", re.I)
_STATION = re.compile(r"\b([A-Z]{3})\b")


def classify(message: str) -> str:
    m = message.lower()
    if re.search(r"\b(leave|vacation|holiday|sick|salary|payroll|shift roster|next week)\b", m):
        return "unanswerable"
    if re.search(r"\b(draft|create|propose|prepare|generate)\b.*\bwork orders?\b", m):
        return "draft"
    if len(_TAILS.findall(message)) >= 2 and re.search(r"\b(why|prioriti[sz]|before|over|first|ahead)\b", m):
        return "priority"
    if re.search(r"\b(compare|comparison|versus|vs|baseline)\b", m) or "optimized schedule" in m:
        return "compare"
    if re.search(r"\b(unavailable|closed|closes|closure|shut|out of service|goes down)\b", m) and \
            re.search(r"\b(facility|hangar|station|base|airport)\b|\b[A-Z]{3}\b", message):
        return "facility"
    if re.search(r"\b(window|windows|slot|slots)\b", m):
        return "windows"
    if re.search(r"\b(parts?|technicians?|requirements?|required|skills?)\b", m):
        return "requirements"
    if re.search(r"\b(72|likely|at risk|require maintenance|need maintenance|attention)\b", m):
        return "at_risk"
    return "unknown"


# --------------------------------------------------------------------------
# Scripted answers
# --------------------------------------------------------------------------
def _loc(x) -> str:
    """A diff endpoint ({station, start}) or plain value as text."""
    if isinstance(x, dict):
        st, t = x.get("station"), x.get("start")
        t = t.strftime("%Y-%m-%dT%H:%MZ") if hasattr(t, "strftime") else t
        return " ".join(str(p) for p in (st, t) if p)
    return str(x)


def _fmt_rul(c: dict) -> str:
    return f"RUL p10 {c['rul_p10']:g}, p50 {c['rul_p50']:g}, p90 {c['rul_p90']:g} cycles"


def scripted(message: str, box: Toolbox) -> tuple[str, list[dict], list]:
    """(answer, evidence, tool_calls) using the toolbox directly. ``tool_calls`` is [(tool, input)]."""
    intent = classify(message)
    calls: list[tuple[str, dict]] = []
    ev: list[dict] = []

    def call(name: str, **kw) -> dict:
        calls.append((name, kw))
        out = getattr(box, name)(**kw)
        ev.extend(out.get("evidence", []))
        return out

    if intent == "unanswerable":
        box._miss("this system has no roster, leave or shift-planning data beyond technician shift windows")
        return ("I can't answer that from the data I have. The system knows technician shift windows and skills, "
                "not leave or personal availability."), ev, calls

    if intent == "at_risk":
        out = call("list_at_risk", horizon_cycles=60)
        comps = out["components"]
        if not comps:
            return "No component currently has a low conservative RUL or an alert or watch status.", ev, calls
        top = comps[:3]
        lines = "; ".join(f"{c['component_id']} ({c['status']}, {_fmt_rul(c)})" for c in top)
        sched = out["scheduled_within_72h"]
        tail = (" Scheduled in the next 72 hours: " + "; ".join(f"{s['task_id']} at {s['station']} from {s['start']}" for s in sched[:3]) + "."
                if sched else " Nothing is scheduled to start in the next 72 hours.")
        return f"Highest risk first: {lines}.{tail} RUL is counted in flights.", ev, calls

    if intent == "priority":
        known = {c.tail for c in box.ctx().state.components} | {a.tail for a in box.ctx().state.aircraft}
        found = [normalize_tail(t, known) for t in _TAILS.findall(message)]
        found = [f for f in found if f]
        if len(found) < 2:
            box._miss("could not identify two aircraft in the question")
            return "I could not tell which two aircraft you mean. Name them like SIM-017 and SIM-012.", ev, calls
        a, b = found[0], found[1]
        out = call("explain_priority", tail_a=a, tail_b=b)
        sa, sb = out["a"], out["b"]
        parts = []
        for s in (sa, sb):
            if s["worst_component"]:
                sched = f", scheduled at {s['scheduled']['station']} from {s['scheduled']['start']}" if s["scheduled"] else ", not scheduled"
                parts.append(f"{s['tail']}: {s['worst_component']} is {s['status']} with RUL p10 {s['rul_p10']:g} and p50 {s['rul_p50']:g}{sched}")
            else:
                parts.append(f"{s['tail']}: no RUL estimate available")
        order = out["scheduled_order"]
        if order == "a_first":
            verdict = f"{a} comes before {b} in the current plan."
        elif order == "b_first":
            verdict = f"{b} comes before {a} in the current plan, so {a} is not prioritized over {b}."
        elif order == "same_time":
            verdict = f"{a} and {b} start at the same time in the current plan."
        else:
            verdict = f"Neither {a} nor {b} has a scheduled task in the current plan."
        urgent = out["more_urgent_by_conservative_rul"]
        why = (f" {urgent} has the lower conservative RUL (p10), which sets the deadline." if urgent else "")
        reason = sa["scheduled"]["reason"] if sa["scheduled"] and order == "a_first" else ""
        extra = f" Planner's reason for {a}: {reason}" if reason else ""
        wo = call("draft_work_orders", tails=[a]) if order == "a_first" else None
        tail = ""
        if wo and wo["work_orders"]:
            pending = [w["id"] for w in wo["work_orders"] if w["status"] == "pending_approval"]
            done = [f"{w['id']} ({w['status']})" for w in wo["work_orders"] if w["status"] != "pending_approval"]
            if pending:
                tail += f" Work order {', '.join(pending)} for {a} is waiting for your approval."
            if done:
                tail += f" Already decided: {', '.join(done)}."
        return " ".join(parts) + ". " + verdict + why + extra + tail, ev, calls

    if intent == "windows":
        out = call("find_windows", day="tomorrow", limit=5)
        pl, gw = out["planned_assignments"], out["ground_windows"]
        bits = []
        if pl:
            bits.append("Planned for " + out["day"] + ": " + "; ".join(
                f"{p['task_id']} at {p['station']} {p['start']} to {p['end']}" for p in pl[:3]))
        if gw:
            bits.append("Longest ground windows: " + "; ".join(
                f"{w['tail']} at {w['station']} {w['start']} to {w['end']} ({w['hours']:g} h)" for w in gw[:3]))
        if not bits:
            return f"No planned work or ground windows were found for {out['day']}.", ev, calls
        return ". ".join(bits) + ".", ev, calls

    if intent == "requirements":
        out = call("job_requirements")
        jobs = out["jobs"]
        if not jobs:
            return "There are no planned jobs, so there are no part or technician requirements to list.", ev, calls
        lines = []
        for j in jobs[:4]:
            part = f"{j['part']} x{j['part_qty']}" if j["part"] else "no part"
            stock = f", {j['part_on_hand_at_station']} on hand at {j['station']}" if j["part_on_hand_at_station"] is not None else ""
            src = f", shipped from {j['part_shipped_from']}" if j["part_shipped_from"] else ""
            lines.append(f"{j['task_id']} at {j['station']}: skill {j['skill']}, technician {j['technician_id']}, {part}{stock}{src}")
        return "; ".join(lines) + ".", ev, calls

    if intent == "facility":
        c = box.ctx()
        known = {s.iata for s in c.state.stations}
        named = [t for t in _STATION.findall(message) if t in known]
        station = named[0] if named else None
        if station is None and c.current is not None and c.current.assignments:
            counts: dict[str, int] = {}
            for a in c.current.assignments:
                counts[a.station] = counts.get(a.station, 0) + 1
            station = max(sorted(counts), key=lambda s: counts[s])
        if station is None:
            box._miss("no facility named and no plan to pick one from")
            return "Name the facility (for example DFW) and I will replan as if it were closed.", ev, calls
        out = call("what_if_facility_closed", station=station)
        if "error" in out:
            return f"I could not evaluate that: {out['error']}.", ev, calls
        b, a = out["before"], out["after"]
        moved = out["moved"]
        mv = ("; ".join(f"{m['task_id']} moves from {_loc(m['from'])} to {_loc(m['to'])}" for m in moved[:3]) + ".") if moved else "No task moves."
        feas = "The replanned schedule is feasible." if out["feasible_after"] else \
            "No feasible plan exists: " + "; ".join(out["infeasible_reasons"][:2]) + "."
        return (f"If {station} is unavailable: {mv} {feas} Flights affected {b['flights_affected']} -> {a['flights_affected']}, "
                f"downtime {b['downtime_h']:g} h -> {a['downtime_h']:g} h, logistics cost ${b['logistics_cost_usd']:g} -> ${a['logistics_cost_usd']:g}. "
                f"This is a what-if; the live plan is unchanged."), ev, calls

    if intent == "compare":
        out = call("compare_plans")
        if "error" in out:
            return "I cannot compare the schedules because one of the plans is not available.", ev, calls
        b, o = out["baseline"]["metrics"], out["optimized"]["metrics"]
        return (f"Baseline ({out['baseline_definition']}) against the optimized plan: "
                f"downtime {b['downtime_h']:g} h vs {o['downtime_h']:g} h, flights affected {b['flights_affected']} vs {o['flights_affected']}, "
                f"logistics cost ${b['logistics_cost_usd']:g} vs ${o['logistics_cost_usd']:g}, "
                f"tasks scheduled {b['tasks_scheduled']} of {b['tasks_total']} vs {o['tasks_scheduled']} of {o['tasks_total']}. "
                f"Simulated, relative to that baseline."), ev, calls

    if intent == "draft":
        known = {c.tail for c in box.ctx().state.components}
        tails = [t for t in (normalize_tail(x, known) for x in _TAILS.findall(message)) if t]
        out = call("draft_work_orders", tails=tails or None)
        n = len(out["work_orders"])
        if not n:
            return "There is nothing in the current plan to draft work orders for.", ev, calls
        return f"Drafted {n} work order(s). All are pending approval; nothing is approved until a person decides.", ev, calls

    box._miss("I can answer questions about at-risk aircraft, priorities, maintenance windows, parts and technicians, facility closures, and schedule comparison")
    hits = call("search_kb", query=message)
    if hits["hits"]:
        h = hits["hits"][0]
        return f"That is outside the planning questions I can compute. The closest task card is {h['doc_id']}: {h['title']}.", ev, calls
    return "I don't have the data to answer that.", ev, calls


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def _to_evidence(raw: list[dict], calls: list[tuple[str, dict]]) -> list[EvidenceRef]:
    seen: set[tuple[str, str]] = set()
    out: list[EvidenceRef] = []
    for i, (name, _inp) in enumerate(calls, 1):
        out.append(EvidenceRef(kind="tool_call", ref=f"tc-{i}", note=name))
        seen.add(("tool_call", f"tc-{i}"))
    for e in raw:
        key = (e["kind"], e["ref"])
        if key in seen or not e.get("ref"):
            continue
        seen.add(key)
        out.append(EvidenceRef(kind=e["kind"], ref=str(e["ref"]), note=e.get("note", "")))
    return out


# What the last answer was produced by and why (never contains credentials). Read by GET /copilot/status.
LAST_MODE: dict = {"mode": "scripted", "note": "no answer yet"}


def chat(req: ChatRequest, *, toolbox: Optional[Toolbox] = None, client=None, mode: Optional[str] = None) -> ChatResponse:
    """``mode`` None picks llm when credentials exist, else scripted."""
    resp = _answer(req, toolbox, client, mode)
    audit.record("agent:coordinator", "copilot_answer", "chat", req.message[:80])
    return resp


def _answer(req: ChatRequest, toolbox: Optional[Toolbox], client, mode: Optional[str]) -> ChatResponse:
    box = toolbox or Toolbox()
    hist = SESSIONS.setdefault(req.session_id, deque(maxlen=HISTORY_TURNS * 2))
    use_llm = mode == "llm" or (mode is None and llm.llm_available())
    if use_llm:
        resp = _chat_llm(req, box, hist, client)
        if resp is not None:
            hist.append({"role": "user", "content": req.message})
            hist.append({"role": "assistant", "content": resp.answer})
            LAST_MODE.update(mode="llm", note="answered by the LLM; every number matched a tool result")
            return resp
        box.missing.clear()
        box.proposed = []
        box._ctx = None  # fresh world for the scripted pass
    answer, ev, calls = scripted(req.message, box)
    if not use_llm:
        LAST_MODE.update(mode="scripted", note="scripted answers (no API key set, or COPILOT_MODE=scripted)")
    resp = ChatResponse(answer=answer, evidence=_to_evidence(ev, calls), missing_info=list(box.missing),
                        proposed_work_orders=list(box.proposed))
    hist.append({"role": "user", "content": req.message})
    hist.append({"role": "assistant", "content": answer})
    return resp


def _chat_llm(req: ChatRequest, box: Toolbox, hist: deque, client) -> Optional[ChatResponse]:
    try:
        client = client or llm.make_client()
    except Exception as exc:
        log.warning("LLM client unavailable: %s", exc)
        LAST_MODE.update(mode="scripted", note=f"LLM client unavailable ({type(exc).__name__}); used scripted answers")
        return None
    system = llm.SAFETY.format(role="maintenance copilot") + " " + llm.COPILOT_PROMPT
    messages = [*list(hist), {"role": "user", "content": req.message}]
    res = llm.run_tool_loop(client, system=system, messages=messages, tools=box.tools())
    if res.error or not res.text:
        log.warning("copilot LLM failed (%s); using scripted mode", res.error or "empty answer")
        LAST_MODE.update(mode="scripted", note=f"LLM call failed ({str(res.error or 'empty answer')[:120]}); used scripted answers")
        return None
    history_text = [m["content"] for m in messages if isinstance(m.get("content"), str)]
    bad = llm.unsupported_numbers(res.text, res.sources(history_text))
    if bad:
        log.warning("copilot answer contains numbers no tool returned %s; using scripted mode", bad)
        LAST_MODE.update(mode="scripted", note="the LLM answer contained a number no tool returned; used scripted answers")
        return None
    raw: list[dict] = []
    for c in res.calls:
        if isinstance(c.output, dict):
            raw.extend(c.output.get("evidence", []))
    evidence = _to_evidence(raw, [(c.tool, c.input) for c in res.calls])
    return ChatResponse(answer=res.text, evidence=evidence, missing_info=list(box.missing),
                        proposed_work_orders=list(box.proposed))
