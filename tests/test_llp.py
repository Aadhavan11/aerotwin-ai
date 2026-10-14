"""LLP model and shop-visit forecast: remaining-cycle math, thresholds, seeding, driver choice, pipeline, routes."""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.ops import llp

NOW = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean():
    llp.clear_replacements()
    yield
    llp.clear_replacements()


def tail_in(cycles=5000.0, cpd=4.0, tail="T-1"):
    return llp.TailInputs(tail, cycles, cpd, {"engine_1": cycles, "engine_2": cycles})


def test_status_thresholds():
    assert llp.status_for(5000) == "ok"
    assert llp.status_for(2000) == "ok"
    assert llp.status_for(1999.9) == "watch"
    assert llp.status_for(500) == "watch"
    assert llp.status_for(499.9) == "alert"
    assert llp.status_for(0.1) == "alert"
    assert llp.status_for(0) == "expired"
    assert llp.status_for(-5) == "expired"


def test_remaining_math_and_dates():
    t = tail_in(cycles=5000, cpd=5.0)
    p = llp.part_state(t, "hpt_disk_x", "hpt_disk", "engine_1", 5000.0, NOW)
    assert p["remaining_cycles"] == pytest.approx(20000.0 - p["csn"], abs=0.1)
    assert p["remaining_days"] == pytest.approx(p["remaining_cycles"] / 5.0, abs=0.1) or p["limit_driver"] == "calendar"
    assert p["simulated"] is True and p["provenance"] == "SIMULATED"
    # consuming 100 more cycles lowers remaining by 100 (no wrap at this stagger)
    q = llp.part_state(t, "hpt_disk_x", "hpt_disk", "engine_1", 5100.0, NOW)
    if q["csn"] > p["csn"]:
        assert p["remaining_cycles"] - q["remaining_cycles"] == pytest.approx(100.0, abs=0.2)


def test_deterministic_seeding():
    a = llp.tail_parts(tail_in(), NOW)
    b = llp.tail_parts(tail_in(), NOW)
    assert a == b
    assert len(a) == 12
    assert {p["part_key"] for p in a} == set(llp.CATALOG)
    assert len({p["serial"] for p in a}) == 12
    other = llp.tail_parts(tail_in(tail="T-2"), NOW)
    assert [p["csn"] for p in a] != [p["csn"] for p in other]
    assert all(0 <= p["csn"] < p["cycle_limit"] for p in a)


def test_replacement_resets_part():
    t = tail_in(cycles=5000)
    pos = "engine_1:hpt_disk"
    before = next(p for p in llp.tail_parts(t, NOW) if p["position"] == pos)
    llp.record_replacement("T-1", pos, at_cycles=5000, when=NOW, serial="NEWSN1")
    after = next(p for p in llp.tail_parts(t, NOW) if p["position"] == pos)
    assert after["csn"] == 0 and after["remaining_cycles"] == 20000.0
    assert after["replaced"] is True and after["serial"] == "NEWSN1"
    assert before["csn"] != 0


def test_calendar_limit_can_drive():
    t = tail_in(cycles=0, cpd=0.05)  # almost no flying: calendar binds
    p = llp.part_state(t, "nlg_leg", "nlg_leg", "airframe", 0.0, NOW)
    assert p["limit_driver"] == "calendar"


def test_registry_tolerates_absence():
    assert llp.apply_install_registry(object()) == 0
    assert llp.apply_install_registry(None) == 0


def fc(**kw):
    base = dict(tail="T", engine="engine_1", as_of=NOW, cycles_per_day=4.0, margin_c=60.0, loss_per_cycle=0.007, loss_rel_sd=0.2,
                margin_basis="x", llp_remaining_cycles=10000.0, llp_part="engine_1:hpt_disk", rul=None)
    base.update(kw)
    return llp.forecast_engine(**base)


def test_driver_selection():
    assert fc()["driver"] == "egt_margin"  # (60-15)/0.007 = 6428 < 10000
    assert fc(llp_remaining_cycles=1000.0)["driver"] == "llp"
    assert fc(llp_remaining_cycles=1000.0)["driver_llp_part"] == "engine_1:hpt_disk"
    assert fc(rul=(20.0, 60.0, 90.0))["driver"] == "rul"
    # capped RUL is censored and never a driver
    out = fc(rul=(100.0, 125.0, 125.0))
    assert out["driver"] == "egt_margin" and out["rul_censored"] is True


def test_window_and_dates():
    out = fc(llp_remaining_cycles=400.0)
    assert out["p50_flights"] == 400.0
    assert out["p10_flights"] <= out["p50_flights"] <= out["p90_flights"]
    assert out["p50_date"] == "2027-01-20T12:00:00Z"  # 400 flights / 4 per day = 100 days
    

def test_overdue():
    out = fc(llp_remaining_cycles=-10.0)
    assert out["overdue"] is True and out["driver"] == "llp"


def test_theil_sen_slope():
    xs = list(range(1, 21))
    ys = [70 - 0.5 * x for x in xs]
    slope, rel = llp.theil_sen(xs, ys)
    assert slope == pytest.approx(-0.5)
    assert rel == pytest.approx(0.0, abs=1e-9)


def test_pipeline_cumulative_monotone():
    rows = [fc(llp_remaining_cycles=r) for r in (-5.0, 100.0, 900.0, 3000.0, 20000.0)]
    pipe = llp.pipeline(rows)
    assert len(pipe) == llp.HORIZON_MONTHS
    cum = [m["cumulative_expected"] for m in pipe]
    assert all(b >= a for a, b in zip(cum, cum[1:]))
    c50 = [m["cumulative_p50_count"] for m in pipe]
    assert all(b >= a for a, b in zip(c50, c50[1:]))
    assert c50[-1] <= len(rows) and cum[-1] <= len(rows) + 1e-6
    assert pipe[0]["p50_count"] >= 2  # the overdue and the 100-flight engine


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_routes_shapes(client):
    r = client.get("/ops/llp")
    assert r.status_code == 200
    j = r.json()
    assert j["simulated"] is True and j["provenance"] == "SIMULATED"
    assert j["count"] == len(j["parts"]) == 12 * 24
    assert set(j["summary"]) == {"ok", "watch", "alert", "expired"}
    tail = j["parts"][0]["tail"]
    one = client.get(f"/ops/llp/{tail}").json()
    assert one["tail"] == tail and one["count"] == 12 and one["aircraft"]["cycles_per_day"] > 0
    filt = client.get("/ops/llp", params={"tail": tail}).json()
    assert filt["count"] == 12
    assert client.get("/ops/llp/NOPE").status_code == 404
    assert client.get("/ops/llp", params={"tail": "NOPE"}).status_code == 404


def test_shop_visits_route(client):
    j = client.get("/ops/shop-visits").json()
    assert j["simulated"] is True and j["provenance"] == "SIMULATED"
    assert j["horizon_months"] == 12 and len(j["pipeline"]) == 12
    assert j["count"] == len(j["engines"]) > 0
    ranks = [e["rank"] for e in j["engines"]]
    assert ranks == list(range(1, len(ranks) + 1))
    p50 = [e["p50_flights"] for e in j["engines"]]
    assert p50 == sorted(p50)
    assert {e["driver"] for e in j["engines"]} <= {"egt_margin", "llp", "rul"}
    assert sum(j["drivers"].values()) == j["count"]
    cum = [m["cumulative_expected"] for m in j["pipeline"]]
    assert cum == sorted(cum)
    t = j["engines"][0]["tail"]
    assert client.get("/ops/shop-visits", params={"tail": t}).json()["count"] == 2
