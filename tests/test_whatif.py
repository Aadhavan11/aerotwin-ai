"""Acceptance tests for feature 3.5 (FEATURES-3.1-3.6.md): part delay changes the golden plan and the diff lists the moved task,
a closed facility is respected, compare returns >= 4 scenario rows, replan time is printed (target < 5 s).
Plus the HTTP endpoints this module owns. Run: pytest -q tests/test_whatif.py -s"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.contracts.models import Scenario, ScenarioEvent
from app.solver import derive_tasks, solve
from app.solver.store import STORE
from app.solver.golden import GOLDEN_COMPONENT, GOLDEN_TAIL, golden_part_delay, golden_state
from app.solver.router import router as solver_router
from app.whatif import compare as cmp
from app.whatif.events import EventError, apply_event, apply_event_to_state
from app.whatif.replan import replan
from app.whatif.router import router as whatif_router
from app.whatif.simulate import affected_legs, simulate, simulate_detailed
from tests.solver_helpers import T0, assert_no_fallback, engine_task, hard_violations, hrs, mini_state, simple_station

SIM17_TASK = "T-SIM-017-engine_2-001"


# ---- fixtures (defined per module so they can never clash with fixtures other people add under tests/) ----
@pytest.fixture(autouse=True)
def fresh_store(monkeypatch):
    """Empty plan store for every test; tests never use mock flags (they exercise the real CP-SAT solver)."""
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("MOCK_SOLVER", raising=False)
    STORE.reset()
    yield
    STORE.reset()


@pytest.fixture(scope="session")
def golden():
    """The golden FleetState (stand-in). Read-only: tests that change it use apply_event_to_state or model_copy(deep=True)."""
    return golden_state()


@pytest.fixture(scope="session")
def golden_tasks(golden):
    return derive_tasks(golden)


def ev(**kw) -> ScenarioEvent:
    kw.setdefault("at", T0 + hrs(2))
    return ScenarioEvent(**kw)


@pytest.fixture(scope="module")
def prior(golden, golden_tasks):
    return solve(golden, golden_tasks, mode="cpsat")


# ------------------------------------------------------------------ apply_event_to_state
def test_apply_event_is_pure_and_returns_a_new_state(golden):
    before = golden.model_dump()
    new = apply_event_to_state(golden, golden_part_delay(12))
    assert golden.model_dump() == before and new is not golden
    assert new.model_dump() != before


def test_part_delay_on_hand_stock_is_unavailable_until_event_time_plus_delay(golden):
    new, effects = apply_event(golden, golden_part_delay(12, at=T0 + hrs(2)))
    kit = next(p for s in new.stations if s.iata == "DFW" for p in s.parts if p.part == "BORESCOPE-KIT")
    assert kit.eta_hours == pytest.approx(14.0) and kit.qty == 2          # units are not lost, only held
    assert "DFW BORESCOPE-KIT: available in 14 h" in effects[0]


def test_part_delay_on_stock_in_transit_adds_the_delay(golden):
    st = golden.model_copy(deep=True)
    next(p for s in st.stations if s.iata == "DFW" for p in s.parts if p.part == "BORESCOPE-KIT").eta_hours = 5.0
    new = apply_event_to_state(st, golden_part_delay(12))
    assert next(p for s in new.stations if s.iata == "DFW" for p in s.parts if p.part == "BORESCOPE-KIT").eta_hours == 17.0


def test_facility_closed_and_tech_unavailable(golden):
    new = apply_event_to_state(golden, ev(type="facility_closed", station="DFW"))
    assert next(s for s in new.stations if s.iata == "DFW").hangar_slots == 0
    assert next(s for s in golden.stations if s.iata == "DFW").hangar_slots == 2
    new = apply_event_to_state(golden, ev(type="tech_unavailable", technician_id="DFW-T1"))
    assert [t.available for s in new.stations if s.iata == "DFW" for t in s.technicians] == [False, True, True]


def test_anomaly_shortens_rul_and_raises_status(golden):
    new, effects = apply_event(golden, ev(type="accelerated_degradation", component_id="SIM-012/engine_1", severity=0.5))
    c = next(c for c in new.components if c.component_id == "SIM-012/engine_1")
    assert c.rul_cycles.p10 == pytest.approx(23.5) and c.status == "alert"
    assert "47 -> 23.5" in effects[0]


def test_anomaly_with_an_event_id_is_not_applied_twice(golden):
    e = ev(id="E-7", type="anomaly", component_id="SIM-012/engine_1", severity=0.5)
    once = apply_event_to_state(golden, e)
    twice = apply_event_to_state(once, e)
    assert next(c for c in twice.components if c.component_id == "SIM-012/engine_1").rul_cycles.p10 == pytest.approx(23.5)


@pytest.mark.parametrize("bad", [
    dict(type="part_delay", station="DFW"),                                   # no part / delay
    dict(type="part_delay", station="XXX", part="BORESCOPE-KIT", delay_hours=3),
    dict(type="facility_closed"),
    dict(type="tech_unavailable", technician_id="NOPE"),
    dict(type="anomaly", component_id="SIM-999/engine_1"),
])
def test_malformed_events_raise_event_error(golden, bad):
    with pytest.raises(EventError):
        apply_event_to_state(golden, ev(**bad))


# ------------------------------------------------------------------ replan: the "killer feature"
def test_12h_dfw_kit_delay_changes_the_plan_and_the_diff_lists_the_moved_task(golden, prior):
    r = replan(golden, prior, golden_part_delay(12))
    print(f"\nreplan after 12 h DFW kit delay: {r.solve_seconds:.3f} s "
          f"(target < 5 s); moved={[m['task_id'] for m in r.diff.moved]} added={r.diff.added} dropped={r.diff.dropped}")
    assert r.solve_seconds < 5.0
    assert r.after.feasible
    assert_no_fallback(r.after, "cpsat")
    assert [m["task_id"] for m in r.diff.moved] == [SIM17_TASK]
    m = r.diff.moved[0]
    assert m["from"]["station"] == "DFW" and m["to"]["start"] > m["from"]["start"]
    assert "BORESCOPE-KIT at DFW delayed 12 h" in m["why"] and "no longer feasible" in m["why"]
    assert r.diff.added == [] and r.diff.dropped == []
    assert r.after.metrics.schedule_changes == 1               # only the affected task changed (stability penalty)


def test_replanned_plan_respects_the_delay_and_every_hard_rule(golden, prior):
    r = replan(golden, prior, golden_part_delay(12))
    st2 = apply_event_to_state(golden, golden_part_delay(12))
    assert hard_violations(st2, derive_tasks(st2), r.after) == []
    a = next(x for x in r.after.assignments if x.task_id == SIM17_TASK)
    assert a.start >= T0 + hrs(14)                              # kit is back at +2 h (event) + 12 h
    assert a.end <= next(t for t in derive_tasks(st2) if t.id == SIM17_TASK).deadline
    assert any(e.kind == "event" and "available in 14 h" in e.note for e in r.after.evidence)


def test_replan_does_not_touch_the_prior_plan_or_the_input_state(golden, prior):
    before_state, before_plan = golden.model_dump(), prior.model_dump()
    replan(golden, prior, golden_part_delay(12))
    assert golden.model_dump() == before_state and prior.model_dump() == before_plan


def test_a_delay_that_does_not_matter_changes_nothing(golden, prior):
    r = replan(golden, prior, ev(type="part_delay", station="MIA", part="BRAKE-ASSY", delay_hours=6))
    assert r.diff.moved == [] and r.diff.added == [] and r.diff.dropped == []
    assert r.after.metrics.schedule_changes == 0


@pytest.mark.parametrize("station", ["ATL", "DFW", "ORD", "DEN", "LAX", "JFK", "MIA", "SEA"])
def test_a_closed_facility_is_never_used(golden, prior, station):
    r = replan(golden, prior, ev(type="facility_closed", station=station))
    assert_no_fallback(r.after, "cpsat")
    assert [a for a in r.after.assignments if a.station == station] == []
    st2 = apply_event_to_state(golden, ev(type="facility_closed", station=station))
    assert hard_violations(st2, derive_tasks(st2), r.after) == []
    assert r.after.feasible or r.after.infeasible_reasons          # feasible, or explained


def test_a_technician_who_becomes_unavailable_is_never_assigned(golden, prior):
    used = {a.technician_id for a in prior.assignments}
    for tech in sorted(used):
        r = replan(golden, prior, ev(type="tech_unavailable", technician_id=tech))
        assert tech not in {a.technician_id for a in r.after.assignments}


def test_an_anomaly_pulls_the_deadline_in_and_the_plan_follows(golden, prior):
    e = ev(id="E-9", type="anomaly", component_id="SIM-012/engine_1", severity=0.7)
    r = replan(golden, prior, e)
    st2 = apply_event_to_state(golden, e)
    t_new = next(t for t in derive_tasks(st2) if t.component_id == "SIM-012/engine_1")
    t_old = next(t for t in derive_tasks(golden) if t.component_id == "SIM-012/engine_1")
    assert t_new.deadline < t_old.deadline
    a = next(x for x in r.after.assignments if x.task_id == t_new.id)
    assert a.end <= t_new.deadline
    assert [m["task_id"] for m in r.diff.moved] == [t_new.id]
    assert hard_violations(st2, derive_tasks(st2), r.after) == []


def test_new_fault_on_a_healthy_component_adds_a_task(golden, prior):
    e = ev(id="E-10", type="anomaly", component_id="SIM-002/brakes", severity=0.95)
    r = replan(golden, prior, e)
    assert r.diff.added == ["T-SIM-002-brakes-001"]
    assert any(a.task_id == "T-SIM-002-brakes-001" for a in r.after.assignments)


def test_a_severe_anomaly_that_leaves_no_time_is_explained_per_window(golden, prior):
    r = replan(golden, prior, ev(id="E-12", type="anomaly", component_id="SIM-012/engine_1", severity=0.9))
    text = " ".join(r.after.infeasible_reasons)
    assert r.after.feasible is False and "too short for the 6 h job" in text and "BORESCOPE-KIT cannot arrive in time" in text
    assert "T-SIM-012-engine_1-001" in r.diff.dropped


def test_impossible_replan_says_why_and_never_relaxes_the_deadline(golden, prior):
    e = ev(id="E-11", type="anomaly", component_id=GOLDEN_COMPONENT, severity=1.0)      # RUL -> 0: may not fly again
    r = replan(golden, prior, e)
    assert r.after.feasible is False
    assert SIM17_TASK in " ".join(r.after.infeasible_reasons) and "deadline" in " ".join(r.after.infeasible_reasons)
    assert not [a for a in r.after.assignments if a.task_id == SIM17_TASK]              # not force-fitted late
    assert SIM17_TASK in r.diff.dropped                                                  # the UI can show what fell out
    assert r.after.metrics.late_or_unscheduled >= 1


def test_already_applied_flag_prevents_double_application(golden, prior):
    e = golden_part_delay(20)                               # 20 h: applying it twice (eta 22 -> 42 h) visibly moves the job
    once = replan(golden, prior, e)
    st2 = apply_event_to_state(golden, e)                   # what the platform would hold if it wrote the event to the DB
    flagged = replan(st2, prior, e, already_applied=True)
    unflagged = replan(st2, prior, e)                       # applies the delay a second time
    key = lambda r: [(a.task_id, a.station, a.start) for a in r.after.assignments]  # noqa: E731
    assert key(flagged) == key(once)
    assert key(unflagged) != key(once)
    assert any("already applied" in x.note for x in flagged.after.evidence)
    assert any("available in 42 h" in x.note for x in unflagged.after.evidence)


def test_invalid_event_is_an_infeasible_result_not_an_exception(golden, prior):
    r = replan(golden, prior, ev(type="part_delay", station="XXX", part="BORESCOPE-KIT", delay_hours=5))
    assert r.after.feasible is False and "event rejected" in r.after.infeasible_reasons[0] and "XXX" in r.after.infeasible_reasons[0]
    assert r.diff.moved == [] and [a.task_id for a in r.after.assignments] == [a.task_id for a in prior.assignments]


# ------------------------------------------------------------------ compare
def test_compare_returns_at_least_four_scenario_rows(golden, golden_tasks, prior):
    scenarios = cmp.default_scenarios(golden, prior, golden_tasks)
    plans = cmp.build_scenario_plans(golden, scenarios, base_plan=prior)
    out = cmp.compare(golden, plans)
    assert len(out.rows) >= 4
    assert all({"name", "feasible", "metrics"} <= set(r) for r in out.rows)
    names = " | ".join(r["name"] for r in out.rows)
    for needle in ("later", "another airport", "delayed 12 h", "unavailable"):
        assert needle in names
    assert out.rows[0]["metrics"]["schedule_changes"] == 0           # "current plan" scenario == the base plan


def test_compare_earliest_constraint_pushes_the_job_later(golden, golden_tasks, prior):
    late = Scenario(name="later", constraints={"earliest": (T0 + hrs(24)).isoformat(), "tail": GOLDEN_TAIL})
    plan = cmp.build_scenario_plans(golden, [late], base_plan=prior)["later"]
    a = next(x for x in plan.assignments if x.task_id == SIM17_TASK)
    assert a.start >= T0 + hrs(24)
    other = next(x for x in plan.assignments if x.task_id != SIM17_TASK)
    assert other.start == next(p for p in prior.assignments if p.task_id == other.task_id).start    # scoped to one aircraft


def test_compare_force_station_uses_only_that_station_for_that_aircraft(golden, golden_tasks, prior):
    sc = Scenario(name="atl", constraints={"force_station": "ATL", "tail": GOLDEN_TAIL})
    plan = cmp.build_scenario_plans(golden, [sc], base_plan=prior)["atl"]
    a = next(x for x in plan.assignments if x.task_id == SIM17_TASK)
    assert a.station == "ATL" and a.part_source == "DFW"           # ATL has no kit: shipped in, and costed
    assert plan.metrics.logistics_cost_usd > prior.metrics.logistics_cost_usd


def test_compare_reports_an_impossible_scenario_honestly(golden, prior):
    sc = Scenario(name="nowhere", constraints={"force_station": "SEA", "tail": GOLDEN_TAIL})
    plan = cmp.build_scenario_plans(golden, [sc], base_plan=prior)["nowhere"]
    assert plan.feasible is False and "outside the forced station" in " ".join(plan.infeasible_reasons)


def test_compare_with_a_bad_event_rejects_that_scenario_only(golden, prior):
    bad = Scenario(name="bad", events=[ev(type="facility_closed", station="XXX")])
    ok = Scenario(name="ok")
    plans = cmp.build_scenario_plans(golden, [bad, ok], base_plan=prior)
    assert plans["bad"].feasible is False and "rejected" in plans["bad"].infeasible_reasons[0]
    assert plans["ok"].feasible


# ------------------------------------------------------------------ simulate
def test_simulate_l1_is_analytic_and_matches_the_plan(golden, prior):
    m = simulate(prior, golden)
    assert m.model_dump() == prior.metrics.model_dump()


def test_simulate_l2_simpy_is_deterministic_with_a_seed(golden, prior):
    a = simulate_detailed(prior, golden, runs=20, seed=7)
    b = simulate_detailed(prior, golden, runs=20, seed=7)
    assert a.per_run_downtime_h == b.per_run_downtime_h and a.metrics == b.metrics
    assert a.runs == 20 and a.downtime_p90_h >= a.downtime_mean_h - 1e-9
    assert simulate(prior, golden, runs=20, seed=7) == a.metrics


def test_simulate_l2_without_jitter_reproduces_the_analytic_plan(golden, prior):
    s = simulate_detailed(prior, golden, runs=3, seed=1, jitter=0.0)
    assert s.downtime_mean_h == 0.0 and s.metrics.flights_affected == 0 and s.deadline_overrun_runs == 0
    assert s.metrics.tasks_scheduled == prior.metrics.tasks_scheduled


def test_simulate_l2_cascades_a_delay_onto_later_legs():
    # 3 h window, 4 h job -> 1 h extension; the next window is only 0.4 h long, so the delay carries into the next departure
    st = mini_state(windows={"SIM-001": [("AAA", 1.0, 4.0), ("BBB", 4.8, 5.2)]}, stations=[simple_station("AAA"), simple_station("BBB")])
    plan = solve(st, [engine_task("T1", "SIM-001", 30, duration_h=4.0)], mode="cpsat")
    assert plan.metrics.downtime_h == pytest.approx(1.0) and plan.metrics.flights_affected == 1      # analytic: no cascade
    s = simulate_detailed(plan, st, runs=1, seed=0, jitter=0.0)
    assert s.downtime_mean_h == pytest.approx(1.0)
    assert s.metrics.flights_affected == 2                                                           # the cascade is visible


def test_simulate_l2_jitter_can_push_a_job_past_its_deadline():
    st = mini_state(windows={"SIM-001": [("AAA", 1.0, 8.0)]}, stations=[simple_station("AAA")])
    task = engine_task("T1", "SIM-001", 5.0, duration_h=4.0)           # planned to end exactly at the deadline
    plan = solve(st, [task], mode="cpsat")
    assert plan.feasible and plan.assignments[0].end == task.deadline
    s = simulate_detailed(plan, st, runs=30, seed=3, tasks=[task])
    assert s.deadline_overrun_runs > 0                                 # +-15% duration jitter eats the zero slack: reported


def test_affected_legs_lists_the_delayed_departures():
    st = mini_state(windows={"SIM-001": [("AAA", 1.0, 4.0)]}, stations=[simple_station("AAA")])
    plan = solve(st, [engine_task("T1", "SIM-001", 30, duration_h=4.0)], mode="cpsat")
    assert [l.id for l in affected_legs(plan, st)] == ["L-SIM-001-out"]


# ------------------------------------------------------------------ HTTP
@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(solver_router)
    app.include_router(whatif_router)
    return TestClient(app)


def test_get_plan_returns_current_baseline_and_compare(client):
    r = client.get("/plan")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"current", "baseline", "compare"}
    assert body["current"]["mode"] == "cpsat" and body["baseline"]["mode"] == "static_baseline"
    assert [x["name"] for x in body["compare"]["rows"]] == ["baseline", "greedy", "optimized"]
    assert body["current"]["synthetic"] is True and body["current"]["feasible"] is True
    assert any("golden stand-in" in e["note"] for e in body["current"]["evidence"])     # honest about the state source


def test_post_plan_solve_modes_and_weights(client):
    for mode in ("greedy", "static_baseline", "cpsat"):
        r = client.post("/plan/solve", json={"mode": mode})
        assert r.status_code == 200 and r.json()["mode"] == mode
    r = client.post("/plan/solve", json={"mode": "cpsat", "weights": {"downtime": 10, "disruption": 25, "logistics": 1.0, "changes": 2}})
    assert r.status_code == 200 and r.json()["feasible"]
    assert client.post("/plan/solve", json={"mode": "bogus"}).status_code == 422


def test_post_plan_replan_updates_the_current_plan(client):
    first = client.get("/plan").json()["current"]
    r = client.post("/plan/replan", json={"event": {"type": "part_delay", "at": "2026-10-12T14:00:00Z", "station": "DFW",
                                                    "part": "BORESCOPE-KIT", "delay_hours": 12}})
    assert r.status_code == 200
    body = r.json()
    assert body["diff"]["moved"] and body["after"]["feasible"] and body["solve_seconds"] < 5
    assert body["before"]["id"] == first["id"]
    assert client.get("/plan").json()["current"]["id"] == body["after"]["id"]        # the replanned plan is now current
    assert client.get("/metrics/plan").json()["replan_seconds"] == body["solve_seconds"]


def test_post_plan_replan_rejects_a_malformed_event_without_changing_anything(client):
    first = client.get("/plan").json()["current"]
    r = client.post("/plan/replan", json={"event": {"type": "facility_closed", "at": "2026-10-12T14:00:00Z", "station": "XXX"}})
    assert r.status_code == 422 and r.json()["error"] == "invalid event"
    assert client.get("/plan").json()["current"]["id"] == first["id"]


def test_post_whatif_compare(client):
    r = client.post("/whatif/compare", json={"scenarios": []})
    assert r.status_code == 200 and len(r.json()["rows"]) >= 4
    r = client.post("/whatif/compare", json={"scenarios": [
        {"name": "a"}, {"name": "b", "constraints": {"force_station": "ATL", "tail": "SIM-017"}},
        {"name": "c", "events": [{"type": "facility_closed", "at": "2026-10-12T12:00:00Z", "station": "DFW"}]}]})
    assert [x["name"] for x in r.json()["rows"]] == ["a", "b", "c"]


def test_metrics_plan_block_has_baseline_optimized_and_replan_seconds(client):
    m = client.get("/metrics/plan").json()
    assert set(m) == {"baseline", "optimized", "replan_seconds"} and m["replan_seconds"] is None
    assert m["optimized"]["tasks_total"] == 5


# ------------------------------------------------------------------ fixtures for the UI team and the demo script
def test_plan_fixtures_match_the_contract_models():
    from pathlib import Path

    from app.contracts.models import Plan, ReplanResult
    root = Path(__file__).resolve().parent.parent / "fixtures"
    for name, model in (("plan_current.json", Plan), ("plan_baseline.json", Plan), ("replan_result.json", ReplanResult)):
        obj = model.model_validate_json((root / name).read_text())
        plan = obj if isinstance(obj, Plan) else obj.after
        assert plan.synthetic is True and plan.assignments and all(a.reason for a in plan.assignments)
    cur = Plan.model_validate_json((root / "plan_current.json").read_text())
    assert cur.mode == "cpsat" and cur.feasible


def test_demo_script_runs_and_prints_the_baseline_vs_optimized_table(capsys, monkeypatch):
    import runpy
    import sys
    from pathlib import Path
    monkeypatch.setattr(sys, "argv", ["plan_demo.py"])
    runpy.run_path(str(Path(__file__).resolve().parent.parent / "scripts" / "plan_demo.py"), run_name="__main__")
    out = capsys.readouterr().out
    for needle in ("BASELINE DEFINITION", "static baseline", "CP-SAT optimized", "replan time:", "MOVED", "SimPy", "SIMULATED"):
        assert needle in out


# ------------------------------------------------------------------ the changed world after a disruption (HTTP)
def test_baseline_is_recomputed_in_the_changed_world_after_a_replan(client):
    before = client.get("/plan").json()
    assert before["baseline"]["feasible"] is True
    r = client.post("/plan/replan", json={"event": {"type": "facility_closed", "at": "2026-10-12T14:00:00Z", "station": "ATL"}})
    assert r.status_code == 200
    after = client.get("/plan").json()
    # SIM-017's home base ATL is now closed: the home-base-only baseline can no longer plan it, and says so
    assert after["baseline"]["id"] != before["baseline"]["id"] and after["baseline"]["feasible"] is False
    assert "ATL" in " ".join(after["baseline"]["infeasible_reasons"])
    assert after["current"]["id"] == r.json()["after"]["id"]
    assert [x["feasible"] for x in after["compare"]["rows"]][0] is False       # baseline row matches


def test_later_solves_and_scenarios_see_the_disruption(client):
    client.get("/plan")
    client.post("/plan/replan", json={"event": {"type": "facility_closed", "at": "2026-10-12T14:00:00Z", "station": "DFW"}})
    plan = client.post("/plan/solve", json={"mode": "cpsat"}).json()
    assert plan["assignments"] and all(a["station"] != "DFW" for a in plan["assignments"])
    rows = client.post("/whatif/compare", json={"scenarios": [{"name": "same world"}]}).json()["rows"]
    assert rows[0]["metrics"]["schedule_changes"] == 0 and rows[0]["feasible"]


def test_polling_get_plan_is_stable_and_does_not_churn_plan_ids(client):
    a, b = client.get("/plan").json(), client.get("/plan").json()
    assert a["current"]["id"] == b["current"]["id"] and a["baseline"]["id"] == b["baseline"]["id"]
    assert [x["plan_id"] for x in a["compare"]["rows"]] == [x["plan_id"] for x in b["compare"]["rows"]]


def test_already_applied_replan_does_not_store_the_event_but_still_refreshes_the_baseline(client):
    first = client.get("/plan").json()
    r = client.post("/plan/replan", json={"already_applied": True, "event": {"type": "part_delay", "at": "2026-10-12T14:00:00Z",
                                                                             "station": "DFW", "part": "BORESCOPE-KIT", "delay_hours": 12}})
    assert r.status_code == 200
    from app.solver.store import STORE as store
    assert store.world_events == []                                                # the platform owns that event
    assert client.get("/plan").json()["baseline"]["id"] != first["baseline"]["id"]
