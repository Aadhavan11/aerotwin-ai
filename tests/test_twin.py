"""Acceptance tests for feature 3.2 backend (FEATURES-3.1-3.6.md)."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.contracts.models import Aircraft, FleetState, ScenarioEvent, TelemetryRow, TwinView
from app.ml import cmapss, health
from app.twin import state
from app.twin.router import router

pytestmark = pytest.mark.skipif(not health.model_available(), reason="run: python -m app.ml.train")

T0 = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
TAIL = "SIM-017"
ENG2 = "SIM-017/engine_2"


@pytest.fixture(autouse=True)
def clean():
    state.reset()
    state.set_now_provider(None)
    state.set_ops_provider(None)
    yield
    state.reset()


@pytest.fixture(scope="module")
def traj():
    return cmapss.trajectories()


@pytest.fixture(scope="module")
def healthy(traj):
    # Real data: an engine that is still far from failure when its recording ends.
    # Synthetic fallback (no NASA files): every engine runs to failure, so any long one will do for its first rows.
    pick = lambda rows: len(rows) >= 130 and (rows[-1]["true_rul"] > 100 or not cmapss.data_available())
    return next(rows for rows in traj.values() if pick(rows))


@pytest.fixture(scope="module")
def failing(traj):
    return next(rows for rows in traj.values() if rows[-1]["true_rul"] <= 25 and len(rows) >= 60)


def row(r, cid=ENG2, tail=TAIL, i=0):
    return TelemetryRow(tail=tail, component_id=cid, ts=T0 + timedelta(hours=1.8 * i), cycle=r["cycle"], values=r["values"])


def feed(rows, cid=ENG2, tail=TAIL, start=0, backfill=False):
    out = None
    for i, r in enumerate(rows):
        out = state.ingest_telemetry(row(r, cid, tail, start + i), backfill=backfill)
    return out


def register():
    state.register_aircraft(Aircraft(tail=TAIL, type="A320-class", home="ATL", location="ATL", status="ok", cycles=8123, hours=14210.5))


# ---- the status rule -------------------------------------------------------
@pytest.mark.parametrize("h,anom,p10,want", [
    (0.9, False, 100, "ok"),
    (0.9, True, 100, "alert"),
    (0.9, False, 29.9, "alert"),
    (0.9, False, 30.0, "watch"),
    (0.9, False, 59.9, "watch"),
    (0.9, False, 60.0, "ok"),
    (0.49, False, 100, "watch"),
    (0.5, False, 100, "ok"),
    (0.3, False, None, "watch"),
    (0.9, False, None, "ok"),
])
def test_status_from_matches_integration_doc(h, anom, p10, want):
    assert state.status_from(h, anom, p10) == want


# ---- telemetry -> health -> twin -------------------------------------------
def test_healthy_engine_stays_ok_and_fills_history(healthy):
    register()
    s = feed(healthy[:80])
    assert s.status == "ok"
    assert s.health > 0.6
    assert s.cycles == healthy[79]["cycle"]
    assert s.rul_cycles is not None and s.rul_cycles.p10 <= s.rul_cycles.p50 <= s.rul_cycles.p90
    twin = state.get_twin(TAIL)
    comp = twin.components[0]
    assert len(comp.history) == 80
    assert comp.sensors_last and "T50" in comp.sensors_last
    assert twin.timeline == []                     # nothing happened


def test_degradation_raises_alert_and_writes_timeline(healthy, failing):
    register()
    feed(healthy[:60])
    # The engine's data jumps to its end-of-life segment (what a scenario injection does).
    s = feed(failing[-30:], start=60)
    assert s.status == "alert"
    twin = state.get_twin(TAIL)
    assert twin.aircraft.status == "alert"         # aircraft status follows its worst component
    kinds = [e.kind for e in twin.timeline]
    assert "anomaly" in kinds
    assert "engine_2" in twin.timeline[0].summary and "alert" in twin.timeline[0].summary
    assert twin.timeline == sorted(twin.timeline, key=lambda e: e.ts, reverse=True)


def test_backfill_does_not_write_timeline(failing):
    register()
    feed(failing[-40:], backfill=True)
    assert state.get_twin(TAIL).timeline == []


def test_apply_event_adds_fault_injected_and_rescores(healthy, failing):
    register()
    feed(healthy[:60])
    feed(failing[-30:], start=60, backfill=True)   # data moved quietly, then the event arrives
    ev = ScenarioEvent(id="E-1", type="anomaly", at=T0 + timedelta(hours=120), tail=TAIL, component_id=ENG2, severity=0.9)
    out = state.apply_event(ev)
    assert [e.kind for e in out] == ["fault_injected"]
    assert out[0].ref == "E-1"
    twin = state.get_twin(TAIL)
    assert twin.components[0].status == "alert"
    assert any(e.kind == "fault_injected" for e in twin.timeline)


def test_station_event_without_tail_touches_no_aircraft():
    register()
    ev = ScenarioEvent(id="E-2", type="part_delay", at=T0, station="DFW", part="BORESCOPE-KIT", delay_hours=12)
    assert state.apply_event(ev) == []
    assert state.get_twin(TAIL).timeline == []


def test_bad_data_keeps_last_good_numbers(healthy):
    register()
    good = feed(healthy[:50])
    bad_row = TelemetryRow(tail=TAIL, component_id=ENG2, ts=T0 + timedelta(hours=200), cycle=healthy[50]["cycle"],
                           values={"T50": 1400.0})
    after = state.ingest_telemetry(bad_row)
    assert after.data_quality.status == "bad"
    assert after.health == good.health
    assert after.status == good.status
    assert after.rul_cycles == good.rul_cycles


# ---- predicted curve -------------------------------------------------------
def test_predicted_curve_shape(failing):
    register()
    feed(failing[-40:])
    c = state.get_twin(TAIL).components[0]
    p = c.predicted
    assert p and p[0].cycle == c.cycles
    assert p[0].health_p50 == pytest.approx(c.health, abs=1e-3)
    assert all(a.health_p50 >= b.health_p50 for a, b in zip(p, p[1:]))           # falling
    assert all(x.health_lo <= x.health_p50 <= x.health_hi for x in p)            # band order
    assert len(p) <= state.PREDICT_MAX_POINTS + 1
    # Reaches ~0 at RUL p50 when that is inside the plotted range.
    rul = c.rul_cycles
    if rul.p50 < len(p) - 1:
        assert p[int(round(rul.p50))].health_p50 == pytest.approx(0.0, abs=0.05)


def test_no_prediction_without_rul():
    register()
    assert state.get_twin(TAIL).components == []


# ---- maintenance -----------------------------------------------------------
def test_maintenance_resets_the_component(healthy, failing):
    register()
    feed(healthy[:60])
    feed(failing[-30:], start=60)
    done = T0 + timedelta(days=10)                 # after the last telemetry row
    s = state.apply_maintenance(TAIL, ENG2, done)
    assert s.health == 1.0 and s.cycles == 0 and s.hours == 0.0
    assert s.last_maintenance == done
    assert s.status == "ok"
    twin = state.get_twin(TAIL)
    assert twin.aircraft.status == "ok"
    assert twin.components[0].history == []
    assert twin.timeline[0].kind == "maintenance"
    # The window was cleared: new telemetry starts from a clean slate and is degraded until 30 rows.
    s2 = feed(healthy[:3], start=300)
    assert s2.data_quality.status == "degraded"
    assert s2.last_maintenance == done


def test_forced_aircraft_status():
    register()
    state.set_aircraft_status(TAIL, "in_maintenance")
    assert state.get_twin(TAIL).aircraft.status == "in_maintenance"
    state.set_aircraft_status(TAIL, None)
    assert state.get_twin(TAIL).aircraft.status == "ok"


# ---- views -----------------------------------------------------------------
def test_get_twin_roundtrips_as_twinview(healthy, failing):
    register()
    feed(healthy[:40])
    feed(failing[-30:], start=40)
    twin = state.get_twin(TAIL)
    again = TwinView.model_validate(json.loads(twin.model_dump_json()))
    assert again == twin
    assert state.get_twin("SIM-999") is None


def test_snapshot_has_what_the_scheduler_needs(failing):
    register()
    feed(failing[-40:])
    state.apply_maintenance(TAIL, "SIM-017/brakes", T0 + timedelta(days=1))
    state.set_ops_provider(lambda: ([], []))
    snap = state.snapshot()
    assert isinstance(snap, FleetState)
    by_id = {c.component_id: c for c in snap.components}
    eng = by_id[ENG2]
    assert eng.rul_cycles is not None and eng.rul_cycles.p10 > 0       # the deadline source
    assert by_id["SIM-017/brakes"].last_maintenance == T0 + timedelta(days=1)
    assert snap.now == max(r.ts for r in state._comps[ENG2].telemetry)  # newest row, no wall clock
    assert [a.tail for a in snap.aircraft] == [TAIL]


def test_empty_twin_snapshot_is_empty_not_a_stand_in():
    snap = state.snapshot()
    assert snap.aircraft == [] and snap.components == [] and state.is_empty()


def test_snapshot_uses_the_sim_clock_when_wired(failing):
    register()
    feed(failing[-5:])
    clock = datetime(2026, 10, 13, 6, 0, tzinfo=timezone.utc)
    state.set_now_provider(lambda: clock)
    assert state.snapshot().now == clock


def test_aircraft_summaries_name_the_worst_component(healthy, failing):
    register()
    feed(healthy[:60], cid="SIM-017/engine_1")
    feed(failing[-30:], cid=ENG2)
    s = state.aircraft_summaries()[0]
    assert s.worst_component == ENG2
    assert s.rul_p10_min is not None and s.status == "alert"


def test_history_is_capped(healthy):
    register()
    for i in range(state.HISTORY_MAX + 20):
        r = healthy[i % len(healthy)]
        state.ingest_telemetry(TelemetryRow(tail=TAIL, component_id=ENG2, ts=T0 + timedelta(hours=i), cycle=i + 1, values=r["values"]), backfill=True)
    assert len(state.get_twin(TAIL).components[0].history) == state.HISTORY_MAX


def test_generic_component_flows_through_the_twin():
    register()
    for i in range(1, 41):
        state.ingest_telemetry(TelemetryRow(tail=TAIL, component_id="SIM-017/brakes", ts=T0 + timedelta(hours=i), cycle=i,
                                            values={"pad_wear_mm": 2.0 + 0.09 * i, "disc_temp_c": 300.0}))
    c = state.get_twin(TAIL).components[0]
    assert c.kind == "brakes"
    assert 0.0 <= c.health <= 1.0 and c.rul_cycles is not None


# ---- HTTP ------------------------------------------------------------------
@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_http_aircraft_telemetry_and_metrics(client, failing):
    register()
    feed(failing[-35:])
    r = client.get(f"/aircraft/{TAIL}")
    assert r.status_code == 200
    body = r.json()
    assert body["synthetic"] is True and body["aircraft"]["tail"] == TAIL
    assert body["components"][0]["component_id"] == ENG2
    assert set(body["components"][0]) >= {"history", "predicted", "sensors_last", "rul_cycles", "data_quality"}
    t = client.get(f"/aircraft/{TAIL}/telemetry", params={"component": ENG2, "last": 10})
    assert t.status_code == 200 and len(t.json()) == 10
    assert client.get("/metrics/ml").json()["data_source"] in ("nasa-cmapss-fd001", "synthetic-cmapss-shaped")


def test_http_unknown_aircraft_is_404_with_contract_error_shape(client):
    r = client.get("/aircraft/SIM-999")
    assert r.status_code == 404
    assert set(r.json()) == {"error", "detail"}
