"""SIM_ENGINE=v2: the service drives telemetry from the v2 World; default (v1) is untouched."""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta

import numpy as np
import pytest

from app import settings
from app.contracts.models import ScenarioEvent
from app.ml.sensors import SENSOR_NAMES, TELEMETRY_KEYS, kind_from_component_id
from app.sim.clock import START
from app.sim.service import SOURCE_ATTRS, Simulator
from app.sim.v2.models import engine as eng

CID = "SIM-017/engine_2"
# sha256 over (state json, source checkpoint, telemetry table) of a fixed v1 scenario, recorded on the commit before SIM_ENGINE existed
V1_GOLDEN_HASH = "c040209a70504531fe87a0461c66da8963c667558bbca75470bd350f5df78e90"


@pytest.fixture
def v2(monkeypatch):
    monkeypatch.setattr(settings, "SIM_ENGINE", "v2")


def _v1_hash() -> str:
    sim = Simulator(":memory:", seed=1234, stochastic=True)
    sim.step(24)
    sim.inject(ScenarioEvent(type="anomaly", at=sim.state.now, tail="SIM-017", component_id=CID))
    sim.inject(ScenarioEvent(type="accelerated_degradation", at=sim.state.now, tail="SIM-003", component_id="SIM-003/brakes", severity=0.5))
    sim.step(30)
    sim.source.restore("SIM-005/hydraulics")
    sim.step(6)
    h = hashlib.sha256()
    h.update(sim.state.model_dump_json().encode())
    h.update(json.dumps({a: getattr(sim.source, a) for a in SOURCE_ATTRS}, sort_keys=True).encode())
    for r in sim.connection.execute("SELECT tail,component_id,cycle,ts,values_json FROM telemetry ORDER BY tail,component_id,cycle"):
        h.update(repr(tuple(r)).encode())
    return h.hexdigest()


def test_flag_off_is_byte_identical_to_v1(monkeypatch):
    monkeypatch.setattr(settings, "SIM_ENGINE", "v1")
    assert _v1_hash() == V1_GOLDEN_HASH
    assert Simulator(":memory:").engine == "v1"


def test_unknown_flag_value_falls_back_to_v1(monkeypatch):
    monkeypatch.setattr(settings, "SIM_ENGINE", "bogus")
    assert Simulator(":memory:").engine == "v1"


def test_golden_fleet_facts_hold_under_v2(v2):
    sim = Simulator(":memory:")
    assert sim.engine == "v2" and sim.source.source == "simulated_v2_world"
    st = sim.state
    assert len(st.aircraft) == 24 and len(st.components) == 240 and len(st.legs) == 480
    legs = [lg for lg in st.legs if lg.tail == "SIM-017"]
    windows = [(a.sched_arr, b.sched_dep) for a, b in zip(legs, legs[1:]) if a.arr == "DFW" and b.sched_dep - a.sched_arr >= timedelta(hours=6)]
    assert windows
    dfw = next(s for s in st.stations if s.iata == "DFW")
    atl = next(s for s in st.stations if s.iata == "ATL")
    assert next(p for p in dfw.parts if p.part == "BORESCOPE-KIT").qty == 2
    stock = next(p for p in atl.parts if p.part == "BORESCOPE-KIT")
    assert atl.hangar_slots > 0 and stock.qty == 0 and stock.eta_hours == 18
    assert any(t.shift_start <= a and t.shift_end >= b and "engine" in t.skills for a, b in windows for t in dfw.technicians)


def test_step_ingests_the_v1_contract(v2):
    rows = []
    sim = Simulator(":memory:", ingest=rows.append)
    assert sim.step(24)["ingested"] == 24 * 4 * 10
    assert len(rows) == 960
    for r in rows:
        assert set(r.values) == set(TELEMETRY_KEYS[kind_from_component_id(r.component_id)]), r.component_id
        assert all(np.isfinite(v) for v in r.values.values())
    assert sim.step(0)["ingested"] == 0
    # row cycle continues from the fleet generator's counters, one per completed leg
    comp = next(c for c in sim.state.components if c.component_id == CID)
    mine = [r.cycle for r in rows if r.component_id == CID]
    assert mine == list(range(mine[0], mine[0] + 4)) and comp.cycles == mine[-1]


def test_initial_state_agrees_with_fleet(v2):
    sim = Simulator(":memory:")
    hi = sim.source.world.health_index()
    w = sim.source.world
    for c in sim.state.components:
        kind, unit = sim.source._index[c.component_id]
        if kind != "engine":  # v1 health = 1 - wear_profile start fraction
            assert abs(float(hi[kind][unit]) - c.health) < 0.06, c.component_id
    eng_hi = np.array([hi["engine"][i] for i in range(w.counts["engine"])])
    assert 0.4 < eng_hi.min() and eng_hi.max() <= 1.0


def test_deterministic_and_resumable(v2, tmp_path):
    def run(sim, hours):
        out = []
        for h in hours:
            sim.step(h)
        out = sim.connection.execute("SELECT tail,component_id,cycle,values_json FROM telemetry ORDER BY tail,component_id,cycle").fetchall()
        return out
    a = Simulator(":memory:")
    b = Simulator(":memory:")
    assert run(a, [24, 12]) == run(b, [24, 12])
    path = str(tmp_path / "s.db")
    c = Simulator(path)
    c.step(24)
    c.inject(ScenarioEvent(type="accelerated_degradation", at=c.state.now, tail="SIM-002", component_id="SIM-002/brakes", severity=1.0))
    resumed = Simulator(path)
    assert resumed.state == c.state
    assert resumed.source.world.state_hash() == c.source.world.state_hash()
    resumed.step(12)
    c.step(12)
    assert resumed.source.world.state_hash() == c.source.world.state_hash()
    assert resumed.state == c.state


