"""In-memory state behind the MOCK routers, so the whole UI flow works with no other module: inject an anomaly -> run agents -> approve -> delay a part -> replan.
All data comes from fixtures/ (synthetic shape examples, not results)."""
from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timedelta, timezone

from .. import fixtures
from ..contracts.models import *  # noqa: F401,F403

UTC = timezone.utc
T0 = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
KINDS = [("engine_1", "engine"), ("engine_2", "engine"), ("brakes", "brakes"), ("landing_gear", "landing_gear"), ("hydraulics", "hydraulics"), ("avionics", "avionics")]


def generic_twin(summary: AircraftSummary) -> TwinView:
    """A plausible healthy twin for any tail that has no hand-made fixture."""
    r = random.Random(summary.tail)
    comps = []
    for cid, kind in KINDS:
        worst = summary.worst_component == f"{summary.tail}/{cid}"
        health = round(r.uniform(0.3, 0.45), 2) if worst else round(r.uniform(0.72, 0.95), 2)
        rul = RulEstimate(p10=summary.rul_p10_min or 80, p50=(summary.rul_p10_min or 80) + 8, p90=(summary.rul_p10_min or 80) + 18) if worst else RulEstimate(p10=70, p50=85, p90=100)
        cyc = 230 + r.randint(0, 400)
        hist = [HistoryPoint(cycle=cyc - 29 + i, ts=T0 - timedelta(hours=6 * (29 - i)), health=round(min(1, health + (29 - i) * 0.004), 3), anomaly_score=0.05, rul_p50=rul.p50 + (29 - i)) for i in range(30)]
        pred = [PredictedPoint(cycle=cyc + j, health_p50=round(max(0, health * (1 - j / rul.p50)), 3), health_lo=round(max(0, health * (1 - j / rul.p10)), 3),
                               health_hi=round(max(0, health * (1 - j / rul.p90)), 3)) for j in range(1, 21)]
        comps.append(TwinComponent(tail=summary.tail, component_id=f"{summary.tail}/{cid}", kind=kind, cycles=cyc, hours=round(cyc * 1.77, 1), health=health, anomaly_score=0.1 if worst else 0.05,
                                   rul_cycles=rul, data_quality=DataQuality(status="ok"), status="watch" if worst else "ok", updated=T0, history=hist, predicted=pred,
                                   sensors_last={"T50": 1405.0, "Nc": 9048.0, "Ps30": 47.4} if kind == "engine" else {}))
    return TwinView(aircraft=Aircraft(tail=summary.tail, type=summary.type, home=summary.home, location=summary.location, status=summary.status, cycles=5000 + int(summary.tail[-3:]) * 97,
                                      hours=9000.0 + int(summary.tail[-3:]) * 213.1), components=comps,
                    timeline=[TwinEvent(ts=T0 - timedelta(days=1), kind="flight", summary="Normal rotation")])


