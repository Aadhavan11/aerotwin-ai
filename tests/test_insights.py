"""Insights endpoints (/insights/*): shapes, determinism, weather propagation, Weibull fit, reset. All SIMULATED."""
import random
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.contracts.models import Leg, TelemetryRow
from app.insights import disruptions, reliability
from app.main import app
from app.ml import health
from app.ml.sensors import GENERIC

UTC = timezone.utc
T0 = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


@pytest.fixture()
def client():
    c = TestClient(app)
    c.post("/scenario/reset", json={"name": "golden"})
    disruptions.reset()
    yield c
    disruptions.reset()


def _leg(i, tail, dep, arr):
    d = T0 + timedelta(hours=4 * i)
    return Leg(id=f"L-{tail}-{i}", tail=tail, dep=dep, arr=arr, sched_dep=d, sched_arr=d + timedelta(hours=2))


# ---- data quality -------------------------------------------------------------------------------
def test_data_quality_shape_and_counts(client):
    j = client.get("/insights/data-quality").json()
    assert j["simulated"] is True and j["as_of"]
    assert set(j["counts"]) == {"ok", "degraded", "bad"}
    assert sum(j["counts"].values()) == 240 == len(j["items"])
    for it in j["items"]:
        assert set(it) == {"tail", "component_id", "kind", "status", "reasons", "faults"}
        assert it["status"] in ("ok", "degraded", "bad")
        assert all(set(f) == {"key", "mode"} for f in it["faults"])
    bad = [it for it in j["items"] if it["status"] != "ok"]
    assert all(it["reasons"] for it in bad)


def test_data_quality_deterministic(client):
    assert client.get("/insights/data-quality").json() == client.get("/insights/data-quality").json()


def _rows(values_by_cycle):
    return [TelemetryRow(tail="SIM-001", component_id="SIM-001/brakes", ts=T0 + timedelta(hours=i), cycle=i + 1, values=v)
            for i, v in enumerate(values_by_cycle)]


def _clean(n=30):
    rng = random.Random(3)
    return [{"pad_wear_mm": round(1.0 + 0.01 * i + rng.gauss(0, 0.01), 3), "disc_temp_c": round(300 + rng.gauss(0, 5), 1)} for i in range(n)]


def test_score_generic_clean_window_ok():
    s = health.score_generic("brakes", "SIM-001", "SIM-001/brakes", _rows(_clean()))
    assert s.data_quality.status == "ok" and s.data_quality.reasons == []


def test_score_generic_partial_stuck_missing_and_spike():
    stuck = _clean()
    for v in stuck[-6:]:
        v["pad_wear_mm"] = stuck[-6]["pad_wear_mm"]
    s = health.score_generic("brakes", "SIM-001", "SIM-001/brakes", _rows(stuck))
    assert s.data_quality.status == "degraded" and any("constant for last" in r for r in s.data_quality.reasons)

    missing = _clean()
    del missing[-2]["disc_temp_c"]
    s = health.score_generic("brakes", "SIM-001", "SIM-001/brakes", _rows(missing))
    assert s.data_quality.status == "degraded" and any("disc_temp_c missing" in r for r in s.data_quality.reasons)

    spike = _clean()
    spike[-1]["pad_wear_mm"] += 5.0
    s = health.score_generic("brakes", "SIM-001", "SIM-001/brakes", _rows(spike))
    assert s.data_quality.status == "degraded" and any("jumped" in r for r in s.data_quality.reasons)


def test_series_issues_clean_signal_has_none():
    rng = np.random.default_rng(5)
    for _ in range(50):
        x = 1.0 + 0.01 * np.arange(30) + rng.normal(0, 0.01, 30)
        assert health.series_issues("x", x) == []


# ---- reliability --------------------------------------------------------------------------------
def test_weibull_fit_recovers_beta_with_censoring():
    rng = random.Random(11)
    beta, eta = 2.5, 1000.0
    life = [rng.weibullvariate(eta, beta) for _ in range(500)]
    cens = [rng.uniform(0.3, 1.3) * eta for _ in life]
    times = [min(a, b) for a, b in zip(life, cens)]
    failed = [a <= b for a, b in zip(life, cens)]
    b_hat, e_hat = reliability.fit_weibull(times, failed)
    assert abs(b_hat - beta) / beta < 0.15
    assert abs(e_hat - eta) / eta < 0.15


def test_weibull_needs_failures():
    with pytest.raises(ValueError):
        reliability.fit_weibull([10.0, 20.0, 30.0], [False, False, True])


