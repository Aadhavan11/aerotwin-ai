"""Tires v2 model tests. Life bands are literature priors, not validation against fleet data."""

import time
from dataclasses import fields

import numpy as np
import pytest
from scipy import stats

from app.sim.v2 import Fleet, get
from app.sim.v2.drivers import OperatingContext
from app.sim.v2.models import tires as MOD

KIND = "tires"


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


STRESS = 2.0
BAND_MEDIAN = 134.0
BAND_SIGMA = 0.18


def test_registered_and_v1_channels():
    m = get(KIND)
    assert {"tread_depth_mm", "tire_pressure_psi"} <= set(m.channels)
    f = run_fixed(5, n=8)
    out = f.emit_all(make_ctx(8, 4, 3))[KIND]
    assert set(out) == set(m.channels)
    assert all(v.shape == (8,) and np.all(np.isfinite(v)) for v in out.values())
    assert 1.0 < out["tread_depth_mm"].mean() < 9.5
    assert 150.0 < out["tire_pressure_psi"].mean() < 260.0


def test_params_documented_as_prior():
    src = open(MOD.__file__).read()
    assert src.count("assumed (no citation)") >= len(MOD.PARAMS)


def test_life_distribution_band_prior():
    """Accelerated (stress_ext=2) landings-to-removal vs a tolerant lognormal prior band; a prior, not validation."""
    life = life_cycles(stress=STRESS)
    assert np.isfinite(life).all()
    ref = np.random.default_rng(0).lognormal(np.log(BAND_MEDIAN), BAND_SIGMA, 4000)
    ks = stats.ks_2samp(life, ref)
    assert ks.pvalue > 0.001, (ks, np.median(life))


def test_hi_monotone_and_bounded():
    f = Fleet(5, {KIND: 64})
    prev = f.health_index()[KIND].copy()
    for c in range(200):
        f.step_cycle(make_ctx(64, c, 5))
        hi = f.health_index()[KIND]
        assert np.all(hi <= prev + 1e-12) and hi.min() >= 0.0 and hi.max() <= 1.0
        prev = hi.copy()
    assert prev.mean() < 0.9


def test_wet_runway_and_sink_and_weight_raise_wear():
    dry = run_fixed(80, wet_runway=False).state[KIND]["tread_loss_mm"].mean()
    wet = run_fixed(80, wet_runway=True).state[KIND]["tread_loss_mm"].mean()
    assert wet > dry * 1.05
    soft = run_fixed(80, sink_rate_fps=2.0).state[KIND]["tread_loss_mm"].mean()
    hard = run_fixed(80, sink_rate_fps=4.0).state[KIND]["tread_loss_mm"].mean()
    assert hard > soft * 1.1
    far = run_fixed(80, taxi_distance_km=12.0).state[KIND]["tread_loss_mm"].mean()
    near = run_fixed(80, taxi_distance_km=2.0).state[KIND]["tread_loss_mm"].mean()
    assert far > near * 1.1


def test_pressure_leak_calendar_and_temperature_and_nitrogen_topup():
    f = run_fixed(150, n=16)
    st = f.state[KIND]
    assert (st["p_cold"] < MOD.PARAMS["P_SET_PSI"]).all()
    hot = run_fixed(150, n=32, oat_c=38.0).state[KIND]["p_cold"].mean()
    cold = run_fixed(150, n=32, oat_c=0.0).state[KIND]["p_cold"].mean()
    assert hot < cold
    f.maintain(KIND, [0, 1], "inspect")
    assert (st["p_cold"][:2] == MOD.PARAMS["P_SET_PSI"]).all() and (st["n2_topups"][:2] == 1).all()
    assert st["p_cold"][2] < MOD.PARAMS["P_SET_PSI"]


def test_fod_cuts_cause_pressure_loss_and_failures():
    f = Fleet(2, {KIND: 600})
    f.state[KIND]["stress_ext"][:] = 1.0
    for c in range(250):
        f.step_cycle(make_ctx(600, c, 2, dust_index=0.9))
    st = f.state[KIND]
    cut = st["cuts"] > 0
    assert cut.sum() > 20
    assert st["p_cold"][cut].mean() < st["p_cold"][~cut].mean()
    assert st["cut_dmg"][cut].mean() > 0.2


def test_stress_ext_scales_tread_loss():
    base = run_fixed(80).state[KIND]["tread_loss_mm"].mean()
    f = Fleet(3, {KIND: 64})
    f.state[KIND]["stress_ext"][:] = 2.0
    for c in range(80):
        f.step_cycle(make_ctx(64, c, 3))
    assert f.state[KIND]["tread_loss_mm"].mean() > 1.8 * base


def test_determinism_and_resume_bit_identity():
    a, b = run_fixed(60, n=32, seed=9), run_fixed(60, n=32, seed=9)
    assert a.state_hash() == b.state_hash()
    assert run_fixed(60, n=32, seed=10).state_hash() != a.state_hash()
    snap = run_fixed(30, n=32, seed=9).snapshot()
    g = Fleet(9, {KIND: 32})
    g.restore(snap)
    for c in range(30, 60):
        g.step_cycle(make_ctx(32, c, 9))
    assert g.state_hash() == a.state_hash()
    e1 = g.emit_all(make_ctx(32, 59, 9))[KIND]
    e2 = a.emit_all(make_ctx(32, 59, 9))[KIND]
    assert all(np.array_equal(e1[k], e2[k]) for k in e1)


def test_maintain_semantics_and_retread_limit():
    f = run_fixed(150, n=16, seed=4)
    st = f.state[KIND]
    loss0, hi0 = st["tread_loss_mm"].copy(), st["hi"].copy()
    f.maintain(KIND, [0], "inspect")
    assert st["tread_loss_mm"][0] == loss0[0] and st["inspected"][0] and not st["inspected"][1]
    obs = f.emit_all(make_ctx(16, 149, 4))[KIND]["tread_depth_mm"]
    assert abs(obs[0] - (9.0 - loss0[0])) < 0.3
    f.maintain(KIND, [2], "repair", q=0.5)
    assert st["tread_loss_mm"][2] < loss0[2] and st["hi"][2] >= hi0[2]
    f.maintain(KIND, [3], "overhaul")
    assert st["tread_loss_mm"][3] == 0.0 and st["retreads"][3] == 1 and st["hi"][3] > hi0[3]
    st["retreads"][5] = MOD.PARAMS["RETREAD_MAX"]
    f.maintain(KIND, [5], "overhaul")
    assert st["condemned"][5] and st["failed"][5] and st["tread_loss_mm"][5] == loss0[5]
    f.maintain(KIND, [5], "replace")
    assert not st["failed"][5] and not st["condemned"][5] and st["retreads"][5] == 0 and st["tread_loss_mm"][5] == 0.0
    assert st["age_cycles"][5] == 0
    with pytest.raises(ValueError):
        f.maintain(KIND, [0], "bogus")


def test_speed_240_units_2000_cycles():
    f = Fleet(1, {KIND: 240})
    t0 = time.perf_counter()
    for c in range(2000):
        f.step_cycle(make_ctx(240, c, 1))
    assert time.perf_counter() - t0 < 5.0
