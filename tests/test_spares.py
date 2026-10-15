"""Spares pool sizing: Croston / SBA hand values, ADI-CV2 classes, single-station Poisson case, marginal analysis, Monte-Carlo check, route."""
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from scipy import stats

from app.insights import reliability_pack as rp
from app.main import app
from app.ops import spares
from app.sim.clock import START


def pool(nodes, depot="A", tat=10.0, nff=0.0, cond=0.0, price=10_000.0, kind="brakes"):
    return spares.Pool(kind, "BRAKE-ASSY", price, depot, nodes, tat, nff, cond, 3.0, 90.0)


def hand_dataset(days=364.0):
    recs = []
    for i in range(1, 25):
        for j in range(3 if i % 2 else 1):
            recs.append(rp.RemovalRec("brakes", f"SIM-{i:03d}", "brakes", "replace", "failure", True, False, 0.2, START + timedelta(days=(i * 13 + j * 101) % days)))
    for i in range(1, 25, 3):
        recs.append(rp.RemovalRec("avionics", f"SIM-{i:03d}", "avionics", "nff_return", "nff", True, True, 0.9, START + timedelta(days=(i * 29) % days)))
    return rp.Dataset(recs, rp.Exposure({"brakes": 24, "avionics": 24}, {"brakes": 1e5, "avionics": 1e5}, {"brakes": 5e4, "avionics": 5e4}, days),
                      {"basis": "test"})


def test_croston_sba_hand_values():
    s = [0, 2, 0, 0, 4, 0]
    # first demand 2 at t=2: z=2, p=2; demand 4 after 3 periods: z=2.2, p=2.1
    assert spares.croston(s, 0.1) == pytest.approx(2.2 / 2.1)
    assert spares.sba(s, 0.1) == pytest.approx(0.95 * 2.2 / 2.1)
    assert spares.croston([0, 0, 0]) == 0.0


def test_adi_cv2_classification_and_rule():
    assert spares.adi_cv2([3, 3, 3, 3]) == (1.0, 0.0)
    adi, cv2 = spares.adi_cv2([0, 0, 2, 0, 0, 6])
    assert adi == 3.0 and cv2 == pytest.approx(0.25)
    assert spares.classify(1.0, 0.0) == "smooth" and spares.classify(1.0, 1.0) == "erratic"
    assert spares.classify(3.0, 0.25) == "intermittent" and spares.classify(3.0, 1.0) == "lumpy"
    assert spares.classify(*spares.adi_cv2([0, 0, 0])) == "none"
    assert [spares.choose_method(k) for k in ("smooth", "erratic", "intermittent", "lumpy")] == ["croston", "croston", "sba", "bootstrap"]
    f = spares.forecast_series("brakes", "A", [0, 0, 2, 0, 0, 6] * 4, lead_days=14.0)
    assert f.klass == "intermittent" and f.method == "sba" and f.ltd_q["p95"] >= f.ltd_q["p50"] >= 0


def test_single_station_matches_poisson():
    p = pool([spares.Node("A", lam=0.2, ship_days=0.0)], tat=10.0)   # pipeline Poisson(2)
    for s in range(0, 6):
        ev = spares.evaluate(p, {"A": s})
        want = float(stats.poisson.cdf(s - 1, 2.0)) if s > 0 else 0.0
        assert ev["stations"]["A"]["fill"] == pytest.approx(want, abs=1e-9)
        assert ev["stations"]["A"]["ebo"] == pytest.approx(sum(max(k - s, 0) * stats.poisson.pmf(k, 2.0) for k in range(200)), abs=1e-6)
    plan = spares.recommend([p], target_fill=0.9)
    s = plan.stock["brakes"]["A"]
    assert stats.poisson.cdf(s - 1, 2.0) >= 0.9 > stats.poisson.cdf(s - 2, 2.0)


def two_echelon():
    return pool([spares.Node("A", 0.15, 0.0, role="main_base"), spares.Node("B", 0.1, 1.0), spares.Node("C", 0.06, 2.0), spares.Node("D", 0.03, 1.5)],
                tat=14.0, nff=0.2, cond=0.1)


def test_marginal_analysis_is_monotone_in_budget():
    pools = [two_echelon(), pool([spares.Node("A", 0.1, 0.0), spares.Node("E", 0.08, 1.0)], tat=20.0, price=38_000.0, kind="hydraulics")]
    last_fill, last_spend = -1.0, -1.0
    for b in (0, 20_000, 60_000, 150_000, 400_000, 1_000_000):
        plan = spares.recommend(pools, target_fill=None, budget_usd=b)
        fill = sum(n.lam * spares.evaluate(p, plan.stock[p.kind])["stations"][n.station]["fill"] for p in pools for n in p.nodes)
        assert plan.spend_usd <= b + 1e-6
        assert fill >= last_fill - 1e-12 and plan.spend_usd >= last_spend
        last_fill, last_spend = fill, plan.spend_usd
    assert last_spend > 0 and last_fill > 0.1