def test_reliability_endpoint(client):
    j = client.get("/insights/reliability").json()
    assert j["simulated"] is True and j["as_of"]
    assert {k["kind"] for k in j["kinds"]} == {"engine", *GENERIC}
    for k in j["kinds"]:
        assert set(k) == {"kind", "components", "samples", "beta", "eta_cycles", "mtbur_cycles", "b10_cycles", "basis"}
        assert k["beta"] > 0 and 0 < k["b10_cycles"] < k["eta_cycles"]
        assert k["basis"] == "fit to simulated life model + fleet counters"
    assert sum(k["components"] for k in j["kinds"]) == 240
    assert j == client.get("/insights/reliability").json()


# ---- disruptions --------------------------------------------------------------------------------
def test_weather_overlay_absorbed_and_not_absorbed():
    # tail rotation: leg0 ATL->DFW, leg1 DFW->ATL, leg2 ATL->DFW ; gaps are 120 min, buffer 85 min
    legs = [_leg(0, "T1", "ATL", "DFW"), _leg(1, "T1", "DFW", "ATL"), _leg(2, "T1", "ATL", "DFW")]
    small = {"id": "WX-1", "station": "ATL", "start": T0.isoformat().replace("+00:00", "Z"), "duration_h": 1.0, "severity": 0.5}   # 30 min < 85
    out = disruptions.propagate([small], legs)
    assert [d["leg_id"] for d in out] == ["L-T1-0"]            # absorbed by the buffer: next leg on time
    assert out[0]["delay_min"] == 30.0 and out[0]["cause"].startswith("weather")
    big = {**small, "id": "WX-2", "duration_h": 4.0, "severity": 1.0}                                                            # 240 min > 85
    out = disruptions.propagate([big], legs)
    assert [d["leg_id"] for d in out] == ["L-T1-0", "L-T1-1", "L-T1-2"]
    assert out[0]["delay_min"] == 240.0
    assert out[1]["delay_min"] == 240.0 - 85.0 and out[1]["cause"].startswith("rotation")
    assert out[2]["delay_min"] == 240.0 - 85.0 - 85.0       # each turn absorbs another 85 min
    assert out[1]["est_dep"] > out[1]["sched_dep"]


def test_weather_endpoints_roundtrip_and_delete(client):
    r = client.post("/insights/weather", json={"station": "ATL", "start": "2026-10-12T12:00:00Z", "duration_h": 6, "severity": 1})
    assert r.status_code == 200 and r.json()["simulated"] is True
    eid = r.json()["id"]
    j = client.get("/insights/disruptions").json()
    assert j["simulated"] is True and [e["id"] for e in j["events"]] == [eid]
    assert set(j["events"][0]) == {"id", "station", "start", "duration_h", "severity"}
    assert j["delayed_legs"] and j["by_tail"]
    assert set(j["delayed_legs"][0]) == {"leg_id", "tail", "dep", "arr", "sched_dep", "est_dep", "sched_arr", "est_arr", "delay_min", "cause"}
    assert sum(t["legs_affected"] for t in j["by_tail"]) == len(j["delayed_legs"])
    assert client.delete(f"/insights/weather/{eid}").status_code == 200
    assert client.delete(f"/insights/weather/{eid}").status_code == 404
    assert client.get("/insights/disruptions").json()["events"] == []


def test_weather_validation(client):
    ok = {"station": "ATL", "start": "2026-10-12T12:00:00Z", "duration_h": 2, "severity": 0.5}
    assert client.post("/insights/weather", json={**ok, "station": "XXX"}).status_code == 422
    assert client.post("/insights/weather", json={**ok, "severity": 1.5}).status_code == 422
    assert client.post("/insights/weather", json={**ok, "duration_h": 0}).status_code == 422


def test_reset_clears_disruptions(client):
    client.post("/insights/weather", json={"station": "DFW", "start": "2026-10-12T12:00:00Z", "duration_h": 3, "severity": 0.8})
    assert client.get("/insights/disruptions").json()["events"]
    client.post("/scenario/reset", json={"name": "golden"})
    assert client.get("/insights/disruptions").json()["events"] == []


# ---- leg profile --------------------------------------------------------------------------------
def test_leg_profile_and_404(client):
    j = client.get("/insights/legs/L-SIM-001-001/profile").json()
    assert j["simulated"] is True and j["leg_id"] == "L-SIM-001-001"
    assert abs(sum(m for _, m in j["phases"]) - 120.0) < 0.05
    assert {"oat_dep_c", "brake_energy_mj", "cruise_fl", "density_alt_arr_ft"} <= set(j)
    assert j == client.get("/insights/legs/L-SIM-001-001/profile").json()
    r = client.get("/insights/legs/NOPE/profile")
    assert r.status_code == 404 and r.json()["error"] == "not_found"
