"""Estimated plan cost (app/solver/costs.py): simulated price list, adds up, and the baseline pays for what it cannot do in time."""
from __future__ import annotations

from datetime import timedelta

import pytest

from app.solver import costs, derive_tasks, solve
from app.solver.golden import golden_state
from app.solver.plan_metrics import compute_metrics
from tests.solver_helpers import engine_task, mini_state, simple_station


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
