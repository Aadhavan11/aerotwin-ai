"""Acceptance tests for feature 3.3 (FEATURES-3.1-3.6.md): deadlines, no overlaps, part counts, infeasible case reported,
CP-SAT no worse than greedy, baseline stated. Run: pytest -q tests/test_solver.py"""
from __future__ import annotations

import random
from datetime import timedelta

import pytest

from app.contracts.models import ObjectiveWeights, RulEstimate
from app.solver import derive_tasks, ground_windows, solve
from app.solver.cards import load_cards
from app.solver.store import STORE
from app.solver.golden import GOLDEN_COMPONENT, GOLDEN_TAIL, golden_state
from app.solver.tasks import cycle_deadline, derive_tasks_ex
from app.solver.windows import ground_windows_checked
from tests.solver_helpers import T0, assert_no_fallback, engine_task, hard_violations, hrs, mini_state, simple_station

MODES = ["static_baseline", "greedy", "cpsat"]


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


# ------------------------------------------------------------------ task cards
def test_task_cards_are_ten_synthetic_cards():
    cards = load_cards()
    assert len(cards) == 10
    assert all(c.synthetic for c in cards)
    assert {c.skill for c in cards} <= {"engine", "gear", "hydraulics", "avionics"}
    header = " ".join(open("app/copilot/kb/task_cards.yaml").read().replace("#", " ").split())
    assert "SYNTHETIC DEMO DATA" in header and "NOT real maintenance data" in header


def test_card_without_synthetic_flag_is_refused(tmp_path):
    from app.solver.cards import load_cards as lc
    bad = tmp_path / "c.yaml"
    bad.write_text("cards:\n  - {id: X, title: t, component_kind: engine, duration_h: 1, skill: engine, trigger: rul}\n")
    with pytest.raises(ValueError, match="synthetic"):
        lc(bad)


# ------------------------------------------------------------------ ground windows
def test_ground_window_is_arrival_to_next_departure():
    s = mini_state(windows={"SIM-001": [("AAA", 2.0, 10.0), ("BBB", 12.0, 20.0)]})
    ws = ground_windows(s.legs, s.now, s.aircraft)
    assert [(w.station, w.hours) for w in ws] == [("AAA", 8.0), ("BBB", 8.0)]
    assert ws[0].before_leg == "L-SIM-001-1"


def test_broken_rotation_gives_no_window_and_is_flagged():
    s = mini_state(windows={"SIM-001": [("AAA", 2.0, 10.0), ("BBB", 12.0, 20.0)]})
    legs = [l.model_copy(update={"dep": "QQQ"}) if l.id == "L-SIM-001-1" else l for l in s.legs]  # leg leaves from the wrong airport
    ws, issues = ground_windows_checked(legs, s.now, s.aircraft)
    assert all(w.station != "AAA" for w in ws)
    assert any("arrives AAA" in i and "QQQ" in i for i in issues)


def test_past_windows_dropped_and_current_one_clipped_to_now():
    s = mini_state(windows={"SIM-001": [("AAA", -6.0, -1.0), ("BBB", -0.0, 5.0), ("CCC", 8.0, 12.0)]})
    ws = ground_windows(s.legs, s.now + timedelta(hours=2), s.aircraft)
    assert [w.station for w in ws] == ["BBB", "CCC"]
    assert ws[0].start == s.now + timedelta(hours=2) and ws[0].hours == 3.0


def test_window_the_aircraft_has_right_now_is_added_with_aircraft_list(golden):
    with_ac = ground_windows(golden.legs, golden.now, golden.aircraft)
    without = ground_windows(golden.legs, golden.now)
    assert len(with_ac) == len(without) + len(golden.aircraft)   # one "parked now" window per aircraft in the golden fleet
    first = [w for w in with_ac if w.tail == GOLDEN_TAIL][0]
    assert first.station == "ATL" and first.start == golden.now


# ------------------------------------------------------------------ derive_tasks
def test_golden_has_five_tasks_including_the_engine_inspection(golden_tasks):
    assert len(golden_tasks) == 5
    sim17 = next(t for t in golden_tasks if t.tail == GOLDEN_TAIL)
    assert sim17.id == "T-SIM-017-engine_2-001" and sim17.source == "rul"
    assert (sim17.required_skill, sim17.required_part, sim17.duration_h) == ("engine", "BORESCOPE-KIT", 6.0)
    assert "p10 = 18" in sim17.basis and "hard constraint" in sim17.basis
    assert any(t.source == "scheduled" for t in golden_tasks)
    assert [t.deadline for t in golden_tasks] == sorted(t.deadline for t in golden_tasks)


