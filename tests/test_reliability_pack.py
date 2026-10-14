"""Reliability pack: metric math on hand-built removal sets, Pareto, repeat defects, AMOS alerts, determinism and route shapes."""
import csv
import gzip
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import settings
from app.insights import reliability_pack as rp
from app.main import app
from app.sim.clock import START


def rec(kind="avionics", tail="T1", pos=None, action="replace", reason="failure", unsched=True, nff=False, hb=0.2, day=0.0):
    return rp.RemovalRec(kind, tail, pos or kind, action, reason, unsched, nff, hb, START + timedelta(days=day))


def ds(removals, fh=10000.0, fc=5000.0, kinds=("avionics",), days=360.0):
    return rp.Dataset(removals, rp.Exposure({k: 10 for k in kinds}, {k: fh for k in kinds}, {k: fc for k in kinds}, days))


def kind_row(out, kind):
    return next(k for k in out["metrics"]["per_kind"] if k["kind"] == kind)


def test_mtbur_mtbf_nff_premature_hand_answers():
    r = [rec(reason="failure", hb=0.1, day=1), rec(reason="failure", hb=0.2, day=2), rec(reason="failure", hb=0.3, day=3),
         rec(reason="nff", nff=True, hb=0.9, day=4), rec(reason="inspection_finding", hb=0.55, day=5),
         rec(reason="scheduled", unsched=False, hb=0.7, day=6), rec(reason="scheduled", unsched=False, action="repair", hb=0.1, day=7)]
    k = kind_row(rp.compute(ds(r)), "avionics")
    assert k["removals"] == 6 and k["unscheduled_removals"] == 5          # the repair is not a removal
    assert k["mtbur_fh"] == 2000.0 and k["mtbur_fc"] == 1000.0
    assert k["failures"] == 3 and k["mtbf_fh"] == pytest.approx(10000 / 3, abs=0.1)
    assert k["nff_removals"] == 1 and k["nff_rate"] == pytest.approx(0.2)
    assert k["premature_removal_rate"] == pytest.approx(3 / 6)            # health_before >= 0.5: 0.9, 0.55, 0.7
    assert k["removal_rate_per_1000fh"] == pytest.approx(0.5)
    assert k["rate_ci_low"] < 0.5 < k["rate_ci_high"]


def test_no_events_gives_none_not_zero_division():
    k = kind_row(rp.compute(ds([])), "avionics")
    assert k["mtbur_fh"] is None and k["mtbf_fh"] is None and k["nff_rate"] is None and k["removal_rate_per_1000fh"] == 0.0


def test_poisson_ci_known_values():
    lo, hi = rp.poisson_ci(0)
    assert lo == 0.0 and hi == pytest.approx(3.689, abs=0.002)
    lo, hi = rp.poisson_ci(10)
    assert lo == pytest.approx(4.795, abs=0.01) and hi == pytest.approx(18.390, abs=0.01)


def test_amos_threshold_and_status():
    base = [2, 4, 4, 4, 5, 5, 7, 9]
    mean, sigma, thr = rp.amos_threshold(base)
    assert mean == 5.0 and sigma == pytest.approx((32 / 7) ** 0.5) and thr == pytest.approx(5 + 3 * (32 / 7) ** 0.5)
    assert rp.amos_status(base + [12.0])["status"] == "alert"
    assert rp.amos_status(base + [10.0])["status"] == "warning"           # above +2 sigma (9.28), below +3 sigma (11.41)
    assert rp.amos_status(base + [9.0])["status"] == "normal"
    assert rp.amos_status([1, 1, 1, 1, 5])["threshold"] == 1.0 and rp.amos_status([1, 1, 1, 1, 5])["status"] == "alert"
    assert rp.amos_status([1, 2])["status"] == "insufficient_data"
    assert rp.amos_status([1, 1, 1, 1, 5], latest_count=1)["status"] == "normal"   # too few events to alert


def test_alert_through_compute_on_steady_then_burst():
    r = []
    for m in range(11):                                   # 2 unscheduled removals in each of 11 periods, then 12 in the last
        r += [rec(day=m * 30 + 3, tail=f"A{m}"), rec(day=m * 30 + 9, tail=f"B{m}")]
    r += [rec(day=330 + d, tail=f"C{d}") for d in range(12)]
    out = rp.compute(ds(r, days=360.0))
    al = next(a for a in out["alerts"] if a["kind"] == "avionics")
    assert al["status"] == "alert" and al["latest_count"] == 12 and al["window_periods"] == 11
    assert al["threshold_per_1000fh"] == pytest.approx(2 * 1000 / (10000 * 30 / 360), abs=1e-3)
    assert kind_row(out, "avionics")["alert_status"] == "alert"