class MockState:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.now = T0
            self.fleet = fixtures.load("fleet", FleetOverview)
            self.twins: dict[str, TwinView] = {"SIM-017": fixtures.load("twin_SIM-017_pre", TwinView)}
            self.plan_current = fixtures.load("plan_current", Plan)
            self.plan_baseline = fixtures.load("plan_baseline", Plan)
            self.workorders: list[WorkOrder] = []
            self.audit: list[AuditEntry] = fixtures.load("audit", list[AuditEntry])
            self.runs: dict[str, tuple[float, AgentRun]] = {}
            self.counters = {"run": 0, "event": 2, "plan": 3}
            self.replanned = False

    # ---- helpers
    def log(self, actor: str, action: str, ref: str, detail: str = "") -> None:
        self.audit.append(AuditEntry(ts=self.now, actor=actor, action=action, ref=ref, detail=detail))

    def fleet_overview(self) -> FleetOverview:
        with self.lock:
            f = self.fleet.model_copy(deep=True)
            ac = f.aircraft
            tasks = fixtures.load("tasks_golden", list[Task])
            f.kpis = FleetKpis(aircraft=len(ac), needing_attention=sum(1 for a in ac if a.status in ("watch", "alert")),
                               predicted_maintenance_72h=sum(1 for t in tasks if t.deadline <= self.now + timedelta(hours=72)),
                               in_maintenance=sum(1 for a in ac if a.status == "in_maintenance"),
                               readiness_pct=round(100 * sum(1 for a in ac if a.status not in ("in_maintenance", "aog")) / max(1, len(ac)), 1))
            return f

    def twin(self, tail: str) -> TwinView | None:
        with self.lock:
            if tail in self.twins:
                return self.twins[tail].model_copy(deep=True)
            s = next((a for a in self.fleet.aircraft if a.tail == tail), None)
            if s is None:
                return None
            self.twins[tail] = generic_twin(s)
            return self.twins[tail].model_copy(deep=True)

    # ---- scenario injection
    def inject(self, ev: ScenarioEvent) -> dict:
        with self.lock:
            self.counters["event"] += 1
            eid = ev.id or f"E-{self.counters['event']}"
            effects: list[str] = []
            changes: list[str] = []
            if ev.type in ("anomaly", "accelerated_degradation") and ev.tail:
                s = next((a for a in self.fleet.aircraft if a.tail == ev.tail), None)
                if s is None:
                    return {"event_id": eid, "effects": [f"unknown tail {ev.tail}"], "twin_changes": []}
                if ev.tail == "SIM-017":
                    self.twins["SIM-017"] = fixtures.load("twin_SIM-017", TwinView)
                    rul = 18.0
                else:
                    tv = self.twin(ev.tail)
                    comp = next((c for c in tv.components if ev.component_id and c.component_id == ev.component_id), tv.components[1 if len(tv.components) > 1 else 0])
                    comp.status, comp.health, comp.anomaly_score = "alert", 0.22, 0.85
                    comp.rul_cycles = RulEstimate(p10=16, p50=20, p90=24)
                    tv.aircraft.status = "alert"
                    tv.timeline.insert(0, TwinEvent(ts=self.now, kind="fault_injected", summary=f"{comp.component_id} anomaly injected", ref=eid))
                    self.twins[ev.tail] = tv
                    rul = 16.0
                s.status, s.worst_component, s.rul_p10_min = "alert", ev.component_id or f"{ev.tail}/engine_2", rul
                effects.append(f"{ev.tail} is now ALERT; RUL p10 {rul:g} cycles")
                changes.append(ev.component_id or f"{ev.tail}/engine_2")
            else:
                effects.append(f"{ev.type} recorded ({ev.station or ''} {ev.part or ev.technician_id or ''}); use POST /plan/replan to recompute the plan")
            self.log("system", "scenario_injected", eid, f"{ev.type} {ev.tail or ev.station or ''}")
            return {"event_id": eid, "effects": effects, "twin_changes": changes}

    # ---- planning
    def replan(self, ev: ScenarioEvent) -> ReplanResult:
        with self.lock:
            res = fixtures.load("replan_result", ReplanResult)
            self.counters["event"] += 1
            self.counters["plan"] += 1
            ev = ev.model_copy(update={"id": ev.id or f"E-{self.counters['event']}"})
            before = self.plan_current.model_copy(deep=True)
            after = res.after.model_copy(deep=True)
            after.id = f"P-{self.counters['plan']}"
            if self.replanned:  # second replan: nothing new to move in the fixture
                after = before.model_copy(update={"id": after.id})
            if ev.type != "part_delay":
                why = {"facility_closed": f"{ev.station or 'The station'} hangar is closed; the job moves to a window at a station with a free hangar.",
                       "tech_unavailable": "The technician is unavailable; another qualified technician takes the job.",
                       "anomaly": "A new anomaly shortened a deadline; tasks were re-ordered.", "accelerated_degradation": "Faster degradation moved a deadline earlier."}.get(ev.type, "Re-solved.")
                for m in res.diff.moved:
                    m["why"] = why
            self.plan_current = after
            self.replanned = True
            for w in self.workorders:
                if w.task_id in {m["task_id"] for m in res.diff.moved}:
                    w.status, w.plan_id = "pending_approval", after.id
            self.log("system", "plan_change", after.id, f"replanned after {ev.type}")
            return ReplanResult(event=ev, before=before, after=after, diff=res.diff, solve_seconds=res.solve_seconds)

    def compare(self, scenarios: list[Scenario]) -> Comparison:
        with self.lock:
            rows = []
            for sc in scenarios:
                m = self.plan_current.metrics.model_copy(deep=True)
                notes = []
                for ev in sc.events:
                    if ev.type == "part_delay":
                        m.schedule_changes += 1
                        notes.append("job moves one night later")
                    elif ev.type == "facility_closed":
                        m.downtime_h += 2.5
                        m.flights_affected += 1
                        notes.append("one departure delayed 2.5 h")
                    elif ev.type == "tech_unavailable":
                        m.schedule_changes += 1
                        m.tech_utilization = min(1.0, m.tech_utilization + 0.01)
                    elif ev.type in ("anomaly", "accelerated_degradation"):
                        m.schedule_changes += 2
                        m.tasks_total += 1
                        m.tasks_scheduled += 1
                if sc.constraints.get("force_station") and sc.constraints["force_station"] != "DFW":
                    m.logistics_cost_usd += 800
                    notes.append(f"kit shipped to {sc.constraints['force_station']}")
                if sc.constraints.get("earliest"):
                    notes.append("not before the requested time")
                rows.append({"name": sc.name, "feasible": True, "metrics": m.model_dump(mode="json"), "notes": notes, "source": "mock"})
            return Comparison(rows=rows)

    # ---- agents + work orders
    def start_run(self, goal: str, mode: str) -> AgentRun:
        with self.lock:
            self.counters["run"] += 1
            run = fixtures.load("agent_run_golden", AgentRun)
            run.id, run.goal, run.mode = f"R-{self.counters['run']}", goal, "scripted" if mode != "llm" else "scripted"  # mock never calls an LLM
            self.runs[run.id] = (time.monotonic(), run)
            self.log("agent:coordinator", "agent_run_started", run.id, goal)
            return run.model_copy(update={"status": "running", "steps": [], "plan": None, "work_orders": [], "explanation": ""})

    def get_run(self, rid: str) -> AgentRun | None:
        with self.lock:
            if rid not in self.runs:
                return None
            t0, full = self.runs[rid]
            k = int((time.monotonic() - t0) / 0.6) + 1
            if k < len(full.steps):
                return full.model_copy(update={"status": "running", "steps": full.steps[:k], "plan": None, "work_orders": [], "explanation": ""})
            if not self.workorders:
                self.workorders = fixtures.load("workorders", list[WorkOrder])
                self.log("agent:coordinator", "work_orders_drafted", rid, f"{len(self.workorders)} pending approval")
            return full.model_copy(update={"status": "done", "plan": self.plan_current, "work_orders": [w.model_copy() for w in self.workorders]})

    def decide(self, wid: str, decision: str, user: str, note: str) -> WorkOrder | None:
        with self.lock:
            w = next((x for x in self.workorders if x.id == wid), None)
            if w is None:
                return None
            w.status = "approved" if decision == "approve" else "rejected"
            w.decided_by, w.decided_at, w.note = user, self.now, note
            self.log(user, f"work_order_{w.status}", wid, note)
            return w.model_copy(deep=True)

    def chat(self, msg: str) -> ChatResponse:
        m = msg.lower()
        for ex in fixtures.raw("chat_examples"):
            if any(k in m for k in ex["match"]):
                resp = ChatResponse.model_validate(ex["response"])
                if resp.proposed_work_orders and not self.workorders:
                    self.workorders = fixtures.load("workorders", list[WorkOrder])
                self.log("agent:coordinator", "copilot_answer", "chat", msg[:80])
                return resp
        return ChatResponse(answer="I can answer questions about at-risk aircraft, priorities, maintenance windows, parts and technicians, facility outages and plan comparisons. (Mock copilot.)",
                            evidence=[], missing_info=["No mock answer for that question; the real copilot calls the same tools."])


STATE = MockState()