def test_deadline_is_arrival_of_the_floor_p10_th_future_flight_not_p50(golden):
    legs = sorted((l for l in golden.legs if l.tail == GOLDEN_TAIL and l.sched_arr > golden.now), key=lambda l: l.sched_dep)
    task = next(t for t in derive_tasks(golden) if t.tail == GOLDEN_TAIL)
    assert task.deadline == legs[17].sched_arr          # p10 = 18 -> the 18th flight (index 17)
    assert task.deadline < cycle_deadline(legs, 22, golden.now)[0]      # p50 = 22 would have given a LATER deadline: never used


def test_deadline_rounds_the_cycle_count_down():
    s = mini_state(windows={"SIM-001": [("AAA", 2.0, 10.0)]})
    future = sorted(s.legs, key=lambda l: l.sched_dep)
    d_int, _ = cycle_deadline(future, 2, s.now)
    d_frac = cycle_deadline(future, int(2.9), s.now)[0]
    assert d_int == d_frac


def test_deadline_beyond_known_rotation_extrapolates_with_the_aircrafts_own_rate(golden):
    legs = sorted((l for l in golden.legs if l.tail == "SIM-005" and l.sched_arr > golden.now), key=lambda l: l.sched_dep)
    d, how = cycle_deadline(legs, 35, golden.now)         # 20 known legs, 35 cycles requested
    assert d > legs[-1].sched_arr and "extrapolated" in how
    per_day = len(legs) / ((legs[-1].sched_arr - golden.now).total_seconds() / 86400)
    assert d == legs[-1].sched_arr + timedelta(days=15 / per_day)


def test_zero_cycles_left_means_before_the_next_departure():
    s = mini_state(windows={"SIM-001": [("AAA", 2.0, 10.0)]})
    d, _ = cycle_deadline(sorted(s.legs, key=lambda l: l.sched_dep), 0, s.now)
    assert d == min(l.sched_dep for l in s.legs)


def test_component_with_alert_but_no_rul_is_reported_not_skipped_silently(golden):
    st = golden.model_copy(deep=True)
    c = next(c for c in st.components if c.component_id == GOLDEN_COMPONENT)
    c.rul_cycles = None
    res = derive_tasks_ex(st)
    assert not any(t.component_id == GOLDEN_COMPONENT for t in res.tasks)
    assert any(GOLDEN_COMPONENT in n and "no RUL" in n for n in res.notes)


def test_overdue_scheduled_check_is_reported_for_a_human(golden):
    st = golden.model_copy(deep=True)
    c = next(c for c in st.components if c.component_id == "SIM-003/hydraulics")
    c.last_maintenance = st.now - timedelta(days=400)
    res = derive_tasks_ex(st)
    assert any("SIM-003/hydraulics" in n and "overdue" in n for n in res.notes)
    assert not any(t.component_id == "SIM-003/hydraulics" for t in res.tasks)


def test_derive_tasks_is_deterministic(golden):
    assert [t.model_dump() for t in derive_tasks(golden)] == [t.model_dump() for t in derive_tasks(golden)]


# ------------------------------------------------------------------ the three solvers on the golden state
@pytest.mark.parametrize("mode", MODES)
def test_golden_plan_is_feasible_meets_deadlines_and_breaks_no_hard_rule(golden, golden_tasks, mode):
    plan = solve(golden, golden_tasks, mode=mode)
    assert plan.feasible and plan.infeasible_reasons == []
    assert_no_fallback(plan, mode)                # the solver itself is valid, not rescued by the engine's guard
    assert len(plan.assignments) == len(golden_tasks) == plan.metrics.tasks_total == plan.metrics.tasks_scheduled
    assert plan.metrics.late_or_unscheduled == 0
    assert hard_violations(golden, golden_tasks, plan) == []
    by_task = {t.id: t for t in golden_tasks}
    assert all(a.end <= by_task[a.task_id].deadline for a in plan.assignments)
    assert all(a.reason and a.technician_id for a in plan.assignments)
    assert plan.synthetic is True


def test_cpsat_picks_dfw_for_the_golden_task_and_explains_why(golden, golden_tasks):
    plan = solve(golden, golden_tasks, mode="cpsat")
    a = next(x for x in plan.assignments if x.task_id == "T-SIM-017-engine_2-001")
    assert a.station == "DFW" and a.part_source is None and a.extension_h == 0.0     # kit on hand at DFW, ATL has none
    assert "BORESCOPE-KIT on hand at DFW" in a.reason and "0 h extension" in a.reason and "deadline" in a.reason
    assert any("proven optimal" in e.note for e in plan.evidence)