def test_target_met_and_cost_mode_stops():
    p = two_echelon()
    plan = spares.recommend([p], target_fill=0.9)
    ev = spares.evaluate(p, plan.stock["brakes"])
    assert all(r["fill"] >= 0.9 for r in ev["stations"].values())
    cost = spares.recommend([p], target_fill=None, budget_usd=None)
    assert cost.mode == "cost" and sum(cost.stock["brakes"].values()) >= sum(plan.stock["brakes"].values()) - 3


def _check(p, target):
    plan = spares.recommend([p], target_fill=target)
    stock = plan.stock[p.kind]
    ana = spares.network_fill(p, stock)
    sim = spares.simulate(p, stock, trials=500, seed=11)
    return ana, sim["fill"]


def test_analytic_vs_simulated_fill_config_a():
    ana, sim = _check(two_echelon(), 0.9)
    assert abs(ana - sim) <= 0.03


def test_analytic_vs_simulated_fill_config_b():
    p = pool([spares.Node("H", 0.4, 0.0, role="main_base"), spares.Node("X", 0.12, 0.5), spares.Node("Y", 0.2, 3.0)], depot="H", tat=25.0, nff=0.1, cond=0.05)
    ana, sim = _check(p, 0.85)
    assert abs(ana - sim) <= 0.03


def test_approximation_breaks_under_campaigns():
    p = two_echelon()
    stock = spares.recommend([p], target_fill=0.9).stock["brakes"]
    v = spares.validate(p, stock, trials=200)
    cases = {c["case"]: c for c in v["cases"]}
    assert cases["base"]["within_3pp"] and not cases["campaign_x3"]["within_3pp"]


def test_simulation_deterministic():
    p = two_echelon()
    st = {"A": 2, "B": 1, "C": 1, "D": 0}
    a = spares.simulate(p, st, trials=50, seed=3)
    b = spares.simulate(p, st, trials=50, seed=3)
    assert a == b
    assert spares.from_simulator(hand_dataset()) == spares.from_simulator(hand_dataset())


def test_from_simulator_payload_and_comparison():
    out = spares.from_simulator(hand_dataset(), target_fill=0.9, validate_trials=50)
    assert out["simulated"] is True and out["provenance"] == "SIMULATED"
    brakes = next(k for k in out["kinds"] if k["kind"] == "brakes")
    assert brakes["demand_per_year"] > 0 and brakes["net_pipeline"]["repairable_return_fraction"] <= 1.0
    rows = [r for r in out["items"] if r["kind"] == "brakes"]
    assert {"recommended", "current", "fill_rate", "ebo", "aog_risk", "holding_cost_usd_year", "sensitivity"} <= set(rows[0])
    assert set(rows[0]["sensitivity"]) == {"tat_-30", "tat_+30", "demand_-20", "demand_+20"}
    s = rows[0]["sensitivity"]
    assert s["tat_+30"]["fill_rate"] <= s["tat_-30"]["fill_rate"] and s["demand_+20"]["fill_rate"] <= s["demand_-20"]["fill_rate"]
    assert all(c["shortfall"] > 0 for c in out["shortfalls"])
    assert out["validation"] and out["validation"][0]["cases"][0]["case"] == "base"
    avi = next(k for k in out["kinds"] if k["kind"] == "avionics")
    assert avi["net_pipeline"]["nff_share"] == 1.0


def test_routes(monkeypatch):
    ds = hand_dataset()
    monkeypatch.setattr(rp, "live_world", lambda: None)
    monkeypatch.setattr(rp, "cached_backfill", lambda *a, **k: ds)
    c = TestClient(app)
    r = c.get("/ops/spares", params={"target_fill": 0.9})
    assert r.status_code == 200
    j = r.json()
    assert j["simulated"] is True and j["provenance"] == "SIMULATED" and j["policy"]["mode"] == "target"
    assert j["items"] and j["comparison"] and "shortfalls" in j and "summary" in j
    r = c.get("/ops/spares", params={"budget_usd": 100000})
    assert r.json()["policy"]["mode"] == "budget" and r.json()["summary"]["recommended_value_usd"] <= 100000
    d = c.get("/ops/spares/demand").json()
    assert d["simulated"] is True and d["forecasts"] and {"class", "method", "lead_time_demand_quantiles"} <= set(d["forecasts"][0])
