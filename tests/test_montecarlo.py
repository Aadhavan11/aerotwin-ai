"""Monte-Carlo plan risk (app/whatif/montecarlo.py, app/ops/mc_api.py)."""
from __future__ import annotations

import time
from dataclasses import replace

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.contracts.models import Assignment, Plan
from app.insights import disruptions
from app.main import app
from app.solver.plan_metrics import compute_metrics
from app.whatif import montecarlo as mc
from tests.solver_helpers import T0, engine_task, hrs, mini_state, simple_station


def _plan(state, tasks, spec):
    """spec: [(task, start_h, end_h)] -> a hand-built plan at AAA."""
    assigns = [Assignment(task_id=t.id, tail=t.tail, station="AAA", start=T0 + hrs(s), end=T0 + hrs(e), technician_id=f"AAA-T{i + 1}",
                          part_source=None, window_hours=18.0, extension_h=0.0, reason="test") for i, (t, s, e) in enumerate(spec)]
    metrics = compute_metrics(state, tasks, assigns)
    return Plan(id="P-T", created=T0, mode="cpsat", feasible=True, assignments=assigns, metrics=metrics, solve_seconds=0.0)


@pytest.fixture()
def world():
    disruptions.reset()
    st = mini_state(aircraft=[("SIM-001", "AAA"), ("SIM-002", "AAA")],
                    windows={"SIM-001": [("AAA", 2, 20)], "SIM-002": [("AAA", 2, 60)]},
                    stations=[simple_station("AAA", slots=2, n_tech=2, kits=2, eta=10.0)])
    t1 = engine_task("T1", "SIM-001", deadline_h=20.5, duration_h=4.0)               # needs the late part, tight deadline
    t2 = engine_task("T2", "SIM-002", deadline_h=200.0, duration_h=4.0, part=None)   # no part, loose deadline
    tasks = [t1, t2]
    plan = _plan(st, tasks, [(t1, 15.0, 19.0), (t2, 5.0, 9.0)])
    yield st, tasks, plan
    disruptions.reset()


def _run(world, cfg=None, n=300, seed=7):
    st, tasks, plan = world
    return mc.evaluate(st, plan, tasks, cfg or mc.MCConfig(), n, seed)


def test_seeded_determinism(world):
    a, b = _run(world, seed=3), _run(world, seed=3)
    assert np.array_equal(a["_total"], b["_total"])
    assert a["risk_ranking"] == b["risk_ranking"]
    assert not np.array_equal(a["_total"], _run(world, seed=4)["_total"])


def test_zero_uncertainty_collapses_to_deterministic_plan(world):
    st, tasks, plan = world
    r = mc.evaluate(st, plan, tasks, mc.MCConfig.zero(), 50, 1)
    assert np.ptp(r["_total"]) == 0.0
    assert r["_total"][0] == pytest.approx(plan.metrics.total_cost_usd)
    assert r["flights_delayed"]["max"] == 0 and r["flights_cancelled"]["max"] == 0 and r["aog_h"]["max"] == 0
    assert r["p_miss_any_deadline"] == 0.0
    assert r["cost_usd"]["cvar95"] == pytest.approx(r["cost_usd"]["mean"], abs=1e-3)


def test_higher_part_delay_raises_expected_cost(world):
    base = mc.MCConfig.zero()
    means = [_run(world, replace(base, part_delay_sigma=s), n=400)["cost_usd"]["mean"] for s in (0.0, 0.3, 0.6, 1.0)]
    assert all(b > a for a, b in zip(means, means[1:])), means


def test_cvar_at_least_mean_and_histogram(world):
    r = _run(world)
    c = r["cost_usd"]
    assert c["cvar95"] >= c["mean"] and c["p95"] >= c["p50"]
    h = r["histogram"]
    assert sum(h["counts"]) == 300 and len(h["bin_edges"]) == len(h["counts"]) + 1


def test_risk_ranking_orders_the_tight_job_first(world):
    r = _run(world, mc.MCConfig(weather_prob=0.0, failure_enabled=False), n=400)
    rank = r["risk_ranking"]
    assert [x["task_id"] for x in rank] == ["T1", "T2"]
    assert rank[0]["p_late"] > 0.0 and rank[0]["p_late"] > rank[1]["p_late"]
    assert rank[0]["expected_cost_usd"] > rank[1]["expected_cost_usd"] and rank[0]["rank"] == 1
    assert 0.99 <= sum(x["share_of_risk_cost"] for x in rank) <= 1.01


def test_failure_before_job_creates_aog(world):
    st, tasks, plan = world
    st = st.model_copy(update={"components": [c.model_copy(update={"rul_cycles": c.rul_cycles.model_copy(update={"p10": 0.5, "p50": 1.0, "p90": 2.0})})
                                               for c in st.components]})
    r = mc.evaluate(st, plan, tasks, replace(mc.MCConfig.zero(), failure_enabled=True), 200, 5)
    assert r["p_unplanned_aog"] > 0.5 and r["aog_h"]["mean"] > 0


def test_cache_by_plan_hash_n_seed(world):
    st, tasks, plan = world
    mc.clear_cache()
    a = mc.run(st, plan, tasks, mc.MCConfig(), 100, 9)
    b = mc.run(st, plan, tasks, mc.MCConfig(), 100, 9)
    c = mc.run(st, plan, tasks, mc.MCConfig(), 100, 10)
    assert a["cached"] is False and b["cached"] is True and c["cached"] is False
    assert a["cost_usd"] == b["cost_usd"]


def test_config_overrides_validated():
    with pytest.raises(ValueError):
        mc.MCConfig.from_overrides({"nope": 1})
    with pytest.raises(ValueError):
        mc.MCConfig.from_overrides({"noshow_prob": 2})
    assert mc.MCConfig.from_overrides({"part_delay_sigma": 1}).part_delay_sigma == 1.0


@pytest.fixture()
def client():
    c = TestClient(app)
    c.post("/scenario/reset", json={"name": "golden"})
    mc.clear_cache()
    yield c
    disruptions.reset()


def test_route_shape_and_performance(client):
    t0 = time.perf_counter()
    r = client.post("/ops/montecarlo", json={"n": 500, "seed": 11})
    assert r.status_code == 200, r.text
    assert time.perf_counter() - t0 < 10.0
    d = r.json()
    for k in ("simulated", "provenance", "n", "seed", "cost_usd", "flights_delayed", "flights_cancelled", "delay_min", "aog_h", "deadline_misses",
              "p_miss_any_deadline", "risk_ranking", "histogram", "plan", "comparison"):
        assert k in d, k
    assert d["simulated"] is True and d["n"] == 500
    assert set(d["cost_usd"]) >= {"mean", "p50", "p90", "p95", "cvar95"}
    assert d["cost_usd"]["cvar95"] >= d["cost_usd"]["mean"]
    assert d["comparison"] is not None and {"optimised", "baseline", "delta_mean_cost_usd"} <= set(d["comparison"])
    assert client.get("/ops/montecarlo/last").json()["plan"]["id"] == d["plan"]["id"]
    again = client.post("/ops/montecarlo", json={"n": 500, "seed": 11}).json()
    assert again["cost_usd"] == d["cost_usd"] and again["cached"] is True


def test_route_errors(client):
    assert client.post("/ops/montecarlo", json={"n": 5001}).status_code == 422
    assert client.post("/ops/montecarlo", json={"plan_id": "P-nope"}).status_code == 404
    assert client.post("/ops/montecarlo", json={"params": {"bogus": 1}}).status_code == 422