def test_static_baseline_is_home_base_last_window_before_deadline(golden, golden_tasks):
    plan = solve(golden, golden_tasks, mode="static_baseline")
    home = {a.tail: a.home for a in golden.aircraft}
    assert all(a.station == home[a.tail] for a in plan.assignments)              # never anywhere but the home base

    # SIM-017 (home ATL): the chosen window must be the LAST ATL window that can still host the job before the deadline
    task = next(t for t in golden_tasks if t.tail == GOLDEN_TAIL)
    a = next(x for x in plan.assignments if x.task_id == task.id)
    kit_ready = golden.now + hrs(18.0)                                          # ATL has no kit: shipped from DFW in 18 h
    fits = [w for w in ground_windows(golden.legs, golden.now, golden.aircraft)
            if w.tail == GOLDEN_TAIL and w.station == "ATL"
            and max(w.start, kit_ready) + hrs(task.duration_h) <= min(task.deadline, w.end)]
    assert fits, "test setup: the golden state must offer ATL windows"
    last = max(fits, key=lambda w: w.start)
    assert last.start <= a.start < last.end
    assert a.part_source == "DFW" and "Baseline policy" in a.reason


def test_optimized_is_cheaper_than_baseline_on_golden(golden, golden_tasks):
    base = solve(golden, golden_tasks, mode="static_baseline")
    opt = solve(golden, golden_tasks, mode="cpsat")
    assert opt.metrics.logistics_cost_usd < base.metrics.logistics_cost_usd
    assert opt.objective_value < base.objective_value
    first_opt = min(a.end for a in opt.assignments if a.tail == GOLDEN_TAIL)
    first_base = min(a.end for a in base.assignments if a.tail == GOLDEN_TAIL)
    assert first_opt < first_base            # the optimizer fixes the critical engine sooner than the fixed schedule


def test_cpsat_is_no_worse_than_greedy_on_golden(golden, golden_tasks):
    g, c = solve(golden, golden_tasks, mode="greedy"), solve(golden, golden_tasks, mode="cpsat")
    assert c.objective_value <= g.objective_value + 1e-3
    assert c.metrics.late_or_unscheduled == 0


def test_cpsat_is_deterministic(golden, golden_tasks):
    runs = [solve(golden, golden_tasks, mode="cpsat") for _ in range(3)]
    key = lambda p: [(a.task_id, a.station, a.start, a.technician_id, a.part_source) for a in p.assignments]  # noqa: E731
    assert key(runs[0]) == key(runs[1]) == key(runs[2])
    assert runs[0].objective_value == runs[1].objective_value


def test_solve_does_not_mutate_its_inputs(golden, golden_tasks):
    before_state, before_tasks = golden.model_dump(), [t.model_dump() for t in golden_tasks]
    for mode in MODES:
        solve(golden, golden_tasks, mode=mode)
    assert golden.model_dump() == before_state and [t.model_dump() for t in golden_tasks] == before_tasks


def test_solve_time_is_well_under_the_five_second_budget(golden, golden_tasks):
    assert solve(golden, golden_tasks, mode="cpsat", time_limit_s=5.0).solve_seconds < 5.0


def test_no_tasks_is_a_feasible_empty_plan(golden):
    for mode in MODES:
        p = solve(golden, [], mode=mode)
        assert p.feasible and p.assignments == [] and p.metrics.tasks_total == 0


# ------------------------------------------------------------------ infeasible cases are reported, never relaxed
def _without_skill(state, skill):
    st = state.model_copy(deep=True)
    for s in st.stations:
        for t in s.technicians:
            t.skills = [x for x in t.skills if x != skill]
    return st


@pytest.mark.parametrize("mode", MODES)
def test_no_engine_technician_anywhere_is_reported_with_that_reason(golden, mode):
    st = _without_skill(golden, "engine")
    tasks = derive_tasks(st)
    plan = solve(st, tasks, mode=mode)
    assert plan.feasible is False
    engine_ids = {t.id for t in tasks if t.required_skill == "engine"}
    text = " ".join(plan.infeasible_reasons)
    assert all(i in text for i in engine_ids)
    assert "no engine technician" in text
    assert not [a for a in plan.assignments if a.task_id in engine_ids]          # nothing was force-fitted
    assert plan.metrics.late_or_unscheduled >= len(engine_ids)
    assert hard_violations(st, tasks, plan) == []
    # the other (non-engine) tasks are still planned: a partial plan is returned
    assert plan.metrics.tasks_scheduled == len(tasks) - len(engine_ids)


