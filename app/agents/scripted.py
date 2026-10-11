"""Scripted agent workflow (feature 3.4, no LLM): Health -> Scheduling -> Logistics -> Operations -> Coordinator.

The same tools an LLM would call, in a fixed order, writing the same ``AgentStep``s (thought, tool_call, tool_result, summary).
The explanation is a template filled ONLY with values returned by tools. Work orders are drafted as ``pending_approval``;
nothing here approves anything. Times on steps are simulated time (the snapshot's ``now``), never the wall clock.
"""
from __future__ import annotations

from typing import Optional

from app.agents.runs import RUNS, RunStore
from app.agents.tools import call_tool
from app.contracts.models import AgentRun, AgentStep, EvidenceRef, Plan, WorkOrder
from app.solver import service
from app.solver.common import fmt

MAX_FOCUS = 3        # components / aircraft investigated in detail


class _Run:
    def __init__(self, run_id: str, store: RunStore, now) -> None:
        self.id, self.store, self.now = run_id, store, now
        self.n = 0
        self.missing: list[str] = []
        self.refs: dict[str, int] = {}        # tool name -> step number of its tool_result (for work-order evidence)

    def step(self, agent: str, kind: str, text: str = "", tool: Optional[str] = None, input: Optional[dict] = None,
             output: Optional[dict] = None) -> int:
        self.n += 1
        self.store.append(self.id, AgentStep(n=self.n, agent=agent, kind=kind, tool=tool, input=input, output=output, text=text,  # type: ignore[arg-type]
                                             ts=self.now))
        return self.n

    def tool(self, agent: str, name: str, args: Optional[dict] = None, thought: str = "", summarize=None) -> dict:
        if thought:
            self.step(agent, "thought", thought)
        self.step(agent, "tool_call", tool=name, input=args or {}, text=f"call {name}")
        res = call_tool(name, args)
        for m in res.get("missing_info", []):
            if m not in self.missing:
                self.missing.append(m)
        text = f"{name} failed: {res['error']}" if res.get("error") else (summarize(res["data"]) if summarize else f"{name} ok")
        self.refs[name] = self.step(agent, "tool_result", text=text, tool=name, input=args or {}, output=res)
        return res


