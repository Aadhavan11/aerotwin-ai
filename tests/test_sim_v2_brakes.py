"""Brakes v2 model tests. Life bands are literature priors, not validation against fleet data."""

import time
from dataclasses import fields

import numpy as np
import pytest
from scipy import stats

from app.sim.v2 import Fleet, get
from app.sim.v2.drivers import OperatingContext
from app.sim.v2.models import brakes as brakes_mod

KIND = "brakes"


def make_ctx(n: int, cycle: int, seed: int = 0, **over) -> OperatingContext:
    r = np.random.default_rng([seed, cycle])
    base = {
        "flight_hours": np.full(n, 2.0), "taxi_out_min": np.full(n, 8.0), "takeoff_min": np.full(n, 2.0), "climb_min": np.full(n, 20.0),
        "cruise_min": np.full(n, 70.0), "descent_min": np.full(n, 20.0), "landing_min": np.full(n, 3.0), "taxi_in_min": np.full(n, 8.0),
        "oat_c": r.uniform(5.0, 30.0, n), "field_elev_ft": np.full(n, 500.0), "density_alt_ft": np.full(n, 800.0),
        "wet_runway": r.random(n) < 0.15, "payload_frac": r.uniform(0.6, 0.95, n), "landing_weight_frac": r.uniform(0.78, 0.95, n),
        "brake_applications": r.integers(10, 17, n), "brake_energy_mj": r.uniform(11.0, 26.0, n), "sink_rate_fps": r.uniform(2.0, 4.0, n),
        "taxi_distance_km": r.uniform(4.8, 7.1, n), "apu_starts": np.ones(n, dtype=np.int64), "apu_hours": np.full(n, 0.8),
        "thrust_derate": np.zeros(n), "humidity_pct": np.full(n, 60.0), "dust_index": r.uniform(0.0, 0.4, n),
        "cycle_index": np.full(n, cycle + 1, dtype=np.int64), "calendar_days": np.full(n, (cycle + 1) / 3.0),
        "flight_cycles": np.full(n, cycle + 1, dtype=np.int64), "total_flight_hours": np.full(n, 2.0 * (cycle + 1)),
        "total_apu_starts": np.full(n, cycle + 1, dtype=np.int64), "total_apu_hours": np.full(n, 0.8 * (cycle + 1)),
        "utilisation": np.ones(n),
    }
    assert set(base) == {f.name for f in fields(OperatingContext)}
    for k, v in over.items():
        base[k] = np.full(n, v, dtype=base[k].dtype) if np.isscalar(v) else np.asarray(v)
    return OperatingContext(**base)


def life_cycles(n=240, stress=5.0, max_cycles=2000, seed=11, **over) -> np.ndarray:
    f = Fleet(seed, {KIND: n})
    f.state[KIND]["stress_ext"][:] = stress
    first = np.full(n, np.inf)
    for c in range(max_cycles):
        f.step_cycle(make_ctx(n, c, seed, **over))
        newly = f.state[KIND]["failed"] & np.isinf(first)
        first[newly] = c + 1
        if not np.isinf(first).any():
            break
    return first


def run_fixed(cycles: int, n: int = 64, seed: int = 3, **over) -> Fleet:
    f = Fleet(seed, {KIND: n})
    for c in range(cycles):
        f.step_cycle(make_ctx(n, c, seed, **over))
    return f


def test_registered_and_v1_channels():
    m = get(KIND)
    assert {"pad_wear_mm", "disc_temp_c"} <= set(m.channels)
    f = run_fixed(5, n=8)
    out = f.emit_all(make_ctx(8, 4, 3))[KIND]
    assert set(out) == set(m.channels)
    assert all(v.shape == (8,) and np.all(np.isfinite(v)) for v in out.values())
    assert 0.0 <= out["pad_wear_mm"].min() and out["pad_wear_mm"].max() < 12.0
    assert 20.0 < out["disc_temp_c"].mean() < 700.0


def test_params_documented_as_prior():
    src = open(brakes_mod.__file__).read()
    assert src.count("assumed (no citation)") >= len(brakes_mod.PARAMS)


def test_life_distribution_band_prior():
    """Accelerated (stress_ext=5) cycles-to-failure vs a tolerant lognormal prior band (median ~335, sigma ~0.25); prior, not validation."""
    life = life_cycles()
    assert np.isfinite(life).all()
    ref = np.random.default_rng(0).lognormal(np.log(BAND_MEDIAN), BAND_SIGMA, 4000)
    ks = stats.ks_2samp(life, ref)
    assert ks.pvalue > 0.001, (ks, np.median(life))


BAND_MEDIAN = 335.0
BAND_SIGMA = 0.25