def test_deadline_that_has_already_passed_is_infeasible_not_relaxed(golden):
    st = golden.model_copy(deep=True)
    comp = next(c for c in st.components if c.component_id == GOLDEN_COMPONENT)
    comp.rul_cycles = RulEstimate(p10=0.4, p50=1, p90=2)         # may not fly again
    tasks = derive_tasks(st)
    t = next(t for t in tasks if t.tail == GOLDEN_TAIL)
    first_dep = min(l.sched_dep for l in st.legs if l.tail == GOLDEN_TAIL)
    assert t.deadline == first_dep                                # must be fixed before the next departure
    plan = solve(st, tasks, mode="cpsat")
    # SIM-017 sits at ATL for 30 minutes: a 6 h job cannot fit before the deadline even with the 3 h extension cap
    assert plan.feasible is False
    assert any(t.id in r and "ground window" in r for r in plan.infeasible_reasons)
    assert not [a for a in plan.assignments if a.task_id == t.id]


def test_baseline_that_cannot_use_its_home_base_says_so():
    s = mini_state(windows={"SIM-001": [("BBB", 2.0, 12.0)]},
                   stations=[simple_station("AAA"), simple_station("BBB")])
    plan = solve(s, [engine_task("T1", "SIM-001", 20)], mode="static_baseline")
    assert not plan.feasible
    assert "only considers the home base AAA" in plan.infeasible_reasons[0] and "other stations would work" in plan.infeasible_reasons[0]
    assert solve(s, [engine_task("T1", "SIM-001", 20)], mode="cpsat").feasible


# ------------------------------------------------------------------ individual constraints on a tiny world
def test_one_technician_cannot_do_two_jobs_at_once():
    s = mini_state(aircraft=[("SIM-001", "AAA"), ("SIM-002", "AAA")],
                   windows={"SIM-001": [("AAA", 1.0, 10.0)], "SIM-002": [("AAA", 1.0, 10.0)]},
                   stations=[simple_station("AAA", slots=2, n_tech=1, kits=2)])
    tasks = [engine_task("T1", "SIM-001", 20), engine_task("T2", "SIM-002", 20)]
    for mode in MODES:
        p = solve(s, tasks, mode=mode)
        assert_no_fallback(p, mode)
        assert p.feasible and hard_violations(s, tasks, p) == []
        a, b = sorted(p.assignments, key=lambda x: x.start)
        assert b.start >= a.end                                  # serialised on the single technician


def test_hangar_slots_cap_parallel_jobs():
    s = mini_state(aircraft=[("SIM-001", "AAA"), ("SIM-002", "AAA")],
                   windows={"SIM-001": [("AAA", 1.0, 10.0)], "SIM-002": [("AAA", 1.0, 10.0)]},
                   stations=[simple_station("AAA", slots=1, n_tech=2, kits=2)])   # two technicians but ONE hangar slot
    tasks = [engine_task("T1", "SIM-001", 20), engine_task("T2", "SIM-002", 20)]
    for mode in MODES:
        p = solve(s, tasks, mode=mode)
        assert_no_fallback(p, mode)
        assert p.feasible and hard_violations(s, tasks, p) == []
        a, b = sorted(p.assignments, key=lambda x: x.start)
        assert b.start >= a.end


def test_part_stock_is_respected_and_shortfall_reported():
    s = mini_state(aircraft=[("SIM-001", "AAA"), ("SIM-002", "AAA")],
                   windows={"SIM-001": [("AAA", 1.0, 10.0)], "SIM-002": [("AAA", 1.0, 10.0)]},
                   stations=[simple_station("AAA", slots=2, n_tech=2, kits=1)])     # ONE kit, two jobs, no shipping source
    tasks = [engine_task("T1", "SIM-001", 20), engine_task("T2", "SIM-002", 20)]
    for mode in MODES:
        p = solve(s, tasks, mode=mode)
        assert_no_fallback(p, mode)
        assert len(p.assignments) == 1 and p.feasible is False
        text = " ".join(p.infeasible_reasons)
        assert "taken by other jobs" in text and "BORESCOPE-KIT at AAA used up by other jobs" in text
        assert hard_violations(s, tasks, p) == []


def test_extension_is_used_only_when_needed_and_is_capped():
    # window 3 h, job 4 h: needs a 1 h extension (allowed: cap 3 h). A 7 h job would need 4 h: refused.
    s = mini_state(windows={"SIM-001": [("AAA", 1.0, 4.0)]}, stations=[simple_station("AAA")])
    p = solve(s, [engine_task("T1", "SIM-001", 30, duration_h=4.0)], mode="cpsat")
    assert_no_fallback(p, "cpsat")
    a = p.assignments[0]
    assert p.feasible and a.extension_h == pytest.approx(1.0)
    assert p.metrics.downtime_h == pytest.approx(1.0) and p.metrics.flights_affected == 1
    assert "1.0 h extension" in a.reason and "delays the next departure" in a.reason
    too_long = solve(s, [engine_task("T1", "SIM-001", 30, duration_h=7.5)], mode="cpsat")
    assert too_long.feasible is False and "too short" in too_long.infeasible_reasons[0]


