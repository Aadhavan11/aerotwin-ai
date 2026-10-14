import random
from dataclasses import dataclass

import pytest

from app.ml.sensors import TELEMETRY_KEYS
from app.sim.degradation import AircraftCtx, add_fault, new_state, restore, step


@dataclass
class P:
    oat_dep_c: float = 15.0
    oat_arr_c: float = 15.0
    density_alt_arr_ft: float = 1000.0
    payload_frac: float = 0.7
    takeoff_derate: float = 0.9
    brake_energy_mj: float = 30.0
    sink_rate_fps: float = 5.0
    wet_runway: bool = False
    landing_weight_frac: float = 0.8


def run(kind, n, p=None, seed=1, state=None, ctx=None):
    p = p or P()
    rng = random.Random(seed)
    s = state or new_state(kind, seed, f"X/{kind}")
    out = []
    for _ in range(n):
        s, v = step(kind, s, p, rng, ctx or AircraftCtx())
        out.append(v)
    return s, out


@pytest.mark.parametrize("kind", list(TELEMETRY_KEYS))
def test_keys(kind):
    _, out = run(kind, 3)
    assert set(out[0]) == set(TELEMETRY_KEYS[kind])


def test_brake_wear_rises_with_energy():
    _, lo = run("brakes", 50, P(brake_energy_mj=15))
    _, hi = run("brakes", 50, P(brake_energy_mj=45))
    assert hi[-1]["pad_wear_mm"] > lo[-1]["pad_wear_mm"] > lo[0]["pad_wear_mm"]


def test_engine_wear_convex_late():
    s0 = new_state("engine", 1, "X/engine_1")
    _, out = run("engine", 1400, state=s0)
    t = [v["T50"] for v in out]
    early = t[400] - t[200]
    late = t[1300] - t[1100]
    assert late > early * 1.5
    assert t[-1] > t[0]


def test_restore_returns_to_new():
    s, _ = run("engine", 300)
    add_fault(s, "hpt_damage", 300, 1.0, 5)
    r = restore(s)
    assert r == new_state("engine", 1, "X/engine")  or r["age"] == 0.0
    assert r["modes"] == [] and r["cycle"] == 0 and r["age"] == 0.0


def test_hpt_damage_shift():
    s = new_state("engine", 3, "X/engine_1")
    s, base = run("engine", 20, state=s, seed=3)
    add_fault(s, "hpt_damage", s["cycle"], 1.0, 6)
    _, after = run("engine", 8, state=s, seed=4)
    assert after[-1]["T50"] - base[-1]["T50"] >= 120


def test_determinism():
    assert run("hydraulics", 30, seed=9)[1] == run("hydraulics", 30, seed=9)[1]


def test_low_hydraulic_pressure_stresses_gear():
    _, ok = run("landing_gear", 5)
    _, bad = run("landing_gear", 5, ctx=AircraftCtx(hyd_pressure_frac=0.6))
    assert bad[-1]["shock_load_g"] > ok[-1]["shock_load_g"]


@pytest.mark.parametrize("kind", ["apu", "tires", "ecs", "electrical"])
def test_new_models_preserve_input_and_monotone_wear(kind):
    state = new_state(kind, 1, f"X/{kind}", start_fraction=0.3)
    rng = random.Random(1)
    for _ in range(30):
        before = dict(state)
        after, _ = step(kind, state, P(), rng)
        assert state == before
        assert after["age"] > state["age"]
        assert after["wear"] >= state["wear"]
        state = after


@pytest.mark.parametrize("kind", ["apu", "ecs", "electrical"])
def test_new_models_wear_faster_in_hot_oat(kind):
    cool, _ = run(kind, 20, P(oat_dep_c=15))
    hot, _ = run(kind, 20, P(oat_dep_c=40))
    assert hot["age"] > cool["age"]
    assert hot["wear"] > cool["wear"]


def test_apu_age_scales_with_runtime():
    short, long = P(), P()
    short.phases = (("taxi_out", 6.0), ("cruise", 90.0), ("taxi_in", 3.0))
    long.phases = (("taxi_out", 12.0), ("cruise", 90.0), ("taxi_in", 6.0))
    a, av = run("apu", 20, short)
    b, bv = run("apu", 20, long)
    assert b["age"] == pytest.approx(2 * a["age"])
    assert bv[-1]["apu_egt_margin_c"] < av[-1]["apu_egt_margin_c"]


@pytest.mark.parametrize("profile", [P(brake_energy_mj=60), P(sink_rate_fps=10)])
def test_tire_wear_increases_with_energy_and_sink_rate(profile):
    baseline, base = run("tires", 30)
    stressed, values = run("tires", 30, profile)
    assert stressed["wear"] > baseline["wear"]
    assert values[-1]["tread_depth_mm"] < base[-1]["tread_depth_mm"]


@pytest.mark.parametrize("ctx", [AircraftCtx(engine_wear=0.8), AircraftCtx(hpt_level=1.0)])
def test_ecs_wear_increases_with_engine_bleed_load(ctx):
    baseline, base = run("ecs", 30)
    stressed, values = run("ecs", 30, ctx=ctx)
    assert stressed["wear"] > baseline["wear"]
    assert values[-1]["pack_outlet_temp_c"] > base[-1]["pack_outlet_temp_c"]


@pytest.mark.parametrize("kind,mode,key,direction", [
    ("apu", "starter_fault", "apu_rpm_pct", -1),
    ("tires", "pressure_leak", "tire_pressure_psi", -1),
    ("ecs", "pack_valve_fault", "pack_outlet_temp_c", 1),
    ("ecs", "pack_valve_fault", "pack_flow_kgs", -1),
    ("electrical", "battery_fault", "battery_capacity_pct", -1),
    ("electrical", "battery_fault", "bus_voltage_v", -1),
])
def test_new_fault_modes_ramp_telemetry(kind, mode, key, direction):
    state = new_state(kind, 1, f"X/{kind}", start_fraction=0.2)
    add_fault(state, mode, 0, severity=1.0, ramp_cycles=5)
    _, faulted = run(kind, 5, state=state)
    _, normal = run(kind, 5, state=new_state(kind, 1, f"X/{kind}", start_fraction=0.2))
    shifts = [direction * (fault[key] - base[key]) for fault, base in zip(faulted, normal)]
    assert shifts[0] > 0
    assert shifts[-1] == pytest.approx(5 * shifts[0], abs=0.01)


def test_electrical_voltage_tracks_capacity_and_aircraft_bus():
    _, fresh = run("electrical", 20)
    worn = new_state("electrical", 1, "X/electrical", start_fraction=0.6)
    _, aged = run("electrical", 20, state=worn)
    _, low_bus = run("electrical", 20, ctx=AircraftCtx(bus_voltage_frac=0.8))
    assert aged[-1]["battery_capacity_pct"] < fresh[-1]["battery_capacity_pct"]
    assert aged[-1]["bus_voltage_v"] < fresh[-1]["bus_voltage_v"]
    assert low_bus[-1]["bus_voltage_v"] < fresh[-1]["bus_voltage_v"]
