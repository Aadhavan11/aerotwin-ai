"""Estimated plan cost (app/solver/costs.py): simulated price list, adds up, and the baseline pays for what it cannot do in time."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.contracts.models import Assignment, RulEstimate
from app.sim.fleet import generate_fleet
from app.solver import costs, derive_tasks, solve
from app.solver.golden import golden_state
from app.solver.plan_metrics import compute_metrics
from tests.solver_helpers import engine_task, mini_state, simple_station


def test_fleet_required_parts_have_nonzero_prices() -> None:
    for state in (golden_state(), generate_fleet()):
        station_parts = {part.part for station in state.stations for part in station.parts}
        required_parts = {task.required_part for task in derive_tasks(state) if task.required_part}
        assert station_parts
        for part in station_parts | required_parts:
            assert costs.PART_PRICE_USD.get(part, 0.0) > 0, part


@pytest.mark.parametrize("kind", ["apu", "tires", "ecs", "electrical"])
def test_new_kind_task_has_nonzero_cost(kind: str) -> None:
    state = golden_state()
    component = next(c for c in state.components if c.kind == kind)
    component.rul_cycles = RulEstimate(p10=18, p50=22, p90=25)
    task = next(t for t in derive_tasks(state) if t.component_id == component.component_id)
    assignment = Assignment(
        task_id=task.id, tail=task.tail, station=state.aircraft[0].location,
        start=state.now, end=state.now + timedelta(hours=task.duration_h),
        technician_id=None, window_hours=task.duration_h, reason="test",
    )
    cost = costs.job_cost(task, assignment)
    assert cost["parts"] > 0
    assert cost["parts"] == costs.PART_PRICE_USD[task.required_part] * task.part_qty
    assert cost["total"] > 0


def test_job_cost_follows_the_price_list():
    st = mini_state(aircraft=[("SIM-001", "AAA")], windows={"SIM-001": [("AAA", 1, 9)]}, stations=[simple_station("AAA")])
    task = engine_task("T1", "SIM-001", 20, duration_h=2.0)
    plan = solve(st, [task], mode="cpsat")
    assert plan.assignments, plan.infeasible_reasons
    a = plan.assignments[0]
    c = costs.job_cost(task, a, shipping_usd=700.0)
    h = (a.end - a.start).total_seconds() / 3600
    assert c["parts"] == costs.PART_PRICE_USD["BORESCOPE-KIT"]
    assert c["labor"] == pytest.approx(h * costs.CREW_SIZE * costs.LABOR_RATE_USD_H, abs=0.01)
    assert c["bay"] == pytest.approx(h * costs.BAY_RATE_USD_H, abs=0.01)
    assert c["overhead"] == costs.JOB_OVERHEAD_USD
    assert c["total"] == pytest.approx(c["parts"] + c["labor"] + c["bay"] + c["overhead"] + 700.0 + c["delay"], abs=0.01)


def test_plan_metrics_add_up_and_baseline_pays_for_unscheduled_work():
    st = golden_state()
    tasks = derive_tasks(st)
    opt = solve(st, tasks, mode="cpsat").metrics
    base = solve(st, tasks, mode="static_baseline").metrics
    for m in (opt, base):
        parts = m.cost_parts_usd + m.cost_labor_usd + m.cost_bay_usd + m.cost_overhead_usd + m.logistics_cost_usd + m.cost_delay_usd + m.cost_exposure_usd
        assert m.total_cost_usd == pytest.approx(parts, abs=0.05)
        assert m.cost_exposure_usd == (m.late_or_unscheduled * costs.DEFERRAL_ALLOWANCE_USD)
    # the same work costs the same parts; the baseline's extra cost is shipping, delay and the deferral allowance
    assert opt.total_cost_usd <= base.total_cost_usd
    assert opt.total_cost_usd > 0


def test_no_tasks_costs_nothing():
    st = golden_state()
    m = compute_metrics(st, [], [])
    assert m.total_cost_usd == 0.0