def test_extension_is_avoided_when_another_window_fits():
    s = mini_state(windows={"SIM-001": [("AAA", 1.0, 4.0), ("AAA", 20.0, 30.0)]}, stations=[simple_station("AAA")])
    p = solve(s, [engine_task("T1", "SIM-001", 60, duration_h=4.0)], mode="cpsat")
    assert p.assignments[0].extension_h == 0.0 and p.assignments[0].start >= T0 + hrs(20)


def test_deadline_forces_the_early_slot_even_when_a_later_one_is_cheaper():
    # later window is free of shipping but after the deadline: the early one must be chosen
    stations = [simple_station("AAA", kits=0), simple_station("BBB", kits=1)]
    stations[0].parts[0].ship_from, stations[0].parts[0].ship_hours, stations[0].parts[0].ship_cost_usd = "BBB", 2.0, 800.0
    s = mini_state(windows={"SIM-001": [("AAA", 6.0, 14.0), ("BBB", 40.0, 50.0)]}, stations=stations)
    p = solve(s, [engine_task("T1", "SIM-001", 20)], mode="cpsat")
    assert_no_fallback(p, "cpsat")
    a = p.assignments[0]
    assert (a.station, a.part_source) == ("AAA", "BBB") and p.metrics.logistics_cost_usd == 800.0
    assert a.end <= T0 + hrs(20)


def test_part_that_arrives_too_late_blocks_the_slot():
    s = mini_state(windows={"SIM-001": [("AAA", 1.0, 8.0)]}, stations=[simple_station("AAA", kits=1, eta=30.0)])
    p = solve(s, [engine_task("T1", "SIM-001", 20)], mode="cpsat")
    assert not p.feasible and "cannot arrive in time" in p.infeasible_reasons[0]


def test_technician_shift_is_respected():
    st = simple_station("AAA")
    st.technicians[0].shift_end = T0 + hrs(6)                  # leaves before the job could finish in the 2h..12h window
    s = mini_state(windows={"SIM-001": [("AAA", 4.0, 12.0)]}, stations=[st])
    p = solve(s, [engine_task("T1", "SIM-001", 20, duration_h=4.0)], mode="cpsat")
    assert not p.feasible and "on shift" in p.infeasible_reasons[0]


def test_stability_penalty_keeps_an_unaffected_assignment_in_place():
    # two home windows; the prior plan used the LATER one (earliest constraint). Without a previous plan the optimizer moves
    # the job to the earlier window (it finishes sooner); WITH the previous plan the change penalty (2.0) outweighs the
    # small earliness gain (~1.0), so the assignment stays where it was.
    s = mini_state(windows={"SIM-001": [("AAA", 2.0, 10.0), ("AAA", 20.0, 30.0)]}, stations=[simple_station("AAA")])
    task = engine_task("T1", "SIM-001", 60)
    forced_late = task.model_copy(update={"earliest": T0 + hrs(18)})
    prior = solve(s, [forced_late], mode="cpsat")
    assert prior.assignments[0].start >= T0 + hrs(20)
    free = solve(s, [task], mode="cpsat")
    assert free.assignments[0].start < T0 + hrs(10)                               # no memory of the prior plan: moves earlier
    stable = solve(s, [task], mode="cpsat", previous=prior)
    assert_no_fallback(stable, "cpsat")
    assert [(a.station, a.start) for a in stable.assignments] == [(a.station, a.start) for a in prior.assignments]
    assert stable.metrics.schedule_changes == 0


def test_one_aircraft_cannot_be_in_two_jobs_at_once():
    st = simple_station("AAA", slots=2, skills=("engine", "avionics"), n_tech=2, kits=2)
    s = mini_state(windows={"SIM-001": [("AAA", 1.0, 12.0)]}, stations=[st])
    a = engine_task("T1", "SIM-001", 30, duration_h=4.0)
    b = a.model_copy(update={"id": "T2", "component_id": "SIM-001/avionics", "required_skill": "avionics", "required_part": None, "part_qty": 0})
    for mode in MODES:
        p = solve(s, [a, b], mode=mode)
        assert_no_fallback(p, mode)
        assert p.feasible
        first, second = sorted(p.assignments, key=lambda x: x.start)
        assert second.start >= first.end                         # two technicians and two slots were free: only the aircraft rule stops overlap


def test_baseline_takes_the_LAST_home_window_while_the_optimizers_take_an_earlier_one():
    s = mini_state(windows={"SIM-001": [("AAA", 2.0, 10.0), ("AAA", 20.0, 30.0)]}, stations=[simple_station("AAA")])
    task = engine_task("T1", "SIM-001", 60)
    base = solve(s, [task], mode="static_baseline").assignments[0]
    assert base.start >= T0 + hrs(20) and "Baseline policy" in base.reason
    for mode in ("greedy", "cpsat"):
        assert solve(s, [task], mode=mode).assignments[0].start < T0 + hrs(10)


