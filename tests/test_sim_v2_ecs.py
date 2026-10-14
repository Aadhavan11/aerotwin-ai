from __future__ import annotations

import time
from dataclasses import fields

import numpy as np
import pytest

from app.sim.v2.core import ComponentModel, Fleet, State, StreamRng, kinds
from app.sim.v2.drivers import OperatingContext
from app.sim.v2.models import ecs as model_module

KIND = "ecs"
V1_KEYS = ("pack_outlet_temp_c", "pack_flow_kgs")
LATENTS = ("fouling", "bearing_wear", "vibration_index", "valve_degradation")
COUNTER = "valve_count"
PARAMETER = "bearing_life_h"


def _ctx(n: int, **overrides: float | np.ndarray) -> OperatingContext:
    """Build every required per-unit array without profiles or dataset access."""
    cycle = overrides.get("cycle_index", 1)
    defaults = {
        "flight_hours": 2.0, "taxi_out_min": 15.0, "takeoff_min": 2.0,
        "climb_min": 15.0, "cruise_min": 80.0, "descent_min": 15.0,
        "landing_min": 3.0, "taxi_in_min": 15.0, "oat_c": 15.0,
        "field_elev_ft": 1000.0, "density_alt_ft": 2000.0,
        "wet_runway": False, "payload_frac": 0.7, "landing_weight_frac": 0.8,
        "brake_applications": 3, "brake_energy_mj": 20.0, "sink_rate_fps": 3.0,
        "taxi_distance_km": 10.0, "apu_starts": 1, "apu_hours": 0.3,
        "thrust_derate": 0.1, "humidity_pct": 50.0, "dust_index": 0.1,
        "cycle_index": cycle, "calendar_days": np.asarray(cycle) / 3.0,
        "flight_cycles": cycle, "total_flight_hours": np.asarray(cycle) * 2.0,
        "total_apu_starts": cycle, "total_apu_hours": np.asarray(cycle) * 0.3,
        "utilisation": 1.0,
    }
    defaults.update(overrides)
    integers = {"apu_starts", "total_apu_starts", "cycle_index", "flight_cycles", "brake_applications"}
    arrays = {}
    for field in fields(OperatingContext):
        dtype = bool if field.name == "wet_runway" else np.int64 if field.name in integers else float
        arrays[field.name] = np.broadcast_to(np.asarray(defaults[field.name], dtype=dtype), (n,)).copy()
    return OperatingContext(**arrays)


def _same_state(state: State, before: State) -> None:
    assert state.keys() == before.keys()
    for key in before:
        assert state[key].dtype == before[key].dtype
        assert state[key].tobytes() == before[key].tobytes(), key


def _ks_uniform(values: np.ndarray) -> float:
    """Two-sided one-sample KS statistic, hand-rolled with numpy."""
    ordered = np.sort(values)
    n = ordered.size
    return float(max(np.max(np.arange(1, n + 1) / n - ordered), np.max(ordered - np.arange(n) / n)))


def _to_failure(fleet: Fleet, ctx: OperatingContext, key: str, cap: int = 5000) -> tuple[np.ndarray, np.ndarray]:
    state = fleet.state[KIND]
    cycles = np.zeros(ctx.n, dtype=np.int64)
    exposure = np.zeros(ctx.n)
    for cycle in range(1, cap + 1):
        fleet.step_cycle(ctx)
        newly = state["failed"] & (cycles == 0)
        cycles[newly] = cycle
        exposure[newly] = state[key][newly]
        if np.all(cycles > 0):
            break
    assert np.all(cycles > 0), "failure cap reached"
    return cycles, exposure


def test_registration() -> None:
    assert KIND in kinds()
    model = Fleet(1, {KIND: 1}).models[KIND]
    assert isinstance(model, ComponentModel)
    assert model.kind == KIND
    assert set(V1_KEYS) <= set(model.channels)


def test_emit_keys_shapes_and_initial_state() -> None:
    fleet = Fleet(2, {KIND: 16})
    state = fleet.state[KIND]
    for key in ("age_cycles", "age_hours", "age_virtual", "hi", "stress_ext"):
        assert state[key].shape == (16,)
        assert state[key].dtype.kind == "f"
    assert state["failed"].dtype == bool
    assert np.array_equal(state["stress_ext"], np.ones(16))
    ctx = _ctx(16)
    fleet.step_cycle(ctx)
    output = fleet.emit_all(ctx)[KIND]
    assert set(output) == set(fleet.models[KIND].channels)
    for value in output.values():
        assert value.shape == (16,)
        assert np.isfinite(value).all()


def test_monotone_hi() -> None:
    fleet = Fleet(3, {KIND: 24})
    history = [fleet.health_index()[KIND].copy()]
    for cycle in range(1, 301):
        fleet.step_cycle(_ctx(24, cycle_index=cycle, oat_c=15.0 + cycle % 20))
        history.append(fleet.health_index()[KIND].copy())
    health = np.stack(history)
    assert np.all(np.diff(health, axis=0) <= 1e-12)
    assert np.all((health >= 0.0) & (health <= 1.0))
    assert np.array_equal(fleet.state[KIND]["age_cycles"], np.full(24, 300.0))
    assert np.array_equal(fleet.state[KIND]["age_hours"], np.full(24, 600.0))


