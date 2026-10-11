"""Acceptance tests for feature 3.4, scripted mode (FEATURES-3.1-3.6.md): status done, >= 3 distinct agents with real tool calls,
>= 1 work order, every work order and recommendation has evidence, and every number in the explanation comes from a tool output.
Run: pytest -q tests/test_agents.py"""
from __future__ import annotations

import json
import re
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.agents.router import router as agents_router
from app.agents.runner import LLM_NOTE, run_workflow
from app.agents.runs import RUNS
from app.agents.tools import TOOLS, call_tool
from app.contracts.models import AgentRun, EvidenceRef
from app.solver import service
from app.solver.golden import golden_state
from app.solver.store import STORE

CONTRACT_TOOLS = {  # CONTRACTS.md section 6: agent -> tools
    "health": {"list_at_risk", "get_component_health", "get_sensor_trend", "get_maintenance_history"},
    "scheduling": {"find_ground_windows", "derive_tasks", "solve_schedule", "compare_plans"},
    "logistics": {"check_parts", "match_technicians", "shortage_report", "suggest_repositioning"},
    "operations": {"simulate_plan", "flights_affected", "replan_with_event"},
    "coordinator": {"draft_work_orders", "search_kb"},
}
GOAL = "Plan maintenance for aircraft needing attention"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("MOCK_SOLVER", raising=False)
    STORE.reset()
    RUNS.reset()
    _reset_copilot_orders()
    yield
    STORE.reset()
    RUNS.reset()


def _reset_copilot_orders() -> None:
    try:    # once the copilot exists it owns the work-order store; start each test from an empty one
        from app.copilot import workorders as store
        store.reset()
    except ImportError:
        pass


@pytest.fixture(scope="module")
def golden_run():
    STORE.reset()
    RUNS.reset()
    _reset_copilot_orders()
    run = run_workflow(GOAL, {}, "scripted")
    yield run
    STORE.reset()
    RUNS.reset()


def numbers(text: str) -> set[float]:
    return {float(x) for x in re.findall(r"\d+(?:\.\d+)?", text.replace(",", ""))}


# ------------------------------------------------------------------ registry
def test_registry_has_the_sixteen_contract_tools_plus_repositioning_with_valid_schemas():
    assert {t for ts in CONTRACT_TOOLS.values() for t in ts} == set(TOOLS)
    for name, spec in TOOLS.items():
        assert name in CONTRACT_TOOLS[spec.agent]
        assert spec.description and spec.input_schema["type"] == "object"
        json.dumps(spec.input_schema)           # JSON-schema serializable (an LLM tool loop will need it)


def test_every_tool_returns_data_evidence_and_missing_info():
    args = {"get_component_health": {"component_id": "SIM-017/engine_2"}, "get_sensor_trend": {"component_id": "SIM-017/engine_2"},
            "get_maintenance_history": {"tail": "SIM-017"}, "find_ground_windows": {"tail": "SIM-017"},
            "check_parts": {"station": "DFW", "part": "BORESCOPE-KIT"},
            "match_technicians": {"station": "DFW", "skill": "engine", "start": "2026-10-12T14:30:00Z", "end": "2026-10-12T20:30:00Z"},
            "search_kb": {"query": "borescope"}}
    for name in TOOLS:
        if name == "replan_with_event":
            continue                             # changes the world; covered separately
        res = call_tool(name, args.get(name, {}))
        assert res.get("error") is None, (name, res.get("error"))
        EvidenceRef.model_validate(res["evidence"])
        assert isinstance(res["missing_info"], list)
        json.dumps(res["data"])                  # JSON-serializable: no numpy / datetime leaks


def test_tool_failures_are_results_not_exceptions():
    assert "unknown tool" in call_tool("nope")["error"]
    assert "unknown plan id" in call_tool("simulate_plan", {"plan_id": "P-999"})["error"]
    assert "unknown component" in call_tool("get_component_health", {"component_id": "SIM-999/x"})["error"]
    assert "unknown station" in call_tool("check_parts", {"station": "XXX", "part": "P"})["error"]
    assert "bad time" in call_tool("match_technicians", {"station": "DFW", "skill": "engine", "start": "soon", "end": "later"})["error"]
    assert call_tool("get_component_health", {})["error"]        # missing argument
    assert "event rejected" in call_tool("replan_with_event", {"event": {"type": "facility_closed", "at": "2026-10-12T12:00:00Z", "station": "XXX"}})["error"]


def test_list_at_risk_is_sorted_by_conservative_rul_and_finds_the_golden_engine():
    rows = call_tool("list_at_risk", {"horizon_cycles": 60})["data"]
    assert rows[0]["component_id"] == "SIM-017/engine_2" and rows[0]["rul_p10"] == 18.0 and rows[0]["is_anomaly"] is True
    p10 = [r["rul_p10"] for r in rows]
    assert p10 == sorted(p10) and all(x < 60 for x in p10)
    assert call_tool("list_at_risk", {"horizon_cycles": 10})["data"][0]["component_id"] == "SIM-017/engine_2"   # alert is always listed