# ------------------------------------------------------------------ the production guard and the engine's fallback
def test_validate_plan_flags_every_kind_of_hard_violation(golden, golden_tasks):
    from app.solver.plan_metrics import validate_plan
    good = solve(golden, golden_tasks, mode="cpsat")
    assert validate_plan(golden, golden_tasks, good.assignments, 3.0) == []
    a = good.assignments[0]
    t = next(t for t in golden_tasks if t.id == a.task_id)

    def bad(**changes):
        mutated = [x.model_copy(update=changes) if x.task_id == a.task_id else x for x in good.assignments]
        return " | ".join(validate_plan(golden, golden_tasks, mutated, 3.0))

    assert "after its deadline" in bad(start=t.deadline, end=t.deadline + hrs(t.duration_h))
    assert "lacks skill" in bad(technician_id="ATL-T2")
    assert "unknown technician" in bad(technician_id="NOPE")
    assert "no valid source" in bad(part_source="MIA") or t.required_part is None
    assert "not inside any ground window" in bad(start=a.start + hrs(0.01) - hrs(200), end=a.end - hrs(200))
    dup = good.assignments + [good.assignments[0]]
    msgs = " | ".join(validate_plan(golden, golden_tasks, dup, 3.0))
    assert "assigned twice" in msgs and "double-booked" in msgs and "two jobs at once" in msgs
    closed = golden.model_copy(deep=True)
    next(s for s in closed.stations if s.iata == a.station).hangar_slots = 0
    assert "no hangar capacity" in " | ".join(validate_plan(closed, golden_tasks, good.assignments, 3.0))
    late_part = golden.model_copy(deep=True)
    for s in late_part.stations:
        for p in s.parts:
            p.eta_hours = 500.0
    if t.required_part:
        assert "arrives after the job starts" in " | ".join(validate_plan(late_part, golden_tasks, good.assignments, 3.0))


def test_engine_falls_back_to_greedy_and_says_so_if_cpsat_returns_an_invalid_plan(golden, golden_tasks, monkeypatch):
    from app.solver import cpsat
    from app.solver.reasons import Placement

    def bogus(ctx, *a, **k):               # a "solver bug": schedules every task at its LATEST start, past the real deadline
        task = ctx.tasks[0]
        c = ctx.candidates(task)[0]
        c = c.__class__(**{**c.__dict__, "hi": c.hi + 100000})
        return cpsat.CpSatResult([Placement(c, c.hi)], {}, "optimal", "proven optimal", 0.0)

    monkeypatch.setattr(cpsat, "run", bogus)
    plan = solve(golden, golden_tasks, mode="cpsat")
    assert plan.mode == "greedy" and plan.feasible
    assert any("failed the hard-constraint check" in e.note for e in plan.evidence)
    assert hard_violations(golden, golden_tasks, plan) == []


def test_engine_falls_back_to_greedy_if_cpsat_crashes(golden, golden_tasks, monkeypatch):
    from app.solver import cpsat

    def boom(*a, **k):
        raise RuntimeError("solver exploded")

    monkeypatch.setattr(cpsat, "run", boom)
    plan = solve(golden, golden_tasks, mode="cpsat")
    assert plan.mode == "greedy" and plan.feasible
    assert any("CP-SAT failed (RuntimeError: solver exploded)" in e.note for e in plan.evidence)
def test_force_station_removes_other_stations_candidates():
    s = mini_state(windows={"SIM-001": [("AAA", 2.0, 10.0), ("BBB", 12.0, 20.0)]},
                   stations=[simple_station("AAA"), simple_station("BBB")])
    p = solve(s, [engine_task("T1", "SIM-001", 40)], mode="cpsat", force_station="BBB")
    assert p.feasible and p.assignments[0].station == "BBB"
    p2 = solve(s, [engine_task("T1", "SIM-001", 40)], mode="cpsat", force_station="CCC")
    assert not p2.feasible and "outside the forced station" in p2.infeasible_reasons[0]


def test_weights_change_the_trade_off():
    # window at AAA (kit must be shipped, $800) is early; window at BBB (kit on hand) is 30 h later, still before the deadline
    stations = [simple_station("AAA", kits=0), simple_station("BBB", kits=1)]
    stations[0].parts[0].ship_from, stations[0].parts[0].ship_hours, stations[0].parts[0].ship_cost_usd = "BBB", 1.0, 800.0
    s = mini_state(windows={"SIM-001": [("AAA", 4.0, 12.0), ("BBB", 34.0, 44.0)]}, stations=stations)
    tasks = [engine_task("T1", "SIM-001", 60)]
    frugal = solve(s, tasks, ObjectiveWeights(logistics=1.0), mode="cpsat")          # a dollar matters a lot
    hasty = solve(s, tasks, ObjectiveWeights(logistics=0.0, changes=0.0), mode="cpsat")  # dollars are free: finish sooner
    assert frugal.assignments[0].station == "BBB" and frugal.metrics.logistics_cost_usd == 0
    assert hasty.assignments[0].station == "AAA" and hasty.metrics.logistics_cost_usd == 800