def run_scripted(run_id: str, goal: str, scope: Optional[dict] = None, store: RunStore = RUNS) -> AgentRun:
    scope = scope or {}
    state, _ = service.get_state()
    r = _Run(run_id, store, state.now)
    tails = scope.get("tails") or None
    horizon_h = float(scope.get("horizon_h") or 120.0)

    # ---------------------------------------------------------------- Health
    h = "health"
    risk = r.tool(h, "list_at_risk", {"horizon_cycles": 60, **({"tails": tails} if tails else {})},
                  "Looking for components whose conservative RUL (p10) is below 60 cycles, or that are in alert.",
                  lambda d: f"{len(d)} component(s) at risk" + (f"; most urgent {d[0]['component_id']} (RUL p10 {d[0]['rul_p10']:g} cycles, health {d[0]['health']:g})" if d else ""))
    at_risk = risk["data"] or []
    for row in at_risk[:MAX_FOCUS]:
        cid = row["component_id"]
        r.tool(h, "get_component_health", {"component_id": cid}, f"Checking {cid} in detail.",
               lambda d: f"{d['component_id']}: health {d['health']:g}, anomaly score {d['anomaly_score']:g}, RUL p10/p50/p90 "
                         + ("/".join(f"{d['rul_cycles'][k]:g}" for k in ("p10", "p50", "p90")) if d.get("rul_cycles") else "unknown"))
        r.tool(h, "get_sensor_trend", {"component_id": cid, "last": 30}, f"Fetching the recent sensor trend of {cid}.",
               lambda d: f"{len(d.get('history', []))} history point(s)" if isinstance(d, dict) else "no sensor trend available")
    for tail in sorted({row["tail"] for row in at_risk[:MAX_FOCUS]})[:2]:
        r.tool(h, "get_maintenance_history", {"tail": tail}, f"Retrieving the maintenance history of {tail}.",
               lambda d: f"{len(d)} maintenance record(s)")
    r.step(h, "summary", f"{len(at_risk)} component(s) need attention: " + (", ".join(x["component_id"] for x in at_risk[:5]) or "none") + ".")

    # ---------------------------------------------------------------- Scheduling
    s = "scheduling"
    dt = r.tool(s, "derive_tasks", {}, "Turning the risks into maintenance tasks with hard deadlines (conservative p10).",
                lambda d: f"{len(d)} task(s); earliest deadline {fmt_iso(d[0]['deadline'])} ({d[0]['id']})" if d else "no tasks")
    tasks = dt["data"] or []
    for tail in sorted({t["tail"] for t in tasks if t["source"] == "rul"})[:MAX_FOCUS]:
        r.tool(s, "find_ground_windows", {"tail": tail, "horizon_h": horizon_h}, f"Finding the ground windows of {tail}.",
               lambda d: f"{len(d)} ground window(s)")
    opt = r.tool(s, "solve_schedule", {"mode": "cpsat"}, "Solving with the constraint solver (CP-SAT).",
                 lambda d: f"plan {d['id']} ({d['mode']}), feasible={d['feasible']}, {d['metrics']['tasks_scheduled']}/{d['metrics']['tasks_total']} tasks scheduled")
    base = r.tool(s, "solve_schedule", {"mode": "static_baseline"}, "Solving the baseline: home base, last window before the deadline.",
                  lambda d: f"baseline {d['id']}, feasible={d['feasible']}")
    r.tool(s, "compare_plans", {"names": ["baseline", "greedy", "optimized"]}, "Comparing baseline, greedy and optimized on the same snapshot.",
                  lambda d: "; ".join(f"{x['name']}: logistics ${x['metrics']['logistics_cost_usd']:g}" for x in d["rows"]))
    r.step(s, "summary", f"Optimized plan {opt['data']['id']} is {'feasible' if opt['data']['feasible'] else 'NOT feasible'}; baseline {base['data']['id']} compared.")
    plan = Plan.model_validate(opt["data"])
    by_task = {t["id"]: t for t in tasks}

    # ---------------------------------------------------------------- Logistics
    lg = "logistics"
    for a in sorted(plan.assignments, key=lambda x: (x.start, x.task_id))[:6]:
        t = by_task.get(a.task_id)
        if t and t.get("required_part"):
            r.tool(lg, "check_parts", {"station": a.station, "part": t["required_part"]}, f"Checking {t['required_part']} at {a.station}.",
                   lambda d: f"on hand {d['on_hand']}, ship from {d['ship_from']}" if d["ship_from"] else f"on hand {d['on_hand']}")
        if t:
            r.tool(lg, "match_technicians", {"station": a.station, "skill": t["required_skill"], "start": a.start.isoformat(), "end": a.end.isoformat()},
                   f"Matching a {t['required_skill']} technician at {a.station}.", lambda d: f"{len(d)} technician(s) match")
    short = r.tool(lg, "shortage_report", {}, "Listing shortages and parts that must be shipped.", lambda d: f"{len(d)} shortage item(s)")
    r.step(lg, "summary", f"{len(short['data'] or [])} shortage item(s).")

    # ---------------------------------------------------------------- Operations
    op = "operations"
    sim = r.tool(op, "simulate_plan", {"plan_id": plan.id}, "Evaluating the plan's operational impact.",
                 lambda d: f"downtime {d['downtime_h']:g} h, flights affected {d['flights_affected']}, logistics ${d['logistics_cost_usd']:g}")
    r.tool(op, "flights_affected", {"plan_id": plan.id}, "Listing the flights the plan delays.", lambda d: f"{len(d)} flight(s) delayed")
    r.step(op, "summary", f"Plan {plan.id}: {sim['data']['flights_affected']} flight(s) affected, downtime {sim['data']['downtime_h']:g} h.")

    # ---------------------------------------------------------------- Coordinator
    co = "coordinator"
    wo = r.tool(co, "draft_work_orders", {"plan_id": plan.id}, "Drafting work orders for human approval.", lambda d: f"{len(d)} work order(s) pending approval")
    orders = [WorkOrder.model_validate(x) for x in (wo["data"] or [])]
    for o in orders:      # tie every work order back to the steps that produced it
        for tool in ("derive_tasks", "solve_schedule", "compare_plans"):
            if tool in r.refs:
                o.evidence.append(EvidenceRef(kind="tool_call", ref=f"{run_id}/step-{r.refs[tool]}", note=tool))
    first_rul = next((t for t in tasks if t["source"] == "rul"), tasks[0] if tasks else None)
    r.tool(co, "search_kb", {"query": first_rul["title"] if first_rul else "inspection"}, "Looking up the task card for the most urgent job.",
           lambda d: f"{len(d)} card(s): " + ", ".join(x["doc_id"] for x in d))
    explanation = _explain(plan, tasks, at_risk, base["data"], short["data"] or [], orders, first_rul)
    r.step(co, "summary", explanation)

    store.add_work_orders(run_id, orders)
    store.update(run_id, plan=plan, explanation=explanation, missing_info=list(r.missing), status="done")
    return store.get(run_id)  # type: ignore[return-value]


