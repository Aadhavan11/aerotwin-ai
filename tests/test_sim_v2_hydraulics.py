"""Simulator lifetime checks compare a assumed prior (no citation), not validation against data."""

from dataclasses import fields, replace
from time import perf_counter

import numpy as np
import pytest

from app.sim.v2.core import REGISTRY, ComponentModel, Fleet
from app.sim.v2.drivers import OperatingContext
from app.sim.v2.models import hydraulics

KIND = "hydraulics"
PARAMS = hydraulics.PARAMS
CHANNELS = ('pump_pressure_kpa', 'fluid_temp_c', 'case_drain_flow_lpm', 'fluid_qty_pct', 'filter_dp_kpa')


def _ctx(n: int, cycle: int = 1, oat_c: float = 15.0, dust_index: float = 0.2) -> OperatingContext:
    values = {field.name: np.zeros(n) for field in fields(OperatingContext)}
    values.update(
        flight_hours=np.full(n, 2.0), oat_c=np.full(n, oat_c), dust_index=np.full(n, dust_index),
        apu_starts=np.ones(n), payload_frac=np.full(n, 0.7), thrust_derate=np.full(n, 0.1),
        calendar_days=np.full(n, cycle / 3.0), flight_cycles=np.full(n, float(cycle)),
        cycle_index=np.full(n, float(cycle)), utilisation=np.ones(n), wet_runway=np.zeros(n, dtype=bool),
    )
    return OperatingContext(**values)


def _run(fleet: Fleet, cycles: int, oat_c: float = 15.0) -> OperatingContext:
    ctx = _ctx(fleet.counts[KIND], oat_c=oat_c)
    for _ in range(cycles):
        ctx.calendar_days[:] = (fleet.cycle + 1) / 3.0
        fleet.step_cycle(ctx)
    return ctx


def _failure_times(accelerated: bool) -> np.ndarray:
    n = 320
    fleet = Fleet(271, {KIND: n})
    ctx = _ctx(n, oat_c=45.0 if accelerated else 15.0, dust_index=0.9 if accelerated else 0.2)
    if accelerated:
        fleet.state[KIND]["stress_ext"][:] = 2.0
    cutoff = 8000 if accelerated else 20000   # life scaled up (see model docstring); cutoffs keep ~0.1% censoring
    times = np.full(n, cutoff, dtype=float)
    seen = np.zeros(n, dtype=bool)
    for cycle in range(1, cutoff + 1):
        ctx.calendar_days[:] = cycle / 3.0
        fleet.step_cycle(ctx)
        failed = fleet.state[KIND]["failed"]
        times[failed & ~seen] = cycle
        seen |= failed
        if seen.all():
            break
    return times


def test_registration_and_channels() -> None:
    # Importing hydraulics explicitly above prevents load_all's silent ImportError from hiding a broken model.
    assert KIND in REGISTRY.kinds()
    model = REGISTRY.get(KIND)
    assert isinstance(model, ComponentModel)
    assert model.channels == CHANNELS
    state = model.init(12, np.random.default_rng(8))
    for value in state.values():
        assert isinstance(value, np.ndarray) and value.shape == (12,)
    for key in ("age_cycles", "age_hours", "age_virtual", "n_inspections", *model.latent_keys):
        assert np.array_equal(state[key], np.zeros(12))
    assert state["failed"].dtype == np.bool_
    assert state["hi"].dtype.kind == state["stress_ext"].dtype.kind == "f"
    assert (state["hi"] == 1.0).all() and (state["stress_ext"] == 1.0).all()


def test_failure_distribution_prior() -> None:
    """Reference Weibull is a assumed prior (no citation), not validation of real equipment."""
    times = np.sort(_failure_times(accelerated=True))
    median = PARAMS["failure_median_accel"]
    shape = PARAMS["failure_shape_accel"]
    cdf = 1.0 - np.exp(-np.log(2.0) * (times / median) ** shape)
    rank = np.arange(1, times.size + 1) / times.size
    statistic = max(np.max(rank - cdf), np.max(cdf - (rank - 1.0 / times.size)))
    assert statistic < 0.18, (statistic, np.median(times))
    assert 0.75 * median < np.median(times) < 1.3 * median
    assert (times < 8000).mean() > 0.95


def test_nominal_life_prior() -> None:
    """GENERIC life is a prior, not validation."""
    median = np.median(_failure_times(accelerated=False))
    assert 0.75 * PARAMS["life_cycles"] < median < 1.3 * PARAMS["life_cycles"]