def test_sensor_trend_says_so_when_there_is_no_twin_instead_of_inventing_one():
    res = call_tool("get_sensor_trend", {"component_id": "SIM-017/engine_2"})
    assert res["data"] == [] and "twin" in res["missing_info"][0]


def test_check_parts_and_shortage_report_match_the_golden_facts():
    assert call_tool("check_parts", {"station": "ATL", "part": "BORESCOPE-KIT"})["data"] | {} == {
        "on_hand": 0, "in_transit": 0, "eta_hours": 0.0, "ship_from": "DFW", "ship_hours": 18.0, "ship_cost": 800.0}
    assert call_tool("check_parts", {"station": "DFW", "part": "BORESCOPE-KIT"})["data"]["on_hand"] == 2
    call_tool("solve_schedule", {"mode": "cpsat"})
    rows = call_tool("shortage_report")["data"]
    assert rows and all({"task_id", "missing", "detail"} <= set(r) for r in rows)
    assert {r["missing"] for r in rows} <= {"part", "technician", "hangar", "window", "capacity"}


def test_compare_plans_and_simulate_use_the_same_plan_ids():
    plan = call_tool("solve_schedule", {"mode": "cpsat"})["data"]
    rows = call_tool("compare_plans", {"names": ["baseline", "greedy", "optimized"]})["data"]["rows"]
    assert [r["name"] for r in rows] == ["baseline", "greedy", "optimized"]
    assert next(r for r in rows if r["name"] == "optimized")["plan_id"] == plan["id"]
    assert call_tool("simulate_plan", {"plan_id": plan["id"]})["data"] == plan["metrics"]
    assert "unknown plan name" in call_tool("compare_plans", {"names": ["best"]})["error"]


# ------------------------------------------------------------------ the scripted workflow
def test_scripted_run_on_golden_meets_the_acceptance_criteria(golden_run):
    run = golden_run
    assert run.status == "done" and run.mode == "scripted"
    agents_with_tool_calls = {s.agent for s in run.steps if s.kind == "tool_call"}
    assert agents_with_tool_calls == set(CONTRACT_TOOLS)            # all five agents called real tools
    assert len(agents_with_tool_calls) >= 3
    assert len(run.work_orders) >= 1 and run.plan is not None and run.plan.feasible
    assert all(o.status == "pending_approval" for o in run.work_orders)              # nothing is approved by the system
    assert all(o.evidence for o in run.work_orders)
    kinds = {e.kind for o in run.work_orders for e in o.evidence}
    assert {"plan", "kb", "health", "tool_call"} <= kinds
    assert len({o.id for o in run.work_orders}) == len(run.work_orders)
    assert run.explanation and "pending human approval" in run.explanation and "Advisory only" in run.explanation


def test_run_finds_the_golden_anomaly_and_plans_it_at_dfw(golden_run):
    risk = next(s for s in golden_run.steps if s.tool == "list_at_risk" and s.kind == "tool_result")
    assert "SIM-017/engine_2" in [r["component_id"] for r in risk.output["data"]]
    wo = next(o for o in golden_run.work_orders if o.tail == "SIM-017")
    assert wo.station == "DFW" and wo.task_id == "T-SIM-017-engine_2-001"
    assert "SIM-017 engine_2" in golden_run.explanation and "RUL p10 18" in golden_run.explanation and "DFW" in golden_run.explanation


def test_steps_are_ordered_numbered_and_every_tool_call_has_a_result(golden_run):
    steps = golden_run.steps
    assert [s.n for s in steps] == list(range(1, len(steps) + 1))
    order = [s.agent for s in steps]
    first_seen = [a for i, a in enumerate(order) if a not in order[:i]]
    assert first_seen == ["health", "scheduling", "logistics", "operations", "coordinator"]
    assert order == sorted(order, key=["health", "scheduling", "logistics", "operations", "coordinator"].index)   # never goes back
    for i, s in enumerate(steps):
        if s.kind == "tool_call":
            assert steps[i + 1].kind == "tool_result" and steps[i + 1].tool == s.tool
            assert steps[i + 1].output["evidence"]["kind"] in {"tool_call", "plan", "health", "kb", "event"}
    assert steps[-1].kind == "summary" and steps[-1].agent == "coordinator"
    assert all(s.ts == golden_state().now for s in steps)          # simulated time, never the wall clock