def test_failed_step_rolls_back_the_world(v2, monkeypatch):
    sim = Simulator(":memory:")
    sim.step(12)
    before = sim.source.world.state_hash()
    with pytest.raises(ValueError):
        sim.inject(ScenarioEvent(type="anomaly", at=sim.state.now, tail="SIM-017", component_id="SIM-017/nope"))
    assert sim.source.world.state_hash() == before
    monkeypatch.setattr(sim, "_ingest", lambda row: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        sim.step(12)
    assert sim.source.world.state_hash() == before and sim.state.now == START + timedelta(hours=12)


def test_anomaly_makes_engine_look_late_life_and_scorer_rul_falls(v2):
    health = pytest.importorskip("app.ml.health")
    if not health.model_available():
        pytest.skip("RUL model artifact not available")
    rows = []
    sim = Simulator(":memory:", ingest=rows.append)
    sim.step(24)

    def rul():
        window = [r for r in rows if r.component_id == CID][-30:]
        s = health.score_component("engine", "SIM-017", CID, window)
        return s.rul_cycles.p50, s.anomaly_score

    before, anomaly_before = rul()
    result = sim.inject(ScenarioEvent(type="anomaly", at=sim.state.now, tail="SIM-017", component_id=CID))
    assert "SIMULATED" in result["effects"][0]
    after, anomaly_after = rul()
    sim.step(24)
    later, _ = rul()
    assert after < before - 30 and later < before - 30 and anomaly_after > anomaly_before
    truth = sim.source.world.ground_truth()
    assert any(f["kind"] == "engine" and f["mode"] == "hpt_blade_erosion" for f in truth)
    # other engine and other tails stay healthy
    other = [r for r in rows if r.component_id == "SIM-017/engine_1"][-30:]
    assert health.score_component("engine", "SIM-017", "SIM-017/engine_1", other).rul_cycles.p50 > after + 30


def test_accelerated_degradation_and_maintenance(v2):
    sim = Simulator(":memory:")
    w = sim.source.world
    sim.step(4)
    kind, unit = sim.source._index["SIM-004/brakes"]
    sim.inject(ScenarioEvent(type="accelerated_degradation", at=sim.state.now, tail="SIM-004", component_id="SIM-004/brakes", severity=1.0))
    assert w.fleet.state[kind]["stress_ext"][unit] == 5.0
    hi0 = float(w.health_index()[kind][unit])
    sim.step(48)
    hi1 = float(w.health_index()[kind][unit])
    assert hi1 < hi0
    sim.source.restore("SIM-004/brakes")
    assert w.fleet.state[kind]["stress_ext"][unit] == 1.0 and sim.source.positions["SIM-004/brakes"] == 0
    assert float(w.health_index()[kind][unit]) > hi1
    assert any(r.kind == "brakes" and r.tail == "SIM-004" for r in w.removals())


def test_reset_restores_the_initial_world(v2):
    sim = Simulator(":memory:")
    h0 = sim.source.world.state_hash()
    sim.step(24)
    sim.reset()
    assert sim.source.world.state_hash() == h0 and sim.clock.now() == START


def test_engine_time_scale_is_flights_not_cmapss_cycles():
    k = eng.PARAMS["flights_per_model_cycle"]
    f = eng._fit()
    n = 4000
    s = eng._sample(f, np.zeros(n, dtype=np.int64), np.random.default_rng(3))
    mean_flights = float((s["life_scale"] * k).mean())
    assert 15000 <= mean_flights <= 22000 and abs(mean_flights - 18000) < 1500


def test_time_scale_scales_wear_rate_not_the_sensor_map(monkeypatch):
    from app.sim.profile import profile_for_cycle
    from app.sim.v2 import Fleet, build_context

    def run(k, cycles):
        monkeypatch.setitem(eng.PARAMS, "flights_per_model_cycle", k)
        fleet = Fleet(5, {"engine": 6})
        tails = [f"E{i}" for i in range(6)]
        prev = None
        for c in range(1, cycles + 1):
            prev = build_context([profile_for_cycle(5, t, c) for t in tails], tails, 5, ["ATL"] * 6, prev=prev)
            fleet.step_cycle(prev)
        return fleet, prev

    slow, ctx = run(87.0, 50)
    fast, _ = run(1.0, 50)
    ratio = fast.state["engine"]["progress"].mean() / slow.state["engine"]["progress"].mean()
    assert 60 < ratio < 120
    # same progress -> same sensors: put the slow fleet at the fast fleet's progress and compare the noise-free parts
    slow.state["engine"]["progress"] = fast.state["engine"]["progress"].copy()
    a = slow.models["engine"].emit(slow.state["engine"], ctx, np.random.default_rng(9))
    b = fast.models["engine"].emit(fast.state["engine"], ctx, np.random.default_rng(9))
    for ch in SENSOR_NAMES:
        np.testing.assert_allclose(a[ch], b[ch], rtol=1e-9, atol=1e-9)
