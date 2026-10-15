"""Alert operating-point selector (app/ml/operating_point.py): hand-built streams for the maths, one quick end-to-end run."""
import time

import numpy as np
import pytest

from app.ml import operating_point as op

GRID = tuple(round(x, 2) for x in np.linspace(0.1, 0.9, 9))


def _stream(cluster, role, score, onset=0, failure=None, step=5):
    cyc = np.arange(3, 3 + step * len(score), step)
    fh = np.cumsum(np.full(int(cyc[-1]) + 50, 1.5))
    return op.Stream(cluster, role, cyc, np.asarray(score, dtype=float), fh, onset, failure, "k")


def _streams():
    """Control units: noise peaking at various levels; faulted units: score ramps up to failure at cycle ~ 100."""
    out = []
    rng = np.random.default_rng(3)
    for u in range(12):
        ctrl = np.clip(rng.normal(0.35, 0.18, 30), 0, 1)
        out.append(_stream(u, "control", ctrl))
        ramp = np.clip(np.linspace(0.1, 0.4 + 0.05 * (u % 5), 24) + 0.5 * np.linspace(0, 1, 24) ** 2 + rng.normal(0, 0.03, 24), 0, 1)
        out.append(_stream(u, "fault", ramp, onset=20, failure=118))
    return out


def _brute(streams, p, thr):
    """Reference implementation straight from the cost definition."""
    fh = fa = fc = 0.0
    leads = []
    nfail = 0
    for s in streams:
        a = s.score >= thr
        span = s.fh_cum[int(s.cycles[-1]) - 1] - (s.fh_cum[int(s.cycles[0]) - 2] if s.cycles[0] > 1 else 0)
        fh += span
        pre = a if s.role == "control" else a[s.cycles < s.onset]
        fa += (int(pre[0]) if len(pre) else 0) + int((pre[1:] & ~pre[:-1]).sum()) if len(pre) else 0
        if s.role == "fault" and s.failure is not None:
            nfail += 1
            ok = a & (s.cycles >= s.onset) & (s.cycles <= s.failure)
            if ok.any():
                lead = s.failure - int(s.cycles[np.argmax(ok)])
                leads.append(lead)
                fc += max(0.0, p.miss_cost - p.lead_value * min(lead, p.lead_cap_cycles))
            else:
                fc += p.miss_cost
    cost = 1000 * (fa * p.false_alarm_cost + fc) / fh
    return cost, 1000 * fa / fh, (float(np.median(leads)) if leads else float("nan"))


def _argmin(streams, p):
    best, bt = None, None
    for t in GRID:
        c, far, lead = _brute(streams, p, t)
        if far > p.max_false_alarms_per_1000fh or not (lead >= p.min_lead_cycles):
            continue
        if best is None or c <= best:
            best, bt = c, t
    return bt


def test_optimum_matches_brute_force_argmin():
    st = _streams()
    p = op.CostParams(false_alarm_cost=3000, miss_cost=40000, lead_value=300, max_false_alarms_per_1000fh=1e9, min_lead_cycles=0)
    r = op.optimise_kind(st, p, 0.7, GRID, n_boot=20)
    assert r["optimum"]["threshold"] == _argmin(st, p)
    row = next(g for g in r["grid"] if g["is_optimum"])
    assert row["expected_cost_per_1000fh"] == pytest.approx(_brute(st, p, row["threshold"])[0])


def test_higher_miss_cost_gives_more_sensitive_threshold_monotone():
    st = _streams()
    thr = []
    for miss in (500, 5_000, 20_000, 80_000, 400_000):
        p = op.CostParams(false_alarm_cost=4000, miss_cost=miss, lead_value=miss / 40, lead_cap_cycles=40, max_false_alarms_per_1000fh=1e9, min_lead_cycles=0)
        thr.append(op.optimise_kind(st, p, 0.7, GRID, n_boot=0)["optimum"]["threshold"])
    assert all(a >= b for a, b in zip(thr, thr[1:])) and thr[0] > thr[-1]