def fmt_iso(iso: str) -> str:
    from datetime import datetime
    return fmt(datetime.fromisoformat(iso.replace("Z", "+00:00")))


def _explain(plan: Plan, tasks: list[dict], at_risk: list[dict], base: dict, short: list[dict], orders: list[WorkOrder],
             first_rul: Optional[dict]) -> str:
    """Template text. Every number below is copied from a tool result."""
    m = plan.metrics
    out = [f"Plan {plan.id} ({plan.mode}) is {'feasible' if plan.feasible else 'NOT feasible'}: {m.tasks_scheduled} of {m.tasks_total} tasks scheduled, "
           f"{m.late_or_unscheduled} late or unscheduled."]
    if first_rul:
        a = next((x for x in plan.assignments if x.task_id == first_rul["id"]), None)
        risk = next((x for x in at_risk if x["component_id"] == first_rul["component_id"]), None)
        head = f"Most urgent: {first_rul['tail']} {first_rul['component_id'].split('/')[-1]}"
        if risk and risk["rul_p10"] is not None:
            head += f", RUL p10 {risk['rul_p10']:g} cycles"
        head += f", hard deadline {fmt_iso(first_rul['deadline'])}."
        if a:
            head += (f" Scheduled at {a.station} {fmt(a.start)} to {fmt(a.end)} with technician {a.technician_id}, "
                     f"extension {a.extension_h:g} h" + (f", part shipped from {a.part_source}" if a.part_source else ", part on hand or not needed") + ".")
        else:
            head += " It could not be scheduled: " + next((r for r in plan.infeasible_reasons if first_rul["id"] in r), "see the plan's reasons") + "."
        out.append(head)
    bm = base["metrics"]
    out.append(f"Baseline (home base, last window before the deadline): logistics ${bm['logistics_cost_usd']:g}, downtime {bm['downtime_h']:g} h, "
               f"flights affected {bm['flights_affected']}. Optimized: logistics ${m.logistics_cost_usd:g}, downtime {m.downtime_h:g} h, "
               f"flights affected {m.flights_affected}. Savings are simulated relative to that baseline.")
    conflicts = [x["detail"] for x in short]
    if conflicts:
        out.append("Logistics flags: " + "; ".join(conflicts) + ".")
    if not plan.feasible:
        out.append("Infeasible: " + " | ".join(plan.infeasible_reasons))
    out.append(f"{len(orders)} work order(s) drafted, all pending human approval. Advisory only; this does not replace approved procedures.")
    return " ".join(out)