def test_every_number_in_the_explanation_comes_from_a_tool_output(golden_run):
    pool: set[float] = set()
    for s in golden_run.steps:
        if s.kind == "tool_result" and s.output:
            pool |= numbers(json.dumps(s.output["data"]))
    pool |= numbers(json.dumps(golden_run.plan.model_dump(mode="json")))
    foreign = numbers(golden_run.explanation) - pool
    assert foreign == set(), f"numbers not found in any tool output: {sorted(foreign)}"


def test_run_is_fast_and_deterministic():
    t0 = time.perf_counter()
    a = run_workflow(GOAL, {}, "scripted")
    elapsed = time.perf_counter() - t0
    assert elapsed < 5.0, elapsed                                   # INTEGRATION.md 4.2: scripted mode under 5 s
    STORE.reset(); RUNS.reset()
    b = run_workflow(GOAL, {}, "scripted")
    key = lambda r: ([(x.station, x.start) for x in r.plan.assignments], r.explanation.replace(r.plan.id, "P"))  # noqa: E731
    assert key(a) == key(b)


def test_scope_tails_narrows_the_health_investigation():
    run = run_workflow(GOAL, {"tails": ["SIM-005"]}, "scripted")
    risk = next(s for s in run.steps if s.tool == "list_at_risk" and s.kind == "tool_result")
    assert {r["tail"] for r in risk.output["data"]} == {"SIM-005"}
    assert run.status == "done"


def test_llm_mode_falls_back_to_scripted_and_says_so():
    run = run_workflow(GOAL, {}, "llm")
    assert run.mode == "scripted" and run.status == "done" and LLM_NOTE in run.missing_info


def test_missing_twin_is_reported_in_missing_info_not_guessed(golden_run):
    assert any("twin" in m for m in golden_run.missing_info)


def test_infeasible_world_is_explained_and_only_scheduled_jobs_get_work_orders(monkeypatch):
    st = golden_state()
    for s in st.stations:
        for t in s.technicians:
            t.skills = [x for x in t.skills if x != "engine"]
    monkeypatch.setattr(service, "_snapshot", lambda: (st, "test"))
    run = run_workflow(GOAL, {}, "scripted")
    assert run.status == "done" and run.plan.feasible is False
    assert "NOT feasible" in run.explanation and "no engine technician" in run.explanation
    assert all(o.task_id not in ("T-SIM-017-engine_2-001", "T-SIM-012-engine_1-001") for o in run.work_orders)    # the engine jobs could not be planned
    assert run.work_orders and all(o.evidence for o in run.work_orders)
    assert any("no engine technician" in m for m in run.missing_info) or run.plan.infeasible_reasons


