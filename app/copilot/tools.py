"""The tools the copilot and the LLM agents call. Deterministic: every number a
user sees comes from a return value here, never from the model.

``Toolbox`` reads the world through a ``Context`` (fleet snapshot, current plan,
static baseline). By default the context comes from the scheduler service
(``app.solver.service``, Pardhu) and falls back to the twin snapshot alone, so
the copilot degrades instead of failing when a neighbour is missing.

Each tool returns a plain dict. An ``evidence`` list of ``{kind, ref, note}``
(``kind`` in ``tool_call|health|plan|kb|event``) travels with the result so the
UI can show chips.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from app.contracts.models import FleetState, Plan, ScenarioEvent
from app.copilot import kb, workorders
from app.ml.sensors import kind_from_component_id
from app.twin import state as twin

log = logging.getLogger(__name__)
UTC = timezone.utc


# --------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------
@dataclass
class Context:
    state: FleetState
    current: Optional[Plan]
    baseline: Optional[Plan]
    source: str


def default_context() -> Context:
    try:
        from app.solver.service import ensure_plans, get_state

        state, source = get_state()
        current, baseline = ensure_plans(state, source)
        return Context(state, current, baseline, source)
    except Exception as exc:
        log.warning("scheduler unavailable (%s); copilot sees the twin only", exc)
        return Context(twin.snapshot(), None, None, "twin.snapshot (no scheduler)")


def _ev(kind: str, ref: str, note: str = "") -> dict:
    return {"kind": kind, "ref": ref, "note": note}


def _t(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%MZ")


def _endpoint(e: Any) -> Any:
    if isinstance(e, dict):
        return {k: (_t(v) if isinstance(v, datetime) else v) for k, v in e.items()}
    return e


def _plan_view(plan: Plan, limit: int = 12) -> dict:
    m = plan.metrics
    return {
        "plan_id": plan.id, "mode": plan.mode, "feasible": plan.feasible,
        "infeasible_reasons": list(plan.infeasible_reasons),
        "metrics": {
            "downtime_h": m.downtime_h, "flights_affected": m.flights_affected,
            "logistics_cost_usd": m.logistics_cost_usd, "tech_utilization": m.tech_utilization,
            "total_cost_usd": m.total_cost_usd, "cost_parts_usd": m.cost_parts_usd, "cost_labor_usd": m.cost_labor_usd,
            "cost_bay_usd": m.cost_bay_usd, "cost_overhead_usd": m.cost_overhead_usd, "cost_delay_usd": m.cost_delay_usd, "cost_exposure_usd": m.cost_exposure_usd,
            "hangar_utilization": m.hangar_utilization, "tasks_total": m.tasks_total,
            "tasks_scheduled": m.tasks_scheduled, "late_or_unscheduled": m.late_or_unscheduled,
        },
        "solve_seconds": plan.solve_seconds,
        "assignments": [
            {"task_id": a.task_id, "tail": a.tail, "station": a.station, "start": _t(a.start), "end": _t(a.end),
             "technician_id": a.technician_id, "part_source": a.part_source, "window_hours": a.window_hours,
             "extension_h": a.extension_h, "reason": a.reason}
            for a in plan.assignments[:limit]
        ],
    }


_TAIL = re.compile(r"(?:SIM-?)?0*(\d{1,3})", re.I)


def normalize_tail(text: str, known: set[str]) -> Optional[str]:
    """'A17', 'SIM-017', '17' -> 'SIM-017' if that aircraft exists."""
    m = _TAIL.search(text or "")
    if not m:
        return None
    cand = f"SIM-{int(m.group(1)):03d}"
    return cand if cand in known else None


# --------------------------------------------------------------------------
# Tool plumbing
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable[..., Any]
    agent: str = "coordinator"          # which agent role the step is attributed to

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


def _schema(props: dict, required: Optional[list[str]] = None) -> dict:
    return {"type": "object", "properties": props, "required": required or [], "additionalProperties": False}


class Toolbox:
    def __init__(self, context_fn: Optional[Callable[[], Context]] = None,
                 replan_fn: Optional[Callable[..., Any]] = None,
                 windows_fn: Optional[Callable[..., Any]] = None):
        self._context_fn = context_fn or (lambda: default_context())
        self._replan_fn = replan_fn
        self._windows_fn = windows_fn
        self._ctx: Optional[Context] = None
        self.missing: list[str] = []
        self.proposed: list = []
        self.actions: list = []                 # proposed changes to maintenance requests (never applied here)

    # one consistent world per question
    def ctx(self) -> Context:
        if self._ctx is None:
            self._ctx = self._context_fn()
        return self._ctx

    def _need_plan(self, which: str = "current") -> Optional[Plan]:
        c = self.ctx()
        plan = c.current if which == "current" else c.baseline
        if plan is None:
            self._miss("no plan is available (the scheduler has not produced one)")
        return plan

    def _miss(self, text: str) -> None:
        if text not in self.missing:
            self.missing.append(text)

    # ---- health (Health agent) ---------------------------------------------
    def list_at_risk(self, horizon_cycles: int = 60) -> dict:
        c = self.ctx()
        rows = []
        for comp in c.state.components:
            r = comp.rul_cycles
            if r is None:
                continue
            if r.p10 <= horizon_cycles or comp.status in ("alert", "watch"):
                rows.append({"tail": comp.tail, "component_id": comp.component_id, "status": comp.status,
                             "health": comp.health, "rul_p10": r.p10, "rul_p50": r.p50, "rul_p90": r.p90,
                             "data_quality": comp.data_quality.status})
        rows.sort(key=lambda x: (x["rul_p10"], x["component_id"]))
        soon = []
        plan = c.current
        if plan is not None:
            limit = c.state.now + timedelta(hours=72)
            soon = [{"task_id": a.task_id, "tail": a.tail, "station": a.station, "start": _t(a.start), "end": _t(a.end)}
                    for a in plan.assignments if a.start <= limit]
        else:
            self._miss("no plan available, so scheduled work in the next 72 hours cannot be listed")
        return {
            "horizon_cycles": horizon_cycles, "count": len(rows), "components": rows[:10],
            "scheduled_within_72h": soon, "now": _t(c.state.now),
            "evidence": [_ev("health", r["component_id"], f"status {r['status']}") for r in rows[:5]],
        }

    def get_component_health(self, component_id: str) -> dict:
        for comp in self.ctx().state.components:
            if comp.component_id == component_id:
                r = comp.rul_cycles
                return {
                    "component_id": comp.component_id, "kind": comp.kind, "status": comp.status,
                    "health": comp.health, "anomaly_score": comp.anomaly_score, "cycles": comp.cycles,
                    "rul_p10": r.p10 if r else None, "rul_p50": r.p50 if r else None, "rul_p90": r.p90 if r else None,
                    "data_quality": comp.data_quality.status, "data_quality_reasons": list(comp.data_quality.reasons),
                    "last_maintenance": _t(comp.last_maintenance) if comp.last_maintenance else None,
                    "top_signals": twin.top_signals(component_id),
                    "evidence": [_ev("health", component_id, f"status {comp.status}")],
                }
        self._miss(f"no component {component_id!r} in the twin")
        return {"error": f"unknown component {component_id!r}", "evidence": []}

    def get_sensor_trend(self, component_id: str, last: int = 20) -> dict:
        tail = component_id.split("/")[0]
        rows = twin.get_telemetry(tail, component_id, max(1, min(int(last), 60)))
        keys = twin.top_signals(component_id)[:3] or (list(rows[-1].values)[:3] if rows else [])
        if not rows:
            self._miss(f"no telemetry stored for {component_id}")
        return {
            "component_id": component_id, "signals": keys,
            "rows": [{"cycle": r.cycle, **{k: r.values.get(k) for k in keys}} for r in rows],
            "evidence": [_ev("health", component_id, "sensor trend")],
        }

    def get_maintenance_history(self, tail: str) -> dict:
        view = twin.get_twin(tail)
        if view is None:
            self._miss(f"no aircraft {tail!r} in the twin")
            return {"error": f"unknown aircraft {tail!r}", "evidence": []}
        return {
            "tail": tail,
            "last_maintenance": {c.component_id: (_t(c.last_maintenance) if c.last_maintenance else None) for c in view.components},
            "events": [{"ts": _t(e.ts), "kind": e.kind, "summary": e.summary} for e in view.timeline[:10]],
            "evidence": [_ev("event", e.ref, e.summary) for e in view.timeline[:3] if e.ref],
        }

    # ---- plan (Scheduling / Operations agents) -------------------------------
    def get_plan(self, which: str = "current") -> dict:
        plan = self._need_plan("current" if which != "baseline" else "baseline")
        if plan is None:
            return {"error": "no plan available", "evidence": []}
        return {**_plan_view(plan), "evidence": [_ev("plan", plan.id, f"{plan.mode} plan")]}

    def explain_priority(self, tail_a: str, tail_b: str) -> dict:
        c = self.ctx()
        plan = self._need_plan()

        def side(tail: str) -> dict:
            comps = [x for x in c.state.components if x.tail == tail and x.rul_cycles is not None]
            worst = min(comps, key=lambda x: x.rul_cycles.p10) if comps else None
            mine = [a for a in (plan.assignments if plan else []) if a.tail == tail]
            first = min(mine, key=lambda a: a.start) if mine else None
            return {
                "tail": tail,
                "worst_component": worst.component_id if worst else None,
                "status": worst.status if worst else None,
                "rul_p10": worst.rul_cycles.p10 if worst else None,
                "rul_p50": worst.rul_cycles.p50 if worst else None,
                "scheduled": ({"task_id": first.task_id, "station": first.station, "start": _t(first.start),
                               "end": _t(first.end), "reason": first.reason} if first else None),
            }

        a, b = side(tail_a), side(tail_b)
        if a["scheduled"] and b["scheduled"]:
            order = "a_first" if a["scheduled"]["start"] < b["scheduled"]["start"] else (
                "b_first" if a["scheduled"]["start"] > b["scheduled"]["start"] else "same_time")
        elif a["scheduled"] or b["scheduled"]:
            order = "a_first" if a["scheduled"] else "b_first"
        else:
            order = "neither_scheduled"
            self._miss(f"neither {tail_a} nor {tail_b} has a scheduled task in the current plan")
        more_urgent = None
        if a["rul_p10"] is not None and b["rul_p10"] is not None and a["rul_p10"] != b["rul_p10"]:
            more_urgent = tail_a if a["rul_p10"] < b["rul_p10"] else tail_b
        ev = [_ev("health", x["worst_component"], f"{x['tail']} worst component") for x in (a, b) if x["worst_component"]]
        ev += [_ev("plan", plan.id, "current plan")] if plan else []
        return {"a": a, "b": b, "scheduled_order": order, "more_urgent_by_conservative_rul": more_urgent,
                "basis": "Deadlines come from the conservative RUL (p10); the optimizer then picks window and station.",
                "evidence": ev}

    def find_windows(self, day: str = "tomorrow", limit: int = 5) -> dict:
        c = self.ctx()
        base = c.state.now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        start = base + timedelta(days=1 if day == "tomorrow" else 0)
        end = start + timedelta(days=1)
        planned = []
        if c.current is not None:
            planned = [{"task_id": a.task_id, "tail": a.tail, "station": a.station, "start": _t(a.start), "end": _t(a.end),
                        "window_hours": a.window_hours, "reason": a.reason}
                       for a in c.current.assignments if a.start < end and a.end > start]
        else:
            self._miss("no plan available, so planned windows cannot be listed")
        windows = []
        fn = self._windows_fn
        if fn is None:
            try:
                from app.solver import ground_windows as fn  # type: ignore[no-redef]
            except Exception:
                fn = None
        if fn is not None and c.state.legs:
            try:
                for w in fn(c.state.legs, c.state.now):
                    if w.start < end and w.end > start:
                        windows.append({"tail": w.tail, "station": w.station, "start": _t(w.start), "end": _t(w.end),
                                        "hours": round(w.hours, 1)})
            except Exception as exc:
                log.warning("ground_windows failed: %s", exc)
        else:
            self._miss("no flight schedule available, so ground windows cannot be listed")
        windows.sort(key=lambda w: (-w["hours"], w["tail"]))
        return {"day": _t(start)[:10], "planned_assignments": planned[:limit], "ground_windows": windows[:limit],
                "evidence": [_ev("plan", c.current.id, "current plan")] if c.current else []}

    def job_requirements(self) -> dict:
        c = self.ctx()
        plan = self._need_plan()
        if plan is None:
            return {"jobs": [], "evidence": []}
        stock = {(s.iata, p.part): p for s in c.state.stations for p in s.parts}
        jobs, ev = [], [_ev("plan", plan.id, "current plan")]
        for a in plan.assignments:
            comp = workorders.component_of(a.task_id)
            kind = kind_from_component_id(comp[1]) if comp else None
            card = kb.default_card_for(kind) if kind else None
            part = card.get("part") if card else None
            on_hand = stock.get((a.station, part)) if part else None
            jobs.append({
                "task_id": a.task_id, "tail": a.tail, "station": a.station, "start": _t(a.start),
                "skill": card.get("skill") if card else None, "technician_id": a.technician_id,
                "part": part, "part_qty": card.get("part_qty") if card else None,
                "part_on_hand_at_station": on_hand.qty if on_hand else None,
                "part_shipped_from": a.part_source, "kb_card": card["id"] if card else None,
            })
            if card:
                ev.append(_ev("kb", card["id"], "synthetic task card"))
        return {"jobs": jobs, "evidence": ev}

    def what_if_facility_closed(self, station: str) -> dict:
        c = self.ctx()
        plan = self._need_plan()
        if plan is None:
            return {"error": "no plan available", "evidence": []}
        if station not in {s.iata for s in c.state.stations}:
            self._miss(f"no station {station!r} in the fleet data")
            return {"error": f"unknown station {station!r}", "evidence": []}
        fn = self._replan_fn
        if fn is None:
            try:
                from app.whatif.replan import replan as fn  # type: ignore[no-redef]
            except Exception:
                self._miss("the what-if module is not available")
                return {"error": "what-if unavailable", "evidence": []}
        event = ScenarioEvent(id="E-whatif", type="facility_closed", at=c.state.now, station=station,
                              note="what-if from the copilot; not applied")
        res = fn(c.state, plan, event)
        diff = res.diff
        return {
            "station": station, "feasible_after": res.after.feasible,
            "infeasible_reasons": list(res.after.infeasible_reasons),
            "moved": [{"task_id": m.get("task_id"), "from": _endpoint(m.get("from")), "to": _endpoint(m.get("to")),
                       "why": m.get("why")} for m in diff.moved[:5]],
            "added": list(diff.added), "dropped": list(diff.dropped),
            "before": _plan_view(res.before, 0)["metrics"], "after": _plan_view(res.after, 0)["metrics"],
            "solve_seconds": res.solve_seconds, "applied_to_world": False,
            "evidence": [_ev("plan", res.before.id, "plan before"), _ev("plan", res.after.id, "plan if closed"),
                         _ev("event", "E-whatif", f"{station} hangar closed (what-if)")],
        }

    def compare_plans(self) -> dict:
        c = self.ctx()
        cur, base = c.current, c.baseline
        if cur is None or base is None:
            self._miss("both the optimized plan and the baseline are needed to compare")
            return {"error": "plans unavailable", "evidence": []}
        return {
            "baseline": _plan_view(base, 0), "optimized": _plan_view(cur, 0),
            "baseline_definition": "static baseline: home base, last ground window that fits before the deadline",
            "evidence": [_ev("plan", base.id, "static baseline"), _ev("plan", cur.id, "optimized plan")],
        }

    # ---- coordinator -----------------------------------------------------------
    def draft_work_orders(self, tails: Optional[list[str]] = None) -> dict:
        plan = self._need_plan()
        if plan is None:
            return {"work_orders": [], "evidence": []}
        orders, notes = workorders.draft_from_plan(plan, tails=tails)
        self.proposed = orders
        return {"work_orders": [{"id": w.id, "tail": w.tail, "station": w.station, "start": _t(w.start), "end": _t(w.end),
                                 "status": w.status, "description": w.description} for w in orders],
                "notes": notes, "all_pending_approval": all(w.status == "pending_approval" for w in orders),
                "evidence": [_ev("plan", plan.id, "drafted from this plan")]
                + [_ev("event", w.id, "work order draft") for w in orders[:3]]}

    # ---- proposals: the copilot prepares a change, the person allows it ------------------------
    def propose_request_edit(self, work_order_id: str, title: Optional[str] = None, work: Optional[str] = None, part: Optional[str] = None,
                             part_qty: Optional[int] = None, order_part: Optional[bool] = None, start: Optional[str] = None, end: Optional[str] = None) -> dict:
        from app.copilot import actions

        prop = actions.propose_edit(work_order_id, title=title, work=work, part=part, part_qty=part_qty, order_part=order_part, start=start, end=end)
        if prop.get("kind"):
            self.actions.append(prop)
        return {"proposed": bool(prop.get("kind")), "valid": prop["ok"], "problems": prop["problems"], "changes": prop.get("changes", []),
                "note": "Nothing was changed. The person sees this proposal and must allow it.", "evidence": []}

    def propose_new_request(self, tail: str, station: str, start: str, end: str, title: str, work: str = "", why: str = "", part: Optional[str] = None,
                            part_qty: int = 0, order_part: bool = False) -> dict:
        from app.copilot import actions
        from datetime import datetime as _dt

        prop = actions.propose_new(tail=tail, station=station, start=_dt.fromisoformat(start.replace("Z", "+00:00")), end=_dt.fromisoformat(end.replace("Z", "+00:00")),
                                   title=title, work=work, why=why, part=part, part_qty=part_qty, order_part=order_part)
        self.actions.append(prop)
        return {"proposed": True, "valid": prop["ok"], "problems": prop["problems"], "changes": prop["changes"],
                "note": "Nothing was created. The person sees this proposal and must allow it.", "evidence": []}

    def search_kb(self, query: str) -> dict:
        hits = kb.search_kb(query)
        if not hits:
            self._miss(f"no task card matches {query!r}")
        return {"query": query, "hits": hits, "evidence": [_ev("kb", h["doc_id"], h["title"]) for h in hits]}

    def report_missing_info(self, items: list[str]) -> dict:
        for i in items:
            self._miss(str(i))
        return {"recorded": len(items), "evidence": []}

    # ---- registry ---------------------------------------------------------------
    def tools(self) -> dict[str, Tool]:
        s = _schema
        t = [
            Tool("list_at_risk", "List components with a low conservative RUL (p10, in flights) or status watch/alert, "
                 "worst first, plus work already scheduled in the next 72 hours.",
                 s({"horizon_cycles": {"type": "integer", "description": "Include components with RUL p10 at or below this many flights. Default 60."}}),
                 self.list_at_risk, "health"),
            Tool("get_component_health", "Health, anomaly score, RUL p10/p50/p90, data quality and top signals of one component.",
                 s({"component_id": {"type": "string", "description": "e.g. SIM-017/engine_2"}}, ["component_id"]),
                 self.get_component_health, "health"),
            Tool("get_sensor_trend", "Recent values of the sensors that drive a component's score.",
                 s({"component_id": {"type": "string"}, "last": {"type": "integer"}}, ["component_id"]),
                 self.get_sensor_trend, "health"),
            Tool("get_maintenance_history", "Last maintenance per component and the recent timeline of one aircraft.",
                 s({"tail": {"type": "string", "description": "e.g. SIM-017"}}, ["tail"]), self.get_maintenance_history, "health"),
            Tool("get_plan", "The current optimized plan or the static baseline: assignments with reasons, and metrics.",
                 s({"which": {"type": "string", "enum": ["current", "baseline"]}}), self.get_plan, "scheduling"),
            Tool("explain_priority", "Compare two aircraft: worst component, conservative RUL, and scheduled slot with the planner's reason.",
                 s({"tail_a": {"type": "string"}, "tail_b": {"type": "string"}}, ["tail_a", "tail_b"]),
                 self.explain_priority, "scheduling"),
            Tool("find_windows", "Planned maintenance and ground windows for today or tomorrow (simulated time).",
                 s({"day": {"type": "string", "enum": ["today", "tomorrow"]}, "limit": {"type": "integer"}}),
                 self.find_windows, "scheduling"),
            Tool("job_requirements", "Parts and technician skills needed by each planned job, and stock at the station.",
                 s({}), self.job_requirements, "logistics"),
            Tool("what_if_facility_closed", "Replan as if a station's hangar were closed. Does not change the real plan.",
                 s({"station": {"type": "string", "description": "IATA code, e.g. DFW"}}, ["station"]),
                 self.what_if_facility_closed, "operations"),
            Tool("compare_plans", "Metrics of the optimized plan against the static baseline, with the baseline defined.",
                 s({}), self.compare_plans, "operations"),
            Tool("draft_work_orders", "Draft work orders (always pending human approval) from the current plan, optionally for some aircraft.",
                 s({"tails": {"type": "array", "items": {"type": "string"}}}), self.draft_work_orders, "coordinator"),
            Tool("propose_request_edit", "Propose a change to a maintenance request that is waiting for a decision (name, work, part, quantity, whether the part must be ordered, "
                 "start and end as ISO UTC times). This does NOT change anything: the person is shown the proposal and must allow it. Use the work order id, for example WO-1.",
                 s({"work_order_id": {"type": "string"}, "title": {"type": "string"}, "work": {"type": "string"}, "part": {"type": "string"}, "part_qty": {"type": "integer"},
                    "order_part": {"type": "boolean"}, "start": {"type": "string"}, "end": {"type": "string"}}, ["work_order_id"]), self.propose_request_edit, "coordinator"),
            Tool("propose_new_request", "Propose a NEW maintenance request for an aircraft (station, start and end as ISO UTC times when the aircraft is on the ground there, name, work, reason, part). "
                 "This does NOT create anything: the person is shown the proposal and must allow it. Use find_windows first if you need a time when the aircraft is on the ground.",
                 s({"tail": {"type": "string"}, "station": {"type": "string"}, "start": {"type": "string"}, "end": {"type": "string"}, "title": {"type": "string"}, "work": {"type": "string"},
                    "why": {"type": "string"}, "part": {"type": "string"}, "part_qty": {"type": "integer"}, "order_part": {"type": "boolean"}},
                   ["tail", "station", "start", "end", "title"]), self.propose_new_request, "coordinator"),
            Tool("search_kb", "Search the synthetic maintenance task cards by keyword.",
                 s({"query": {"type": "string"}}, ["query"]), self.search_kb, "coordinator"),
            Tool("report_missing_info", "Call this when the data cannot answer something instead of guessing.",
                 s({"items": {"type": "array", "items": {"type": "string"}}}, ["items"]), self.report_missing_info, "coordinator"),
        ]
        return {x.name: x for x in t}
