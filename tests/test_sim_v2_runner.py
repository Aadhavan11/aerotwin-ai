"""Background backfill runner: determinism, resume, speed gate, HTTP happy path, role guard."""
from __future__ import annotations

import csv
import gzip
import io
import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.sim.v2 import runner as runner_mod
from app.sim.v2.runner import CYCLES_PER_YEAR, Runner
from app.sim.v2.runs_api import router

SMALL = dict(years=0.05, seed=11, fleet=4, checkpoint_every=40)


@pytest.fixture
def rn(tmp_path):
    return Runner(tmp_path / "runs.db", tmp_path / "runs")


def _files(rn: Runner, rid: str) -> dict[str, bytes]:
    out = Path(rn._row(rid)["out_dir"])
    tele = rn.export_path(rid)
    return {"telemetry": tele.read_bytes(), "removals": (out / "removals.csv.gz").read_bytes(),
            "truth": (out / "ground_truth.json").read_bytes(), "events": (out / "events.json").read_bytes()}


def test_deterministic_and_interrupted_runs_resume_bit_identical(rn):
    a = rn.create(start=False, **SMALL)
    st = rn.execute(a)
    assert st["status"] == "done" and st["cycles_done"] == st["total_cycles"] == round(0.05 * CYCLES_PER_YEAR)
    ref = _files(rn, a)

    b = rn.create(start=False, **SMALL)
    assert rn.execute(b)["state_hash"] == st["state_hash"] and _files(rn, b) == ref  # same seed -> same bytes

    c = rn.create(start=False, **SMALL)
    mid = rn.execute(c, stop_after_cycle=40)
    assert mid["status"] == "interrupted" and mid["cycles_done"] == 40
    with pytest.raises(ValueError):
        rn.export_path(c)
    done = rn.execute(c)
    assert done["status"] == "done" and done["state_hash"] == st["state_hash"] and _files(rn, c) == ref

    d = rn.create(start=False, **SMALL)
    crashed = rn.execute(d, crash_at_cycle=60)  # dies mid-segment: a partial part file is left behind
    assert crashed["status"] == "failed" and "simulated crash" in crashed["error"]
    assert list((Path(rn._row(d)["out_dir"]) / "parts").glob("*.tmp"))
    assert rn.status(d)["cycles_done"] == 40
    done = rn.execute(d)
    assert done["status"] == "done" and done["state_hash"] == st["state_hash"] and _files(rn, d) == ref


def test_outputs_are_simulated_and_truth_is_separate(rn):
    rid = rn.create(start=False, **SMALL)
    rn.execute(rid)
    rows = list(csv.DictReader(io.StringIO(gzip.decompress(rn.export_path(rid).read_bytes()).decode())))
    assert len(rows) == 4 * 10 * round(0.05 * CYCLES_PER_YEAR)
    assert {r["provenance"] for r in rows} == {"SIMULATED"}
    assert not any(k.startswith("fault_") or "truth" in k or k == "hi" for k in rows[0])
    out = Path(rn._row(rid)["out_dir"])
    truth = json.loads((out / "ground_truth.json").read_text())
    assert truth["provenance"] == "SIMULATED" and "faults" in truth
    assert json.loads((out / "manifest.json").read_text())["provenance"] == "SIMULATED"


def test_one_simulated_year_backfill_speed_gate(rn):
    rid = rn.create(years=1.0, seed=7, start=False)
    t0 = time.perf_counter()
    st = rn.execute(rid)
    elapsed = time.perf_counter() - t0
    assert st["status"] == "done" and st["rows"] == 24 * 10 * CYCLES_PER_YEAR
    assert elapsed < 60, f"1 simulated fleet-year took {elapsed:.1f}s"
    removals = list(csv.DictReader(io.StringIO(gzip.decompress((Path(rn._row(rid)["out_dir"]) / "removals.csv.gz").read_bytes()).decode())))
    assert removals and {r["provenance"] for r in removals} == {"SIMULATED"}
    # engines at real-flight time scale (~18k flights on wing), staggered start: year-1 shop visits (overhaul/replace) are a handful
    shop = [r for r in removals if r["kind"] == "engine" and r["action"] in ("overhaul", "replace")]
    assert 0 <= len(shop) <= 4