def test_pareto_ordering_and_per_ata():
    r = [rec("tires", day=i) for i in range(5)] + [rec("brakes", day=i) for i in range(3)] + [rec("avionics", day=i) for i in range(6)] \
        + [rec("apu", day=1)]
    out = rp.compute(ds(r, kinds=("tires", "brakes", "avionics", "apu", "engine")))
    assert [p["ata_chapter"] for p in out["pareto"]] == ["32", "34", "49", "72"]
    assert [p["unscheduled_removals"] for p in out["pareto"]][:3] == [8, 6, 1]
    assert out["pareto"][0]["share"] == pytest.approx(8 / 15, abs=1e-4) and out["pareto"][-1]["cumulative_share"] == pytest.approx(1.0, abs=1e-4)
    ch32 = next(p for p in out["metrics"]["per_ata"] if p["ata_chapter"] == "32")
    assert ch32["kinds"] == ["brakes", "tires"] and ch32["flight_hours"] == 20000.0 and ch32["mtbur_fh"] == 2500.0
    assert [t["kind"] for t in out["top_contributors"]["kinds"]][:3] == ["avionics", "tires", "brakes"]


def test_repeat_defect_detection():
    hit = [rec(day=0), rec(day=30), rec(day=80), rec(day=400)]
    miss = [rec(tail="T2", day=0), rec(tail="T2", day=50), rec(tail="T2", day=100)]            # only 2 inside any 90 days
    other_pos = [rec(tail="T3", pos="engine_1", kind="engine", day=0), rec(tail="T3", pos="engine_2", kind="engine", day=10),
                 rec(tail="T3", pos="engine_1", kind="engine", day=20)]
    sched = [rec(tail="T4", day=d, unsched=False, reason="scheduled") for d in (0, 10, 20)]
    out = rp.repeat_defects(hit + miss + other_pos + sched)
    assert len(out) == 1
    d = out[0]
    assert d["tail"] == "T1" and d["position"] == "avionics" and d["count"] == 3 and d["ata_chapter"] == "34"


def test_run_dir_loader(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"cycles": 100, "tails": ["T1", "T2"], "run_id": "x", "spec": {"seed": 3}}))
    rows = [{"kind": "avionics", "unit": 0, "position": "avionics", "tail": "T1", "action": "replace", "removal_reason": "failure",
             "unscheduled": "True", "nff": "False", "health_before": 0.2, "tsn_h": 200.0, "csn": 100.0, "time": START.isoformat()},
            {"kind": "avionics", "unit": 1, "position": "avionics", "tail": "T2", "action": "replace", "removal_reason": "nff",
             "unscheduled": "True", "nff": "True", "health_before": 0.9, "tsn_h": 100.0, "csn": 50.0, "time": (START + timedelta(days=5)).isoformat()}]
    with gzip.open(tmp_path / "removals.csv.gz", "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    out = rp.compute(rp.from_run_dir(tmp_path))
    k = kind_row(out, "avionics")
    assert k["units"] == 2 and k["flight_cycles"] == 200.0 and k["flight_hours"] == 400.0   # 2 h per cycle inferred
    assert k["unscheduled_removals"] == 2 and k["nff_rate"] == 0.5 and k["mtbur_fh"] == 200.0
    assert kind_row(out, "engine")["units"] == 4


def test_backfill_deterministic_and_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr(rp, "_MEM", {})
    a = rp.compute(rp.run_backfill(11, years=0.15, fleet=3))
    b = rp.compute(rp.run_backfill(11, years=0.15, fleet=3))
    assert a == b and a["provenance"] == "SIMULATED"
    d1 = rp.cached_backfill(11, years=0.15, fleet=3)
    files = list((tmp_path / "cache").glob("reliability_*.json"))
    assert len(files) == 1 and rp.config_hash(11, 0.15, 3) in files[0].name
    rp._MEM.clear()
    d2 = rp.cached_backfill(11, years=0.15, fleet=3)           # from disk
    assert rp.compute(d1) == rp.compute(d2)
    assert rp.config_hash(11, 0.15, 3) != rp.config_hash(12, 0.15, 3)


def test_pack_route_shape_and_provenance():
    c = TestClient(app)
    j = c.get("/insights/reliability/pack").json()
    assert j["simulated"] is True and j["provenance"] == "SIMULATED" and j["as_of"]
    assert {"as_of", "simulated", "provenance", "disclaimer", "source", "taxonomy_label", "metrics", "pareto", "alerts", "repeat_defects",
            "top_contributors", "unscheduled_removals_total", "weibull"} <= set(j)
    assert set(j["metrics"]) == {"per_kind", "per_ata"} and len(j["metrics"]["per_kind"]) == 9
    k = j["metrics"]["per_kind"][0]
    assert {"kind", "ata_chapter", "ata_section", "units", "unit_definition", "flight_hours", "flight_cycles", "removals", "unscheduled_removals", "failures",
            "nff_removals", "mtbur_fh", "mtbur_fc", "mtbf_fh", "mtbf_fc", "nff_rate", "premature_removal_rate", "removal_rate_per_1000fh",
            "rate_ci_low", "rate_ci_high", "ci_level", "alert_status"} == set(k)
    assert {a["kind"] for a in j["alerts"]} == {x["kind"] for x in j["metrics"]["per_kind"]}
    assert j["source"]["basis"] == "backfill" and j["source"]["years"] == 1.0
    assert sum(p["unscheduled_removals"] for p in j["pareto"]) == j["unscheduled_removals_total"]
    shares = [p["unscheduled_removals"] for p in j["pareto"]]
    assert shares == sorted(shares, reverse=True)
    assert j == c.get("/insights/reliability/pack").json()