# ------------------------------------------------------------------ randomized perturbations of the golden world
def _perturb(seed: int):
    rng = random.Random(seed)
    st = golden_state()
    for s in st.stations:
        if rng.random() < 0.2:
            s.hangar_slots = 0
        for t in s.technicians:
            if rng.random() < 0.2:
                t.available = False
        for p in s.parts:
            if rng.random() < 0.25:
                p.qty, p.eta_hours = rng.choice([(0, 0.0), (1, rng.uniform(5, 40))])
    return st


@pytest.mark.parametrize("seed", range(12))
def test_random_worlds_never_break_a_hard_rule_and_cpsat_never_loses_to_greedy(seed):
    st = _perturb(seed)
    tasks = derive_tasks(st)
    plans = {m: solve(st, tasks, mode=m) for m in MODES}
    for m, p in plans.items():
        assert_no_fallback(p, m)
        assert hard_violations(st, tasks, p) == [], (seed, m)
        assert p.feasible == (p.metrics.late_or_unscheduled == 0)
        assert not [r for r in p.infeasible_reasons if r.startswith("internal check failed")]
    assert plans["cpsat"].objective_value <= plans["greedy"].objective_value + 1e-3
    assert plans["cpsat"].metrics.tasks_scheduled >= plans["greedy"].metrics.tasks_scheduled


# ------------------------------------------------------------------ the candidate generator enforces the hard rules by itself
def test_every_candidate_respects_deadline_shift_and_extension_cap(golden, golden_tasks):
    from app.solver.candidates import SolveContext
    ctx = SolveContext(golden, golden_tasks)
    n = 0
    for task in ctx.tasks:
        dl = ctx.deadline_min(task)
        for c in ctx.candidates(task):
            n += 1
            assert c.lo <= c.hi
            assert c.hi + c.dur <= dl                                    # even the LATEST allowed start ends by the deadline
            assert c.hi + c.dur <= c.win_e + ctx.max_ext_min             # extension cap
            assert c.lo >= c.win_s and c.lo >= ctx.earliest_min(task)
            assert ctx.stations[c.station].hangar_slots > 0
            assert task.required_skill in next(t for t in ctx.stations[c.station].technicians if t.id == c.tech_id).skills
    assert n > 50


def test_candidates_exclude_closed_hangars_unavailable_technicians_and_missing_parts(golden, golden_tasks):
    from app.solver.candidates import SolveContext
    st = golden.model_copy(deep=True)
    for s in st.stations:
        if s.iata in ("DFW", "ATL"):
            s.hangar_slots = 0
        for t in s.technicians:
            t.available = t.id != "ORD-T1"
    ctx = SolveContext(st, derive_tasks(st))
    for task in ctx.tasks:
        for c in ctx.candidates(task):
            assert c.station not in ("DFW", "ATL") and c.tech_id != "ORD-T1"
    kit_task = next(t for t in ctx.tasks if t.required_part == "BORESCOPE-KIT")
    assert all(c.opt.pool is not None for c in ctx.candidates(kit_task))       # a part option is always attached


# ------------------------------------------------------------------ technician skill levels ("engine" = level 1, "engine:2" = level 2)
def test_skill_strings_parse_and_a_higher_level_covers_a_lower_requirement():
    from app.solver.skills import has_skill, parse_skill, skill_label, with_level
    assert parse_skill("engine") == ("engine", 1) and parse_skill("engine:2") == ("engine", 2) and parse_skill("engine:x") == ("engine", 1)
    assert has_skill(["engine"], "engine") and has_skill(["engine:2"], "engine") and has_skill(["engine:2"], "engine:2")
    assert not has_skill(["engine"], "engine:2") and not has_skill(["gear:3"], "engine")
    assert skill_label("engine") == "engine" and skill_label("engine:2") == "engine (level 2)" and with_level("engine", 2) == "engine:2"


def _senior_world(levels: list[list[str]]):
    from app.contracts.models import Technician
    st = simple_station("AAA", slots=2, n_tech=1, kits=2)
    st.technicians = [Technician(id=f"AAA-T{i + 1}", skills=sk, shift_start=T0 - hrs(12), shift_end=T0 + hrs(24 * 6)) for i, sk in enumerate(levels)]
    return mini_state(windows={"SIM-001": [("AAA", 1.0, 12.0)]}, stations=[st])