def test_determinism() -> None:
    a, b = Fleet(7, {KIND: 16}), Fleet(7, {KIND: 16})
    for cycle in range(1, 201):
        ctx = _ctx(16, cycle_index=cycle, oat_c=10.0 + cycle % 30)
        a.step_cycle(ctx)
        b.step_cycle(ctx)
    assert a.state_hash() == b.state_hash()
    for channel in a.models[KIND].channels:
        assert np.array_equal(a.emit_all(ctx)[KIND][channel], b.emit_all(ctx)[KIND][channel])
    before = a.state_hash()
    a.emit_all(ctx)
    assert a.state_hash() == before


def test_resume_bit_identity() -> None:
    a = Fleet(8, {KIND: 12})
    for cycle in range(1, 151):
        a.step_cycle(_ctx(12, cycle_index=cycle, oat_c=cycle % 40))
    snapshot = a.snapshot()
    for cycle in range(151, 401):
        a.step_cycle(_ctx(12, cycle_index=cycle, oat_c=cycle % 40))
    b = Fleet(8, {KIND: 12})
    b.restore(snapshot)
    for cycle in range(151, 401):
        b.step_cycle(_ctx(12, cycle_index=cycle, oat_c=cycle % 40))
    assert a.state_hash() == b.state_hash()
    ctx = _ctx(12, cycle_index=400)
    for channel in a.models[KIND].channels:
        assert np.array_equal(a.emit_all(ctx)[KIND][channel], b.emit_all(ctx)[KIND][channel])


def test_maintenance() -> None:
    fleet = Fleet(9, {KIND: 8})
    state = fleet.state[KIND]
    ctx = _ctx(8)
    for _ in range(200):
        fleet.step_cycle(ctx)
    idx = np.array([0, 2, 5], dtype=np.int64)
    untouched = np.array([1, 3, 4, 6, 7], dtype=np.int64)
    before = {key: value.copy() for key, value in state.items()}
    fleet.maintain(KIND, idx, "inspect")
    _same_state(state, before)
    for action in ("inspect", "repair", "overhaul", "replace"):
        fleet.maintain(KIND, np.array([], dtype=np.int64), action)
        _same_state(state, before)
    with pytest.raises(ValueError):
        fleet.maintain(KIND, idx, "unknown")
    with pytest.raises(ValueError):
        fleet.models[KIND].maintain(state, np.array([], dtype=np.int64), "unknown", 0.5, StreamRng(9, "maint-test").at(0))
    fleet.maintain(KIND, idx, "repair", 0.4)
    assert np.allclose(state["age_virtual"][idx], before["age_virtual"][idx] * 0.4)
    # Kijima damage reduction improves health (ordinary steps cannot improve it).
    assert np.all(state["hi"][idx] >= before["hi"][idx])
    for key in LATENTS:
        assert np.allclose(state[key][idx], before[key][idx] * 0.4), key
    for key in state:
        assert np.array_equal(state[key][untouched], before[key][untouched]), key
    assert np.array_equal(state["age_cycles"], before["age_cycles"])
    assert np.array_equal(state["age_hours"], before["age_hours"])
    fleet.maintain(KIND, idx, "overhaul", 0.8)
    for key in (*LATENTS, "age_virtual", COUNTER):
        assert np.all(state[key][idx] == 0.0), key
    assert np.all(state["hi"][idx] == 1.0)
    assert not state["failed"][idx].any()
    assert np.array_equal(state["age_cycles"], before["age_cycles"])
    assert np.array_equal(state["age_hours"], before["age_hours"])
    for key in state:
        assert np.array_equal(state[key][untouched], before[key][untouched]), key
    params = state[PARAMETER][idx].copy()
    fleet.maintain(KIND, idx, "replace")
    for key in (*LATENTS, "age_cycles", "age_hours", "age_virtual", COUNTER):
        assert np.all(state[key][idx] == 0.0), key
    assert np.all(state["hi"][idx] == 1.0)
    assert not state["failed"][idx].any()
    assert np.any(state[PARAMETER][idx] != params)
    for key in state:
        assert np.array_equal(state[key][untouched], before[key][untouched]), key


@pytest.mark.parametrize("q, factor", [(-1.0, 0.0), (2.0, 1.0)])
def test_repair_clamps_quality(q: float, factor: float) -> None:
    fleet = Fleet(10, {KIND: 2})
    fleet.step_cycle(_ctx(2))
    state = fleet.state[KIND]
    before = state["age_virtual"].copy()
    fleet.maintain(KIND, np.array([0]), "repair", q)
    assert state["age_virtual"][0] == before[0] * factor
    assert state["age_virtual"][1] == before[1]


def test_timing() -> None:
    fleet = Fleet(11, {KIND: 240})
    ctx = _ctx(240)
    start = time.perf_counter()
    for _ in range(2000):
        fleet.step_cycle(ctx)
        fleet.emit_all(ctx)
    assert time.perf_counter() - start < 5.0