def test_monotone_health_and_latents() -> None:
    fleet = Fleet(61, {KIND: 24})
    model = fleet.models[KIND]
    state = fleet.state[KIND]
    state["stress_ext"][:] = np.linspace(1.0, 40.0, 24)
    stress = state["stress_ext"].copy()
    rng = np.random.default_rng(93)
    for cycle in range(1, 501):
        old = {key: value.copy() for key, value in state.items()}
        ctx = replace(_ctx(24, cycle), oat_c=rng.uniform(-10, 45, 24), dust_index=rng.uniform(0, 1, 24),
                      flight_hours=rng.uniform(0.5, 6, 24), payload_frac=rng.uniform(0.3, 1, 24),
                      thrust_derate=rng.uniform(0, 0.3, 24), apu_starts=rng.integers(0, 4, 24))
        fleet.step_cycle(ctx)
        assert ((state["hi"] >= 0.0) & (state["hi"] <= 1.0)).all()
        assert (state["hi"] <= old["hi"]).all()
        for key in model.latent_keys:
            assert (state[key] >= old[key]).all(), key
        assert (state["failed"] | ~old["failed"]).all()
        assert np.array_equal(state["stress_ext"], stress)
        assert np.array_equal(state["age_cycles"], old["age_cycles"] + 1.0)
        assert np.array_equal(state["age_hours"], old["age_hours"] + ctx.flight_hours)
        assert np.array_equal(state["age_virtual"], old["age_virtual"] + 1.0)
    assert state["failed"].any()
    assert model.health_index(state) is state["hi"]


def test_temperature_acceleration() -> None:
    hot, cold = Fleet(31, {KIND: 64}), Fleet(31, {KIND: 64})
    _run(hot, 1350, oat_c=35.0)
    _run(cold, 1350, oat_c=-10.0)
    assert hot.health_index()[KIND].mean() < cold.health_index()[KIND].mean() - 0.05


def test_external_stress_acceleration() -> None:
    normal, stressed = Fleet(19, {KIND: 32}), Fleet(19, {KIND: 32})
    stressed.state[KIND]["stress_ext"][:] = 3.0
    _run(normal, 1350)
    _run(stressed, 1350)
    assert stressed.health_index()[KIND].mean() < normal.health_index()[KIND].mean() - 0.1


def test_determinism() -> None:
    a, b, c = (Fleet(seed, {KIND: 16}) for seed in (7, 7, 8))
    for fleet in (a, b, c):
        _run(fleet, 100)
    assert a.state_hash() == b.state_hash()
    assert a.state_hash() != c.state_hash()


def test_resume_bit_identity() -> None:
    a = Fleet(3, {KIND: 12})
    _run(a, 150)
    a.maintain(KIND, [0, 3], "replace")
    a.state[KIND]["stress_ext"][:] = 1.7
    snapshot = a.snapshot()
    ctx_a = _run(a, 100)
    hash_a = a.state_hash()
    emitted_a = a.emit_all(ctx_a)[KIND]
    assert a.state_hash() == hash_a  # Sensors must not change model state.
    b = Fleet(3, {KIND: 12})
    b.restore(snapshot)
    ctx_b = _run(b, 100)
    assert b.state_hash() == hash_a
    emitted_b = b.emit_all(ctx_b)[KIND]
    for key in CHANNELS:
        assert np.array_equal(emitted_a[key], emitted_b[key])


@pytest.mark.parametrize("q", [0.0, 0.35, 1.0])
def test_maintenance(q: float) -> None:
    fleet = Fleet(2, {KIND: 6})
    state = fleet.state[KIND]
    model = fleet.models[KIND]
    idx = np.array([0, 2], dtype=np.int64)
    untouched = np.array([1, 3, 4, 5], dtype=np.int64)
    state["stress_ext"][:] = 2.0
    _run(fleet, 80)
    before = {key: value.copy() for key, value in state.items()}
    fleet.maintain(KIND, idx, "inspect")
    for key in state:
        expected = before[key].copy()
        if key == "n_inspections":
            expected[idx] += 1.0
        assert np.array_equal(state[key], expected), key
    fleet.maintain(KIND, idx, "repair", q)
    for key in (*model.latent_keys, "age_virtual"):
        expected = 0.0 if KIND == "hydraulics" and key == "filter_dp_kpa" else q * before[key][idx]
        assert np.allclose(state[key][idx], expected), key
    assert (state["hi"][idx] >= before["hi"][idx]).all()
    assert np.array_equal(state["failed"][idx], state["hi"][idx] <= 0.0)
    for key in (*model.parameter_keys, "stress_ext", "age_cycles", "age_hours"):
        assert np.array_equal(state[key], before[key]), key
    for key in state:
        assert np.array_equal(state[key][untouched], before[key][untouched]), key
    _run(fleet, 3)
    before_reset = {key: value.copy() for key, value in state.items()}
    fleet.maintain(KIND, idx, "overhaul")
    assert (state["hi"][idx] == 1.0).all() and not state["failed"][idx].any()
    for key in (*model.latent_keys, "age_virtual"):
        assert (state[key][idx] == 0.0).all()
    for key in (*model.parameter_keys, "stress_ext", "age_cycles", "age_hours", "n_inspections"):
        assert np.array_equal(state[key], before_reset[key]), key
    for key in state:
        assert np.array_equal(state[key][untouched], before_reset[key][untouched]), key
    _run(fleet, 3)
    before_replace = {key: value.copy() for key, value in state.items()}
    state["failed"][idx] = True
    fleet.maintain(KIND, idx, "replace")
    for key in (*model.latent_keys, "age_cycles", "age_hours", "age_virtual", "n_inspections"):
        assert (state[key][idx] == 0.0).all(), key
    assert (state["hi"][idx] == 1.0).all() and not state["failed"][idx].any()
    for key in model.parameter_keys:
        assert not np.array_equal(state[key][idx], before_replace[key][idx]), key
    assert np.array_equal(state["stress_ext"], before_replace["stress_ext"])
    for key in state:
        assert np.array_equal(state[key][untouched], before_replace[key][untouched]), key
    snap = fleet.state_hash()
    fleet.maintain(KIND, np.array([], dtype=np.int64), "replace")
    assert fleet.state_hash() == snap
    with pytest.raises(ValueError):
        fleet.maintain(KIND, idx, "unknown")
    with pytest.raises(ValueError):
        model.maintain(state, idx, "unknown", q, np.random.default_rng(5))
    for invalid_q in (-0.1, 1.1):
        with pytest.raises(ValueError):
            fleet.maintain(KIND, idx, "repair", invalid_q)
    assert fleet.state_hash() == snap