def _client(rn: Runner, monkeypatch) -> TestClient:
    monkeypatch.setattr(runner_mod, "_RUNNER", rn)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_api_happy_path(rn, monkeypatch):
    c = _client(rn, monkeypatch)
    r = c.post("/sim/runs", json={"years": 0.02, "seed": 5, "fleet": 3, "checkpoint_every": 20})
    assert r.status_code == 202
    body = r.json()
    rid = body["id"]
    assert body["provenance"] == "SIMULATED" and body["total_cycles"] == round(0.02 * CYCLES_PER_YEAR)
    deadline = time.time() + 60
    while time.time() < deadline:
        st = c.get(f"/sim/runs/{rid}").json()
        if st["status"] in ("done", "failed"):
            break
        time.sleep(0.1)
    assert st["status"] == "done", st
    assert st["progress"] == 1.0 and st["cycles_done"] == st["total_cycles"] and st["eta_seconds"] == 0.0
    ex = c.get(f"/sim/runs/{rid}/export")
    assert ex.status_code == 200 and ex.headers["x-data-source"] == "SIMULATED"
    assert ex.content[:2] == b"\x1f\x8b" or ex.content[:4] == b"PAR1"
    assert c.post(f"/sim/runs/{rid}/resume").json()["status"] == "done"
    assert any(x["id"] == rid for x in c.get("/sim/runs").json())


def test_api_errors_and_resume_of_interrupted(rn, monkeypatch):
    c = _client(rn, monkeypatch)
    assert c.get("/sim/runs/nope").status_code == 404
    assert c.post("/sim/runs/nope/resume").status_code == 404
    assert c.post("/sim/runs", json={"years": 0}).status_code == 422
    assert c.post("/sim/runs", json={"years": 1, "fleet": 99}).status_code == 422
    rid = rn.create(start=False, **SMALL)
    assert c.get(f"/sim/runs/{rid}/export").status_code == 409
    rn.execute(rid, stop_after_cycle=40)
    assert c.get(f"/sim/runs/{rid}").json()["status"] == "interrupted"
    assert c.post(f"/sim/runs/{rid}/resume").status_code == 202
    deadline = time.time() + 60
    while time.time() < deadline and c.get(f"/sim/runs/{rid}").json()["status"] != "done":
        time.sleep(0.1)
    assert c.get(f"/sim/runs/{rid}").json()["status"] == "done"


def test_sim_run_routes_are_ops_admin_only(tmp_path, monkeypatch):
    from app.auth import core
    from app.main import app
    monkeypatch.setattr(core, "DB_FILE", tmp_path / "auth.sqlite")
    monkeypatch.setattr(core, "_conn", None)
    monkeypatch.setattr(core, "_fails", {})
    monkeypatch.setattr(core, "_master_mtime", 0.0)
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.setenv("AUTH_DEMO_PASSWORD", "demo-pass-1")
    core.seed()
    c = TestClient(app)

    def login(user):
        assert c.post("/auth/login", json={"username": user, "password": "demo-pass-1"}).status_code == 200

    assert c.post("/sim/runs", json={"years": 1}).status_code == 401
    for user in ("crew", "dispatch", "maintenance"):
        login(user)
        assert c.post("/sim/runs", json={"years": 1}).status_code == 403
        assert c.get("/sim/runs/abc").status_code == 403
        assert c.post("/sim/runs/abc/resume").status_code == 403
        assert c.get("/sim/runs/abc/export").status_code == 403
    for user in ("operations", "administrator"):
        login(user)
        assert c.get("/sim/runs/abc").status_code != 403
        assert c.post("/sim/runs/abc/resume").status_code != 403
    monkeypatch.setattr(core, "_conn", None)


def test_manifest_route(rn, monkeypatch):
    c = _client(rn, monkeypatch)
    assert c.get("/sim/runs/nope/manifest").status_code == 404
    rid = rn.create(start=False, **SMALL)
    assert c.get(f"/sim/runs/{rid}/manifest").status_code == 404  # created, not yet run
    rn.execute(rid)
    r = c.get(f"/sim/runs/{rid}/manifest")
    assert r.status_code == 200 and r.headers["x-data-source"] == "SIMULATED"
    on_disk = json.loads((Path(rn._row(rid)["out_dir"]) / "manifest.json").read_text())
    assert r.json() == on_disk
    assert "reproducibility" in r.json()


def test_manifest_route_is_ops_admin_only(tmp_path, monkeypatch):
    from app.auth import core
    from app.main import app
    monkeypatch.setattr(core, "DB_FILE", tmp_path / "auth.sqlite")
    monkeypatch.setattr(core, "_conn", None)
    monkeypatch.setattr(core, "_fails", {})
    monkeypatch.setattr(core, "_master_mtime", 0.0)
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    monkeypatch.setenv("AUTH_DEMO_PASSWORD", "demo-pass-1")
    core.seed()
    c = TestClient(app)
    assert c.get("/sim/runs/abc/manifest").status_code == 401
    assert c.post("/auth/login", json={"username": "crew", "password": "demo-pass-1"}).status_code == 200
    assert c.get("/sim/runs/abc/manifest").status_code == 403
    assert c.post("/auth/login", json={"username": "operations", "password": "demo-pass-1"}).status_code == 200
    assert c.get("/sim/runs/abc/manifest").status_code == 404  # authorised; unknown run
    monkeypatch.setattr(core, "_conn", None)