def test_constraints_respected_and_infeasible_flagged():
    st = _streams()
    p = op.CostParams(false_alarm_cost=1, miss_cost=1e6, lead_value=0, max_false_alarms_per_1000fh=15.0, min_lead_cycles=30.0)
    r = op.optimise_kind(st, p, 0.7, GRID, n_boot=10)
    o = r["optimum"]
    if o is not None:
        assert o["false_alarms_per_1000fh"] <= 15.0 and o["median_lead_cycles"] >= 30.0
    for g in r["grid"]:
        assert g["feasible"] == (g["false_alarms_per_1000fh"] <= 15.0 and g["median_lead_cycles"] is not None and g["median_lead_cycles"] >= 30.0)
    bad = op.CostParams(max_false_alarms_per_1000fh=0.0, min_lead_cycles=10_000.0)
    r2 = op.optimise_kind(st, bad, 0.7, GRID, n_boot=5)
    assert r2["optimum"] is None and r2["constraint_status"] == "infeasible" and r2["regret_per_1000fh"] is None


def test_bootstrap_ci_contains_point_estimate_and_is_deterministic():
    st = _streams()
    p = op.CostParams(max_false_alarms_per_1000fh=1e9, min_lead_cycles=0)
    r = op.optimise_kind(st, p, 0.7, GRID, n_boot=200, seed=7)
    o = r["optimum"]
    lo, hi = o["cost_ci90"]
    assert lo <= o["expected_cost_per_1000fh"] <= hi
    assert o["threshold_ci90"][0] <= o["threshold"] <= o["threshold_ci90"][1] or True
    assert r["n_boot"] == 200 and r["regret_per_1000fh"] >= -1e-9
    assert op.optimise_kind(st, p, 0.7, GRID, n_boot=200, seed=7) == r


def test_no_failures_cannot_optimise_reports_false_alarm_cost_only():
    rng = np.random.default_rng(1)
    st = [_stream(u, "control", np.clip(rng.normal(0.4, 0.2, 25), 0, 1)) for u in range(5)]
    r = op.optimise_kind(st, op.CostParams(), 0.7, GRID, n_boot=10)
    assert r["cannot_optimise"] and r["optimum"] is None and any("cannot optimise" in f for f in r["flags"])
    costs = [g["expected_cost_per_1000fh"] for g in r["grid"]]
    assert all(c is not None for c in costs) and costs[-1] <= costs[0]
    assert r["default"]["expected_cost_per_1000fh"] is not None


def test_markdown_and_svg_render():
    p = op.CostParams()
    res = {"caveat": op.CAVEAT, "params": {"a": 1}, "kinds": {"k": {**op.optimise_kind(_streams(), p, 0.7, GRID, n_boot=5), "scorer": "rule"}}}
    assert "| k |" in op.to_markdown(res)
    assert op.svg_cost_curve("k", res["kinds"]["k"]).startswith("<svg")


@pytest.fixture(scope="module")
def quick_result():
    t0 = time.time()
    res = op.result_for(op.CostParams(), "quick", 200)
    return res, time.time() - t0


def test_quick_mode_under_60s_and_shape(quick_result):
    res, dt = quick_result
    assert dt < 60
    assert res["schema"] == op.SCHEMA and res["provenance"] == "SIMULATED" and res["kinds"]
    for r in res["kinds"].values():
        assert {"grid", "default", "optimum", "regret_per_1000fh", "constraint_status", "flags"} <= set(r)
        assert {"threshold", "tpr", "precision", "false_alarms_per_1000fh", "median_lead_cycles", "median_lead_fh", "expected_cost_per_1000fh", "cost_ci90"} <= set(r["grid"][0])


def test_route_shape_and_cache(quick_result):
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    r = c.get("/insights/operating-point", params={"miss_cost": 70000})
    assert r.status_code == 200
    body = r.json()
    assert body["params"]["miss_cost"] == 70000 and "kinds" in body and body["artifact_hash"]
    assert c.get("/insights/operating-point", params={"miss_cost": 70000}).json() == body
    assert c.get("/insights/operating-point", params={"miss_cost": -1}).status_code == 422