def test_driver_sensitivity_and_exact_fouling() -> None:
    clean, dusty = Fleet(12, {KIND: 32}), Fleet(12, {KIND: 32})
    clean_ctx, dusty_ctx = _ctx(32, dust_index=0.05), _ctx(32, dust_index=0.8)
    for _ in range(400):
        model_module.update_latent(clean.state[KIND], clean_ctx)
        model_module.update_latent(dusty.state[KIND], dusty_ctx)
    assert np.all(dusty.state[KIND]["fouling"] > 2.0 * clean.state[KIND]["fouling"])
    clean_rise = model_module.outlet_temperature(clean.state[KIND]) - model_module.PARAMS["outlet_healthy_c"]
    dusty_rise = model_module.outlet_temperature(dusty.state[KIND]) - model_module.PARAMS["outlet_healthy_c"]
    assert np.all(dusty_rise > 2.0 * clean_rise)
    p = model_module.PARAMS
    expected = p["fouling_inf"] * clean.state[KIND]["fouling_factor"] * (p["dust_baseline"] + 0.05) * (-np.expm1(-800.0 / p["fouling_tau_h"]))
    assert np.allclose(clean.state[KIND]["fouling"], expected, rtol=1e-12)
    prior = dusty.state[KIND]["fouling"].copy()
    model_module.update_latent(dusty.state[KIND], clean_ctx)
    assert np.all(dusty.state[KIND]["fouling"] >= prior)


def test_life_distribution() -> None:
    """Bearing-dominated first-passage PIT = 1-exp(-(hours/scale)**beta).

    At a clean, cool station the exchanger asymptote stays below its limit;
    accelerated flight hours reach sampled Weibull bearing lives before valve
    shocks reach a hard limit. Capture actual exposure at each failure cycle,
    with <=100 h discretisation. Assert bearing dominance rather than assume it.
    KS < .12 is a prior, tolerance band, not a calibration against data.
    """
    fleet = Fleet(14, {KIND: 240})
    ctx = _ctx(240, flight_hours=100.0, dust_index=0.0, oat_c=15.0)
    cycles, hours = _to_failure(fleet, ctx, "bearing_hours", cap=500)
    state = fleet.state[KIND]
    assert np.all(hours >= state["bearing_life_h"])
    assert np.all(hours < state["bearing_life_h"] + 100.0)
    p = model_module.PARAMS
    pit = -np.expm1(-(hours / p["bearing_scale_h"]) ** p["bearing_beta"])
    assert cycles.size >= 200
    assert _ks_uniform(pit) < 0.12


def test_stress_and_bearing_flow_temperature_channels() -> None:
    normal, stressed = Fleet(13, {KIND: 8}), Fleet(13, {KIND: 8})
    stressed.state[KIND]["stress_ext"][:] = 2.0
    for _ in range(100):
        model_module.update_latent(normal.state[KIND], _ctx(8))
        model_module.update_latent(stressed.state[KIND], _ctx(8))
    assert np.all(stressed.state[KIND]["fouling"] > 1.8 * normal.state[KIND]["fouling"])
    assert np.allclose(stressed.state[KIND]["bearing_hours"], 2.0 * normal.state[KIND]["bearing_hours"])
    assert np.allclose(stressed.state[KIND]["bearing_wear"], 4.0 * normal.state[KIND]["bearing_wear"])
    state = normal.state[KIND]
    ctx = _ctx(8)
    before = normal.emit_all(ctx)[KIND]
    state["bearing_hours"][:] = state["bearing_life_h"] * 0.8
    state["valve_degradation"][:] = 0.4
    model_module.update_health(state)
    after = normal.emit_all(ctx)[KIND]
    assert np.all(after["acm_speed_rpm"] < before["acm_speed_rpm"] - 1000.0)
    assert np.all(after["pack_flow_kgs"] < before["pack_flow_kgs"] - 0.2)
    assert np.all(after["pack_outlet_temp_c"] > before["pack_outlet_temp_c"] + 3.0)
    assert np.all(after["ram_door_pos_pct"] < before["ram_door_pos_pct"] - 10.0)


def test_temperature_failure_and_maintenance() -> None:
    fleet = Fleet(15, {KIND: 3})
    state = fleet.state[KIND]
    state["fouling"][:] = 0.9
    state["valve_degradation"][:] = 0.8
    state["valve_count"][:] = 10
    model_module.update_health(state)
    assert np.all(state["hi"] > 0.0)
    assert np.all(model_module.outlet_temperature(state) >= model_module.PARAMS["outlet_limit_c"])
    assert state["failed"].all()
    # Cooling the station cannot clear a previously latched temperature failure.
    fleet.step_cycle(_ctx(3, oat_c=-10.0))
    assert state["failed"].all()
    fleet.maintain(KIND, np.array([0]), "repair", 1.0)
    assert state["failed"][0]
    fleet.maintain(KIND, np.array([0]), "repair", 0.5)
    assert not state["failed"][0]
    assert state["failed"][1:].all()
    fleet.maintain(KIND, np.array([1]), "overhaul")
    assert state["valve_count"][1] == 0
    assert state["valve_degradation"][1] == 0.0