def test_emit_shapes_and_finite() -> None:
    fleet = Fleet(13, {KIND: 20})
    ctx = _run(fleet, 30)
    emitted = fleet.emit_all(ctx)[KIND]
    assert set(emitted) == set(CHANNELS)
    repeated = fleet.emit_all(ctx)[KIND]
    for key, value in emitted.items():
        assert value.shape == (20,) and value.dtype.kind == "f"
        assert np.isfinite(value).all()
        assert np.array_equal(value, repeated[key])
    other_noise = fleet.models[KIND].emit(fleet.state[KIND], ctx, np.random.default_rng(98))
    assert any(not np.array_equal(emitted[key], other_noise[key]) for key in CHANNELS)


def test_performance() -> None:
    fleet = Fleet(11, {KIND: 240})
    ctx = _ctx(240)
    start = perf_counter()
    for cycle in range(1, 2001):
        ctx.calendar_days[:] = cycle / 3.0
        fleet.step_cycle(ctx)
        fleet.emit_all(ctx)
    assert perf_counter() - start < 5.0


def test_pressure_limit_and_maintenance_recovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(PARAMS, "pressure_noise", 0.0)
    fleet = Fleet(18, {KIND: 4})
    state = fleet.state[KIND]
    state["eta_loss"][:] = [0.0, 0.5, 1.0, 1.2]
    fleet.step_cycle(_ctx(4))
    pressure = fleet.emit_all(_ctx(4))[KIND]["pump_pressure_kpa"]
    span = PARAMS["pressure_healthy"] - PARAMS["pressure_limit"]
    assert np.allclose(pressure, PARAMS["pressure_limit"] + span * state["hi"])
    assert np.array_equal(state["failed"], [False, False, True, True])
    assert (pressure[2:] == PARAMS["pressure_limit"]).all()
    fleet.maintain(KIND, [2, 3], "repair", 0.25)
    assert (state["hi"][2:] > 0.0).all() and not state["failed"][2:].any()


def test_leak_contamination_and_precharge_channels(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("pressure_noise", "temp_noise", "drain_noise", "qty_noise", "filter_noise"):
        monkeypatch.setitem(PARAMS, key, 0.0)
    fleet = Fleet(14, {KIND: 8})
    ctx = _ctx(8)
    fresh = fleet.emit_all(ctx)[KIND]
    state = fleet.state[KIND]
    state["leak"][:] = 0.1
    state["precharge_loss"][:] = 0.1
    state["eta_loss"][:] = 0.1
    worn = fleet.emit_all(ctx)[KIND]
    assert (worn["pump_pressure_kpa"] < fresh["pump_pressure_kpa"]).all()
    assert (worn["case_drain_flow_lpm"] > fresh["case_drain_flow_lpm"]).all()
    assert (worn["fluid_qty_pct"] < fresh["fluid_qty_pct"]).all()
    assert (worn["fluid_temp_c"] > fresh["fluid_temp_c"]).all()
    clean, dirty = Fleet(22, {KIND: 8}), Fleet(22, {KIND: 8})
    dirty.state[KIND]["acid_number"][:] = 1.0
    clean.step_cycle(ctx)
    dirty.step_cycle(ctx)
    assert (dirty.state[KIND]["eta_loss"] > clean.state[KIND]["eta_loss"]).all()
    assert (dirty.state[KIND]["filter_dp_kpa"] > clean.state[KIND]["filter_dp_kpa"]).all()