@pytest.mark.parametrize("mode", MODES)
def test_a_level_2_task_goes_to_the_level_2_technician_only(mode):
    s = _senior_world([["engine"], ["engine:2"]])
    task = engine_task("T1", "SIM-001", 30).model_copy(update={"required_skill": "engine:2"})
    p = solve(s, [task], mode=mode)
    assert_no_fallback(p, mode)
    assert p.feasible and p.assignments[0].technician_id == "AAA-T2"
    from app.solver.plan_metrics import validate_plan
    assert validate_plan(s, [task], p.assignments, 3.0) == []


def test_without_a_level_2_technician_the_task_is_infeasible_and_says_which_level():
    s = _senior_world([["engine"], ["engine"]])
    task = engine_task("T1", "SIM-001", 30).model_copy(update={"required_skill": "engine:2"})
    p = solve(s, [task], mode="cpsat")
    assert not p.feasible and not p.assignments
    assert "no engine (level 2) technician" in p.infeasible_reasons[0]


def test_a_level_2_technician_can_still_do_a_plain_task():
    s = _senior_world([["engine:2"]])
    p = solve(s, [engine_task("T1", "SIM-001", 30)], mode="cpsat")
    assert p.feasible and p.assignments[0].technician_id == "AAA-T1"


def test_validate_plan_rejects_a_junior_technician_on_a_senior_task():
    from app.solver.plan_metrics import validate_plan
    s = _senior_world([["engine"], ["engine:2"]])
    task = engine_task("T1", "SIM-001", 30).model_copy(update={"required_skill": "engine:2"})
    good = solve(s, [task], mode="cpsat")
    bad = [good.assignments[0].model_copy(update={"technician_id": "AAA-T1"})]
    assert validate_plan(s, [task], good.assignments, 3.0) == []
    assert any("lacks skill engine:2" in m for m in validate_plan(s, [task], bad, 3.0))


def test_card_min_level_becomes_the_task_skill(golden, tmp_path):
    from app.solver.cards import load_cards
    from app.solver.tasks import derive_tasks_ex
    y = tmp_path / "c.yaml"
    y.write_text(open("app/copilot/kb/task_cards.yaml").read().replace(
        "  - id: TC-ENG-BORESCOPE\n", "  - id: TC-ENG-BORESCOPE\n    min_level: 2\n", 1))
    cards = load_cards(y)
    assert next(c for c in cards if c.id == "TC-ENG-BORESCOPE").min_level == 2
    tasks = derive_tasks_ex(golden, cards=cards).tasks
    assert next(t for t in tasks if t.tail == GOLDEN_TAIL).required_skill == "engine:2"
    assert [t.id for t in tasks if ":" in t.required_skill] == [t.id for t in tasks if t.required_part == "BORESCOPE-KIT"]   # only borescope jobs need level 2


# ------------------------------------------------------------------ spare-part repositioning (advisory)
def test_repositioning_moves_stock_from_the_donor_to_the_station_that_will_need_it(golden):
    from app.solver.reposition import suggest_repositioning
    rows = {(r["part"], r["to_station"]): r for r in suggest_repositioning(golden)}
    kit = rows[("BORESCOPE-KIT", "ATL")]
    assert kit["kind"] == "reposition" and kit["from_station"] == "DFW" and kit["qty"] == 1          # SIM-017 is homed at ATL, ATL holds 0
    assert (kit["ship_hours"], kit["cost_usd"]) == (18.0, 800.0)                                      # straight from the stock record
    assert kit["task_ids"] == ["T-SIM-017-engine_2-001"] and "Advisory" in kit["note"]
    assert all(r["kind"] == "reposition" for r in rows.values())


def test_repositioning_never_drains_a_donor_below_its_own_demand(golden):
    from app.solver.reposition import suggest_repositioning
    st = golden.model_copy(deep=True)
    dfw = next(s for s in st.stations if s.iata == "DFW")
    next(p for p in dfw.parts if p.part == "BORESCOPE-KIT").qty = 1       # only one spare left at the donor, two stations are short
    rows = [r for r in suggest_repositioning(st) if r["part"] == "BORESCOPE-KIT"]
    assert sum(r["qty"] for r in rows if r["kind"] == "reposition") == 1
    assert any(r["kind"] == "shortage_no_donor" for r in rows)            # the second shortage is reported, not hidden


def test_repositioning_changes_nothing_and_is_deterministic(golden):
    from app.solver.reposition import suggest_repositioning
    before = golden.model_dump()
    a, b = suggest_repositioning(golden), suggest_repositioning(golden)
    assert a == b and golden.model_dump() == before
