"""Acceptance tests for feature 3.6 backend and the LLM loop (FEATURES-3.1-3.6.md)."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import copilot
from app.contracts.models import (
    Aircraft, Assignment, ChatRequest, ComponentState, DataQuality, FleetState, PartStock, Plan, PlanDiff, PlanMetrics,
    ReplanResult, RulEstimate, Station, TelemetryRow, Technician,
)
from app.copilot import audit, chat as chat_mod, kb, llm, persist, tools, workorders
from app.copilot.router import router
from app.copilot.tools import Context, Toolbox
from app.twin import state as twin

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


# ---- a small, hand-built world ----------------------------------------------
def comp(tail, name, kind, p10, p50, p90, health, status, anomaly=0.2):
    return ComponentState(
        tail=tail, component_id=f"{tail}/{name}", kind=kind, cycles=200, hours=360.0, health=health,
        anomaly_score=anomaly, rul_cycles=RulEstimate(p10=p10, p50=p50, p90=p90),
        data_quality=DataQuality(status="ok"), last_maintenance=None, status=status, updated=NOW)


def asg(task, tail, station, start_h, hours, tech, reason, part_source=None):
    s = NOW + timedelta(hours=start_h)
    return Assignment(task_id=task, tail=tail, station=station, start=s, end=s + timedelta(hours=hours),
                      technician_id=tech, part_source=part_source, window_hours=hours + 2.0, extension_h=0.0, reason=reason)


def metrics(down, flights, cost, sched=2, total=2):
    return PlanMetrics(downtime_h=down, flights_affected=flights, logistics_cost_usd=cost, tech_utilization=0.12,
                       hangar_utilization=0.08, schedule_changes=0, tasks_total=total, tasks_scheduled=sched,
                       late_or_unscheduled=0)


def make_world() -> Context:
    state = FleetState(
        now=NOW,
        aircraft=[Aircraft(tail=t, type="A320-class", home="ATL", location="DFW", status="ok", cycles=8000, hours=14000.0)
                  for t in ("SIM-017", "SIM-012", "SIM-005")],
        components=[
            comp("SIM-017", "engine_2", "engine", 18, 22, 25, 0.18, "alert", 0.91),
            comp("SIM-012", "engine_1", "engine", 47, 55, 63, 0.46, "watch", 0.30),
            comp("SIM-005", "hydraulics", "hydraulics", 35, 42, 50, 0.40, "watch"),
        ],
        legs=[],
        stations=[
            Station(iata="DFW", hangar_slots=2, lat=32.9, lon=-97.0,
                    technicians=[Technician(id="DFW-T1", skills=["engine"], shift_start=NOW, shift_end=NOW + timedelta(days=5))],
                    parts=[PartStock(part="BORESCOPE-KIT", qty=2)]),
            Station(iata="ATL", hangar_slots=2, lat=33.6, lon=-84.4,
                    technicians=[Technician(id="ATL-T1", skills=["engine"], shift_start=NOW, shift_end=NOW + timedelta(days=5))],
                    parts=[PartStock(part="BORESCOPE-KIT", qty=0, ship_from="DFW", ship_hours=18.0, ship_cost_usd=800.0)]),
        ],
    )
    current = Plan(id="P-2", created=NOW, mode="cpsat", feasible=True, solve_seconds=0.8, metrics=metrics(0.0, 0, 600.0),
                   assignments=[
                       asg("T-SIM-017-engine_2-001", "SIM-017", "DFW", 14.5, 6.0, "DFW-T1",
                           "Only window before the deadline with an engine technician and a kit on hand."),
                       asg("T-SIM-012-engine_1-001", "SIM-012", "DFW", 62.0, 6.0, "DFW-T1", "Later window, kit on hand."),
                   ])
    baseline = Plan(id="P-1", created=NOW, mode="static_baseline", feasible=True, solve_seconds=0.1,
                    metrics=metrics(46.0, 1, 2200.0),
                    assignments=[asg("T-SIM-017-engine_2-001", "SIM-017", "ATL", 60.0, 6.0, "ATL-T1", "Home base, last window.", "DFW")])
    return Context(state, current, baseline, "test world")


def fake_replan(state, plan, event):
    moved = plan.assignments[0]
    after = plan.model_copy(update={"id": "P-3", "assignments": [
        moved.model_copy(update={"station": "ATL", "start": moved.start + timedelta(hours=40), "end": moved.end + timedelta(hours=40)}),
        *plan.assignments[1:]], "metrics": metrics(12.0, 2, 1400.0)})
    diff = PlanDiff(moved=[{"task_id": moved.task_id, "from": {"station": "DFW", "start": moved.start},
                            "to": {"station": "ATL", "start": after.assignments[0].start},
                            "why": "DFW hangar closed; ATL is the next feasible station"}], added=[], dropped=[])
    return ReplanResult(event=event, before=plan, after=after, diff=diff, solve_seconds=0.1)


def box(world=None, **kw):
    w = world or make_world()
    return Toolbox(context_fn=lambda: w, replan_fn=fake_replan, **kw)


def recording(b: Toolbox):
    outs: list[str] = []
    for name in ("list_at_risk", "explain_priority", "find_windows", "job_requirements", "what_if_facility_closed",
                 "compare_plans", "draft_work_orders", "search_kb"):
        orig = getattr(b, name)

        def wrap(*a, _o=orig, **k):
            r = _o(*a, **k)
            outs.append(json.dumps(r, default=str))
            return r
        setattr(b, name, wrap)
    return outs


@pytest.fixture(autouse=True)
def clean():
    persist.set_persistence(False)          # memory backend unless a test opts in
    twin.reset()
    twin.set_now_provider(lambda: NOW)
    copilot.reset_all()
    kb.reload()
    yield
    twin.reset()
    twin.set_now_provider(None)
    copilot.reset_all()
    persist.set_persistence(None)


def ask(q, b=None, **kw):
    b = b or box()
    return chat_mod.chat(ChatRequest(session_id="s", message=q), toolbox=b, mode="scripted", **kw), b


# ---- the six example questions ------------------------------------------------
@pytest.mark.parametrize("q,must", [
    ("Which aircraft are most likely to require maintenance in the next 72 hours?", ["SIM-017", "engine_2", "p10 18"]),
    ("Why is aircraft SIM-017 prioritized over SIM-012?", ["SIM-017", "SIM-012", "comes before"]),
    ("Find the best feasible maintenance windows for tomorrow.", ["2026-10-13"]),
    ("Which parts and technicians are required for these maintenance jobs?", ["BORESCOPE-KIT", "DFW-T1"]),
    ("What changes if a maintenance facility becomes unavailable?", ["DFW", "what-if", "moves from"]),
    ("Compare the current schedule with the optimized schedule.", ["Baseline", "2200", "600"]),
])
def test_six_questions_have_evidence_and_only_tool_numbers(q, must):
    b = box()
    outs = recording(b)
    resp, _ = ask(q, b)
    assert resp.answer
    for token in must:
        assert token in resp.answer, (token, resp.answer)
    assert len(resp.evidence) >= 1
    assert llm.unsupported_numbers(resp.answer, outs + [q]) == []


def test_a17_alias_resolves():
    resp, _ = ask("Why is aircraft A17 prioritized over A12?")
    assert "SIM-017" in resp.answer and "SIM-012" in resp.answer


def test_priority_question_drafts_a_pending_work_order():
    resp, _ = ask("Why is SIM-017 prioritized over SIM-012?")
    assert resp.proposed_work_orders
    assert all(w.status == "pending_approval" for w in resp.proposed_work_orders)
    assert resp.proposed_work_orders[0].tail == "SIM-017"
    assert any(e.kind == "kb" for e in resp.proposed_work_orders[0].evidence)
    assert any(a.action == "work_order_drafted" for a in audit.list_entries())


def test_priority_is_honest_when_the_other_aircraft_comes_first():
    resp, _ = ask("Why is SIM-012 prioritized over SIM-017?")
    assert "not prioritized" in resp.answer
    assert resp.proposed_work_orders == []


def test_unanswerable_question_returns_missing_info_not_a_guess():
    resp, _ = ask("Which technician is on leave next week?")
    assert resp.missing_info
    assert "can't answer" in resp.answer
    assert resp.proposed_work_orders == []


def test_without_a_plan_the_copilot_says_what_is_missing():
    w = make_world()
    w.current = w.baseline = None
    resp, _ = ask("Compare the current schedule with the optimized schedule.", box(w))
    assert resp.missing_info
    assert "cannot compare" in resp.answer


def test_whatif_does_not_change_the_world():
    w = make_world()
    before = w.current.model_dump_json()
    ask("What if DFW becomes unavailable?", box(w))
    assert w.current.model_dump_json() == before


def test_unknown_question_falls_back_to_kb_or_refuses():
    resp, _ = ask("Tell me about borescope inspection")
    assert resp.missing_info                                     # outside the planning questions
    assert "TC-ENG-BORESCOPE" in resp.answer


# ---- knowledge base -----------------------------------------------------------
def test_kb_search():
    hits = kb.search_kb("borescope engine inspection")
    assert hits and hits[0]["doc_id"] == "TC-ENG-BORESCOPE"
    assert kb.search_kb("zzzz qqqq") == []
    assert kb.default_card_for("engine")["id"] == "TC-ENG-BORESCOPE"


# ---- number check ---------------------------------------------------------------
def test_extract_numbers_normalises():
    assert llm.extract_numbers("SIM-017 has RUL 45.90\n1. first") == ["17", "45.9"]
    assert llm.extract_numbers("1. item\n2) next 18 cycles") == ["18"]


def test_unsupported_numbers():
    assert llm.unsupported_numbers("RUL 18 at DFW", ['{"rul_p10": 18.0}']) == []
    assert llm.unsupported_numbers("RUL 19 at DFW", ['{"rul_p10": 18.0}']) == ["19"]


# ---- LLM loop with a fake client --------------------------------------------------
def block(type_, **kw):
    return SimpleNamespace(type=type_, **kw)


class FakeClient:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        nxt = self.script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def reply(*blocks, stop="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop)


def tool_use(name, inp, id_="tu_1"):
    return reply(block("text", text="Checking."), block("tool_use", id=id_, name=name, input=inp), stop="tool_use")


def test_llm_answer_with_supported_numbers_is_used():
    b = box()
    fc = FakeClient([tool_use("list_at_risk", {"horizon_cycles": 60}),
                     reply(block("text", text="SIM-017 engine_2 is the highest risk with RUL p10 18 and p50 22 cycles."))])
    resp = chat_mod.chat(ChatRequest(session_id="s", message="What is at risk?"), toolbox=b, client=fc, mode="llm")
    assert "SIM-017 engine_2" in resp.answer and "Highest risk first" not in resp.answer
    assert any(e.kind == "tool_call" and e.note == "list_at_risk" for e in resp.evidence)
    assert any(e.kind == "health" and e.ref == "SIM-017/engine_2" for e in resp.evidence)
    assert len(fc.calls) == 2


def test_llm_request_shape_for_sonnet_5_5():
    b = box()
    fc = FakeClient([reply(block("text", text="Nothing to report."))])
    chat_mod.chat(ChatRequest(session_id="s", message="hello"), toolbox=b, client=fc, mode="llm")
    kw = fc.calls[0]
    assert kw["model"] == "claude-sonnet-5-5"
    assert "tool_choice" not in kw          # forced tool use is rejected by this model
    assert "thinking" not in kw             # adaptive by default; disabled is rejected
    for k in ("temperature", "top_p", "top_k"):
        assert k not in kw
    assert kw["output_config"] == {"effort": "medium"}
    assert {t["name"] for t in kw["tools"]} >= {"list_at_risk", "search_kb", "report_missing_info"}
    assert all(t["input_schema"]["additionalProperties"] is False for t in kw["tools"])


def test_llm_invented_number_falls_back_to_scripted():
    b = box()
    fc = FakeClient([tool_use("list_at_risk", {}),
                     reply(block("text", text="SIM-017 engine_2 has RUL p10 37 cycles."))])
    resp = chat_mod.chat(ChatRequest(session_id="s", message="Which aircraft are at risk in the next 72 hours?"),
                         toolbox=b, client=fc, mode="llm")
    assert "37" not in resp.answer
    assert "Highest risk first" in resp.answer           # the scripted template


def test_llm_error_falls_back_to_scripted():
    import anthropic

    err = anthropic.APIConnectionError(request=SimpleNamespace(method="POST", url="x"))
    resp = chat_mod.chat(ChatRequest(session_id="s", message="Compare the current schedule with the optimized schedule."),
                         toolbox=box(), client=FakeClient([err]), mode="llm")
    assert "Baseline" in resp.answer


def test_llm_refusal_and_runaway_loops_are_errors():
    r = llm.run_tool_loop(FakeClient([reply(stop="refusal")]), system="s", messages=[{"role": "user", "content": "x"}], tools={})
    assert r.error and "refusal" in r.error
    loops = [tool_use("search_kb", {"query": "engine"}, f"tu_{i}") for i in range(3)]
    r = llm.run_tool_loop(FakeClient(loops), system="s", messages=[{"role": "user", "content": "x"}],
                          tools=box().tools(), max_steps=3)
    assert r.error and "no final answer" in r.error and len(r.calls) == 3


def test_unknown_tool_and_bad_arguments_return_error_results():
    fc = FakeClient([tool_use("does_not_exist", {}), tool_use("get_component_health", {"wrong": 1}, "tu_2"),
                     reply(block("text", text="Done."))])
    r = llm.run_tool_loop(fc, system="s", messages=[{"role": "user", "content": "x"}], tools=box().tools())
    assert r.text == "Done." and [c.ok for c in r.calls] == [False, False]
    sent = fc.calls[-1]["messages"][-1]["content"]
    assert sent[0]["is_error"] is True


def test_missing_info_tool_reaches_the_response():
    b = box()
    fc = FakeClient([tool_use("report_missing_info", {"items": ["no leave data"]}),
                     reply(block("text", text="I cannot tell."))])
    resp = chat_mod.chat(ChatRequest(session_id="s", message="Who is off?"), toolbox=b, client=fc, mode="llm")
    assert resp.missing_info == ["no leave data"]


def test_run_agent_emits_steps_and_swaps_in_the_template_on_bad_numbers():
    b = box()
    ok = FakeClient([tool_use("list_at_risk", {}), reply(block("text", text="SIM-017 engine_2 has RUL p10 18."))])
    res = llm.run_agent("health", "find risk", {"horizon": 60}, {"list_at_risk": b.tools()["list_at_risk"]},
                        client=ok, ts_fn=lambda: NOW, template="TEMPLATE")
    assert [s.kind for s in res.steps] == ["thought", "tool_call", "tool_result", "summary"]   # in the order they happened
    assert [s.n for s in res.steps] == [1, 2, 3, 4]
    assert res.numbers_ok and res.text.startswith("SIM-017")
    bad = FakeClient([tool_use("list_at_risk", {}), reply(block("text", text="RUL p10 99."))])
    res2 = llm.run_agent("health", "find risk", {}, {"list_at_risk": b.tools()["list_at_risk"]},
                         client=bad, ts_fn=lambda: NOW, template="TEMPLATE")
    assert not res2.numbers_ok and res2.text == "TEMPLATE" and res2.unsupported == ["99"]


def test_run_cache_roundtrip(tmp_path):
    c = llm.RunCache(tmp_path / "cache.json")
    k = c.key("goal", llm.state_hash(make_world().state))
    assert c.get(k) is None
    c.put(k, {"id": "R-1"})
    assert c.get(k) == {"id": "R-1"}


def test_llm_unavailable_without_credentials(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    assert llm.llm_available() is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    assert llm.llm_available() is True
    monkeypatch.setenv("COPILOT_MODE", "scripted")
    assert llm.llm_available() is False


# ---- work orders ------------------------------------------------------------------
def draft(world=None):
    w = world or make_world()
    return workorders.draft_from_plan(w.current)


def test_drafts_are_pending_and_audited():
    orders, _ = draft()
    assert [o.status for o in orders] == ["pending_approval"] * 2
    assert orders[0].description.startswith("Engine borescope inspection on SIM-017 at DFW")
    assert {e.kind for e in orders[0].evidence} == {"plan", "health", "kb"}
    assert [a.action for a in audit.list_entries()] == ["work_order_drafted"] * 2


def test_approve_requires_a_named_person_and_writes_audit():
    wo = draft()[0][0]
    for bad in ("", "  ", "system", "unknown", "agent:coordinator"):
        with pytest.raises(workorders.WorkOrderError) as e:
            workorders.decide(wo.id, "approve", bad)
        assert e.value.status == 422
    done = workorders.decide(wo.id, "approve", "Aashrith", "looks right")
    assert done.status == "approved" and done.decided_by == "Aashrith" and done.decided_at == NOW
    last = audit.list_entries()[0]                                   # newest first
    assert (last.actor, last.action, last.ref) == ("Aashrith", "work_order_approved", wo.id)
    with pytest.raises(workorders.WorkOrderError) as e:
        workorders.decide(wo.id, "reject", "Aashrith")
    assert e.value.status == 409
    with pytest.raises(workorders.WorkOrderError) as e:
        workorders.decide("WO-999", "approve", "Aashrith")
    assert e.value.status == 404


def test_approval_lands_on_the_twin_timeline():
    twin.register_aircraft(Aircraft(tail="SIM-017", type="A320-class", home="ATL", location="DFW", status="ok", cycles=1, hours=1.0))
    wo = draft()[0][0]
    workorders.decide(wo.id, "approve", "Aashrith")
    ev = twin.get_twin("SIM-017").timeline[0]
    assert ev.kind == "plan_change" and wo.id in ev.summary


def test_pending_order_is_superseded_by_a_newer_plan_and_redraft_is_idempotent():
    w = make_world()
    first, _ = workorders.draft_from_plan(w.current)
    again, _ = workorders.draft_from_plan(w.current)
    assert [o.id for o in again] == [o.id for o in first]
    newer = w.current.model_copy(update={"id": "P-3"})
    second, _ = workorders.draft_from_plan(newer)
    assert second[0].id != first[0].id
    old = workorders.get_order(first[0].id)
    assert old.status == "rejected" and old.decided_by == "system" and "superseded" in old.note
    assert any(a.action == "work_order_superseded" for a in audit.list_entries())


def test_an_approved_order_is_not_replaced_by_a_later_slot_but_is_by_an_earlier_one():
    w = make_world()
    wo = workorders.draft_from_plan(w.current)[0][0]
    workorders.decide(wo.id, "approve", "Aashrith")
    later = w.current.model_copy(update={"id": "P-3", "assignments": [
        a.model_copy(update={"start": a.start + timedelta(hours=10), "end": a.end + timedelta(hours=10)})
        for a in w.current.assignments]})
    out, notes = workorders.draft_from_plan(later)
    assert out[0].id == wo.id and out[0].status == "approved"
    assert any("already approved" in n for n in notes)
    earlier = w.current.model_copy(update={"id": "P-4", "assignments": [
        a.model_copy(update={"start": a.start - timedelta(hours=5), "end": a.end - timedelta(hours=5)})
        for a in w.current.assignments]})
    out2, _ = workorders.draft_from_plan(earlier)
    assert out2[0].id != wo.id and out2[0].status == "pending_approval"
    assert workorders.get_order(wo.id).status == "approved"


def test_completion_resets_the_twin_component():
    twin.register_aircraft(Aircraft(tail="SIM-017", type="A320-class", home="ATL", location="DFW", status="ok", cycles=1, hours=1.0))
    twin.seed_components([comp("SIM-017", "engine_2", "engine", 18, 22, 25, 0.18, "alert", 0.91)])
    wo = draft()[0][0]
    workorders.decide(wo.id, "approve", "Aashrith")
    assert workorders.complete_due(wo.start) == []                   # window not over
    done = workorders.complete_due(wo.end + timedelta(minutes=1))
    assert [d.id for d in done] == [wo.id]
    c = twin.get_twin("SIM-017").components[0]
    assert c.health == 1.0 and c.last_maintenance == wo.end and c.status == "ok"
    assert audit.list_entries()[0].action == "work_order_completed"
    assert workorders.complete_due(wo.end + timedelta(hours=1)) == []   # only once


# ---- HTTP ---------------------------------------------------------------------------
@pytest.fixture
def client(monkeypatch):
    world = make_world()
    monkeypatch.setattr(tools, "default_context", lambda: world)
    monkeypatch.setenv("COPILOT_MODE", "scripted")
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_http_chat_workorders_decision_audit(client):
    r = client.post("/copilot/chat", json={"session_id": "s1", "message": "Why is SIM-017 prioritized over SIM-012?"})
    assert r.status_code == 200
    body = r.json()
    assert body["synthetic"] is True and body["proposed_work_orders"][0]["status"] == "pending_approval"
    wid = body["proposed_work_orders"][0]["id"]
    assert [w["id"] for w in client.get("/workorders").json()] == [wid]
    d = client.post(f"/workorders/{wid}/decision", json={"decision": "approve", "user": "Aashrith", "note": "ok"})
    assert d.status_code == 200 and d.json()["status"] == "approved"
    actions = [a["action"] for a in client.get("/audit").json()]      # newest first
    assert actions == ["work_order_approved", "copilot_answer", "work_order_drafted"]
    again = client.post(f"/workorders/{wid}/decision", json={"decision": "reject", "user": "Aashrith"})
    assert again.status_code == 409 and set(again.json()) == {"error", "detail"}
    agent = client.post(f"/workorders/{wid}/decision", json={"decision": "approve", "user": "agent:coordinator"})
    assert agent.status_code == 422 or agent.status_code == 409


def test_http_quick_prompts_are_the_six_questions(client):
    assert len(client.get("/copilot/quick-prompts").json()["prompts"]) == 6


# ---- agent-drafted orders share this store -----------------------------------------
def test_draft_matches_the_agent_registry_call_shape_and_enriches_from_tasks():
    from app.contracts.models import Task

    w = make_world()
    task = Task(id="T-SIM-017-engine_2-001", tail="SIM-017", component_id="SIM-017/engine_2",
                title="Engine borescope inspection", duration_h=6.0, earliest=NOW, deadline=NOW + timedelta(days=3),
                required_skill="engine", required_part="BORESCOPE-KIT", source="rul",
                basis="engine_2 RUL p10 = 18 cycles (conservative, hard deadline)")
    orders = workorders.draft(w.current, w.state, [task])           # same call as app.agents.workorders.draft
    first = next(o for o in orders if o.task_id == task.id)
    assert "Why: engine_2 RUL p10 = 18 cycles" in first.description
    assert first.status == "pending_approval"
    assert {e.kind for e in first.evidence} == {"plan", "health", "kb"}
    assert workorders.get_order(first.id) is not None               # visible to /workorders and the decision endpoint


# ---- database backend ---------------------------------------------------------------
@pytest.fixture
def dbmode(tmp_path, monkeypatch):
    from app import db, settings

    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "t.db")
    db.init_db(drop=True)
    db.set_sim_now(NOW)
    persist.set_persistence(True)
    yield db
    persist.set_persistence(False)


def test_db_backend_persists_orders_audit_and_survives_a_restart(dbmode):
    w = make_world()
    first, _ = workorders.draft_from_plan(w.current)
    workorders.decide(first[0].id, "approve", "Aashrith", "ok")
    workorders.reset()                                  # forget process memory: a restart
    audit.reset()
    again = workorders.list_orders()
    assert [o.id for o in again] == [o.id for o in first]
    assert workorders.get_order(first[0].id).status == "approved"
    assert [a.action for a in audit.list_entries()][:1] == ["work_order_approved"]
    assert audit.list_entries()[0].ts == NOW                         # the DB clock
    nxt, _ = workorders.draft_from_plan(w.current.model_copy(update={
        "id": "P-9", "assignments": [w.current.assignments[0].model_copy(update={"task_id": "T-SIM-005-hydraulics-001", "tail": "SIM-005"})]}))
    assert nxt[0].id == "WO-3"                                       # continues after the stored ids
    with dbmode.conn() as c:
        assert c.execute("SELECT COUNT(*) FROM work_orders").fetchone()[0] == 3


def test_db_backend_completion_is_remembered(dbmode):
    twin.register_aircraft(Aircraft(tail="SIM-017", type="A320-class", home="ATL", location="DFW", status="ok", cycles=1, hours=1.0))
    twin.seed_components([comp("SIM-017", "engine_2", "engine", 18, 22, 25, 0.18, "alert", 0.91)])
    wo = workorders.draft_from_plan(make_world().current)[0][0]
    workorders.decide(wo.id, "approve", "Aashrith")
    assert [d.id for d in workorders.complete_due(wo.end + timedelta(minutes=1))] == [wo.id]
    workorders.reset()
    assert workorders.complete_due(wo.end + timedelta(hours=2)) == []   # not completed twice after a restart


def test_scenario_reset_clearing_the_tables_clears_the_copilot(dbmode):
    workorders.draft_from_plan(make_world().current)
    assert workorders.list_orders()
    with dbmode.conn() as c:                            # what the simulator's reset does
        c.execute("DELETE FROM work_orders")
        c.execute("DELETE FROM audit")
    assert workorders.list_orders() == [] and audit.list_entries() == []


def test_metrics_block_has_n_test():
    from app.ml import health

    assert health.ml_metrics_block()["n_test"] == 100