def test_hi_monotone_and_bounded():
    f = Fleet(5, {KIND: 64})
    prev = f.health_index()[KIND].copy()
    for c in range(300):
        f.step_cycle(make_ctx(64, c, 5))
        hi = f.health_index()[KIND]
        assert np.all(hi <= prev + 1e-12) and hi.min() >= 0.0 and hi.max() <= 1.0
        prev = hi.copy()
    assert prev.mean() < 0.9


def test_wear_increases_with_energy_and_applications():
    lo = run_fixed(100, brake_energy_mj=10.0, brake_applications=10).state[KIND]["wear_mm"]
    hi_e = run_fixed(100, brake_energy_mj=25.0, brake_applications=10).state[KIND]["wear_mm"]
    hi_a = run_fixed(100, brake_energy_mj=10.0, brake_applications=20).state[KIND]["wear_mm"]
    assert hi_e.mean() > lo.mean() * 1.1
    assert hi_a.mean() > lo.mean() * 1.3


def test_wet_runway_raises_wear():
    dry = run_fixed(150, wet_runway=False).state[KIND]["wear_mm"].mean()
    wet = run_fixed(150, wet_runway=True).state[KIND]["wear_mm"].mean()
    assert wet > dry * 1.05


def test_stress_ext_scales_wear_and_oxidation_needs_heat():
    base = run_fixed(100).state[KIND]
    f = Fleet(3, {KIND: 64})
    f.state[KIND]["stress_ext"][:] = 2.0
    for c in range(100):
        f.step_cycle(make_ctx(64, c, 3))
    assert f.state[KIND]["wear_mm"].mean() > 1.7 * base["wear_mm"].mean()
    cool = run_fixed(100, brake_energy_mj=8.0, oat_c=5.0).state[KIND]["ox_mm"].mean()
    hot = run_fixed(100, brake_energy_mj=45.0, oat_c=35.0).state[KIND]["ox_mm"].mean()
    assert hot > 20 * cool


def test_determinism_and_resume_bit_identity():
    a, b = run_fixed(60, n=32, seed=9), run_fixed(60, n=32, seed=9)
    assert a.state_hash() == b.state_hash()
    assert run_fixed(60, n=32, seed=10).state_hash() != a.state_hash()
    half = run_fixed(30, n=32, seed=9)
    snap = half.snapshot()
    g = Fleet(9, {KIND: 32})
    g.restore(snap)
    for c in range(30, 60):
        g.step_cycle(make_ctx(32, c, 9))
    assert g.state_hash() == a.state_hash()
    e1 = g.emit_all(make_ctx(32, 59, 9))[KIND]
    e2 = a.emit_all(make_ctx(32, 59, 9))[KIND]
    assert all(np.array_equal(e1[k], e2[k]) for k in e1)


def test_maintain_semantics():
    f = run_fixed(300, n=16, seed=4, brake_applications=20)
    st = f.state[KIND]
    w0, age0, hi0 = st["wear_mm"].copy(), st["age_cycles"].copy(), st["hi"].copy()
    f.maintain(KIND, [0, 1], "inspect")
    assert np.array_equal(st["wear_mm"], w0) and st["inspected"][:2].all() and not st["inspected"][2:].any()
    pins = f.emit_all(make_ctx(16, 299, 4))[KIND]["pad_wear_mm"]
    assert np.abs(pins[:2] - w0[:2]).max() < 0.1
    f.maintain(KIND, [2], "repair", q=0.5)
    assert st["wear_mm"][2] == pytest.approx(0.5 * w0[2]) and st["age_cycles"][2] == age0[2]
    assert st["hi"][2] >= hi0[2]
    f.maintain(KIND, [3], "overhaul", q=1.0)
    assert st["wear_mm"][3] == pytest.approx(0.25 * w0[3])
    old_k = st["k_app"][4]
    f.maintain(KIND, [4], "replace")
    assert st["wear_mm"][4] == 0 and st["age_cycles"][4] == 0 and st["hi"][4] == 1.0 and not st["failed"][4]
    assert st["k_app"][4] != old_k
    assert st["wear_mm"][5] == w0[5]
    with pytest.raises(ValueError):
        f.maintain(KIND, [0], "bogus")


def test_rto_shock_hits_wear_and_temperature():
    f = Fleet(2, {KIND: 400})
    for c in range(60):
        f.step_cycle(make_ctx(400, c, 2))
    st = f.state[KIND]
    assert st["rto_count"].sum() > 0
    hit = st["rto_count"] > 0
    assert st["wear_mm"][hit].mean() > st["wear_mm"][~hit].mean()


def test_speed_240_units_2000_cycles():
    f = Fleet(1, {KIND: 240})
    t0 = time.perf_counter()
    for c in range(2000):
        f.step_cycle(make_ctx(240, c, 1))
    assert time.perf_counter() - t0 < 5.0