def test_a_crashing_run_ends_in_error_not_in_running(monkeypatch):
    import app.agents.scripted as scripted
    monkeypatch.setattr(scripted, "call_tool", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    run = run_workflow(GOAL, {}, "scripted")
    assert run.status == "error" and "RuntimeError: boom" in run.explanation


# ------------------------------------------------------------------ a deadline nobody can meet: honest, with a human-only suggestion
def _near_failure_world(monkeypatch, p10: float = 1.9):
    from app.contracts.models import RulEstimate
    st = golden_state()
    comp = next(c for c in st.components if c.component_id == "SIM-017/engine_2")
    comp.rul_cycles = RulEstimate(p10=p10, p50=p10 * 3, p90=p10 * 8)
    monkeypatch.setattr(service, "_snapshot", lambda: (st, "test"))
    return st


def test_near_failure_is_infeasible_with_a_least_bad_suggestion_that_is_never_applied(monkeypatch):
    _near_failure_world(monkeypatch)
    run = run_workflow(GOAL, {}, "scripted")
    assert run.status == "done" and run.plan.feasible is False
    assert not any(a.task_id == "T-SIM-017-engine_2-001" for a in run.plan.assignments)          # never scheduled past the deadline
    assert not any(o.task_id == "T-SIM-017-engine_2-001" for o in run.work_orders)
    short = next(s for s in run.steps if s.tool == "shortage_report" and s.kind == "tool_result").output["data"]
    row = next(r for r in short if r["task_id"] == "T-SIM-017-engine_2-001")
    assert row["least_bad"]["hours_after_deadline"] > 0 and "NOT a plan" in row["least_bad"]["note"]
    assert "For a human to decide (NOT applied)" in run.explanation and "after its deadline" in run.explanation
    assert "Savings are simulated" not in run.explanation          # no baseline-vs-optimized claim for a failed plan
    pool = set()
    for s in run.steps:
        if s.kind == "tool_result" and s.output:
            pool |= numbers(json.dumps(s.output["data"]))
    pool |= numbers(json.dumps(run.plan.model_dump(mode="json")))
    assert numbers(run.explanation) - pool == set()                 # still only tool-derived numbers


def test_when_no_slot_exists_at_all_the_explanation_says_so(monkeypatch):
    st = golden_state()
    for s in st.stations:
        for t in s.technicians:
            t.skills = [x for x in t.skills if x != "engine"]
    monkeypatch.setattr(service, "_snapshot", lambda: (st, "test"))
    run = run_workflow(GOAL, {}, "scripted")
    assert "no slot exists for T-SIM-017-engine_2-001 anywhere in the known rotation" in run.explanation
    assert call_tool("shortage_report")["data"][0]["least_bad"] is None


def test_least_bad_is_a_function_of_the_state_and_marks_itself_as_not_a_plan(monkeypatch):
    from app.solver.leastbad import least_bad
    st = _near_failure_world(monkeypatch)
    from app.solver import derive_tasks
    task = next(t for t in derive_tasks(st) if t.tail == "SIM-017")
    lb = least_bad(st, task)
    assert lb["station"] == "DFW" and lb["end"] > task.deadline and lb["hours_after_deadline"] == 6.0 and "NOT a plan" in lb["note"]


# ------------------------------------------------------------------ disruption through the tool, then a fresh run in the changed world
def test_replan_tool_changes_the_world_and_the_next_run_sees_it():
    first = run_workflow(GOAL, {}, "scripted")
    res = call_tool("replan_with_event", {"event": {"type": "part_delay", "at": "2026-10-12T14:00:00Z", "station": "DFW",
                                                    "part": "BORESCOPE-KIT", "delay_hours": 12}})
    assert res.get("error") is None and [m["task_id"] for m in res["data"]["diff"]["moved"]] == ["T-SIM-017-engine_2-001"]
    assert res["evidence"]["kind"] == "event"
    second = run_workflow(GOAL, {}, "scripted")
    a1 = next(a for a in first.plan.assignments if a.task_id == "T-SIM-017-engine_2-001")
    a2 = next(a for a in second.plan.assignments if a.task_id == "T-SIM-017-engine_2-001")
    assert a2.start > a1.start                                       # the new run plans in the changed world


# ------------------------------------------------------------------ HTTP
@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(agents_router)
    return TestClient(app)


def test_post_agents_run_returns_immediately_and_polling_reaches_done(client):
    r = client.post("/agents/run", json={"goal": GOAL, "scope": {}, "mode": "scripted"})
    assert r.status_code == 200
    first = AgentRun.model_validate(r.json())
    assert first.id.startswith("R-") and first.goal == GOAL
    deadline = time.time() + 30
    body = None
    while time.time() < deadline:                                     # the UI polls once a second; here we poll fast
        body = client.get(f"/agents/runs/{first.id}").json()
        if body["status"] != "running":
            break
        time.sleep(0.1)
    assert body and body["status"] == "done" and len(body["steps"]) > 20 and body["work_orders"]
    AgentRun.model_validate(body)


def test_get_unknown_run_is_a_404_with_the_error_shape(client):
    r = client.get("/agents/runs/R-999")
    assert r.status_code == 404 and r.json()["error"] == "unknown run"


def test_steps_stream_in_while_the_run_is_in_progress():
    seen: list[int] = []
    RUNS.add_listener(lambda run: seen.append(len(run.steps)))
    try:
        run_workflow(GOAL, {}, "scripted")
    finally:
        RUNS._listeners.clear()
    assert seen == sorted(seen) and max(seen) > 50 and len(set(seen)) > 20      # progress was visible step by step


def test_agent_run_fixture_matches_the_model():
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "fixtures" / "agent_run_golden.json"
    run = AgentRun.model_validate_json(path.read_text())
    assert run.status == "done" and run.work_orders and {s.agent for s in run.steps} == set(CONTRACT_TOOLS)


def test_repositioning_tool_is_advisory_and_shows_up_in_the_run(golden_run):
    res = call_tool("suggest_repositioning", {})
    assert res.get("error") is None and res["data"]
    assert all("Advisory" in r["note"] for r in res["data"])
    step = next(s for s in golden_run.steps if s.tool == "suggest_repositioning" and s.kind == "tool_result")
    assert step.agent == "logistics" and "Advisory, not applied" in golden_run.explanation
    assert "move 1 BORESCOPE-KIT from DFW to ATL" in golden_run.explanation


def test_the_run_explains_the_time_to_fix_contrast_with_tool_numbers_only(golden_run):
    assert "Time to fix SIM-017 engine_2: baseline" in golden_run.explanation and "optimized" in golden_run.explanation
    cmp_rows = next(s for s in golden_run.steps if s.tool == "compare_plans" and s.kind == "tool_result").output["data"]["rows"]
    o = next(r for r in cmp_rows if r["name"] == "optimized")["exposure"][0]
    assert f"optimized {o['hours_to_fix']:g} h" in golden_run.explanation
