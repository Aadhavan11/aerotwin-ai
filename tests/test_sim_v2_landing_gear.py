"""Landing gear v2 model tests. Life bands are literature priors, not validation against fleet data."""

import time
from dataclasses import fields

import numpy as np
import pytest
from scipy import stats

from app.sim.v2 import Fleet, get
from app.sim.v2.drivers import OperatingContext
from app.sim.v2.models import landing_gear as MOD

KIND = "landing_gear"


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


STRESS = 8.0
BAND_MEDIAN = 930.0
BAND_SIGMA = 0.25


def test_registered_and_v1_channels():
    m = get(KIND)
    assert {"strut_pressure_kpa", "shock_load_g"} <= set(m.channels)
    f = run_fixed(5, n=8)
    out = f.emit_all(make_ctx(8, 4, 3))[KIND]
    assert set(out) == set(m.channels)
    assert all(v.shape == (8,) and np.all(np.isfinite(v)) for v in out.values())
    assert 1700.0 < out["strut_pressure_kpa"].mean() < 1850.0
    assert 1.0 < out["shock_load_g"].mean() < 2.5
    assert set(np.unique(out["hard_landing_flag"])) <= {0.0, 1.0}


def test_params_documented_as_prior():
    src = open(MOD.__file__).read()
    assert src.count("assumed (no citation)") >= len(MOD.PARAMS)


def test_life_distribution_band_prior():
    """Accelerated (stress_ext=8) cycles-to-failure vs a tolerant lognormal prior band; a prior, not validation."""
    life = life_cycles(stress=STRESS, max_cycles=4000)
    assert np.isfinite(life).all()
    ref = np.random.default_rng(0).lognormal(np.log(BAND_MEDIAN), BAND_SIGMA, 4000)
    ks = stats.ks_2samp(life, ref)
    assert ks.pvalue > 0.001, (ks, np.median(life))


def test_hi_monotone_and_bounded():
    f = Fleet(5, {KIND: 64})
    prev = f.health_index()[KIND].copy()
    for c in range(400):
        f.step_cycle(make_ctx(64, c, 5))
        hi = f.health_index()[KIND]
        assert np.all(hi <= prev + 1e-12) and hi.min() >= 0.0 and hi.max() <= 1.0
        prev = hi.copy()
    assert prev.mean() < 0.99


def test_strut_pressure_decays_monotonically_gamma_process():
    f = Fleet(5, {KIND: 32})
    prev = np.zeros(32)
    for c in range(200):
        f.step_cycle(make_ctx(32, c, 5))
        assert np.all(f.state[KIND]["leak_kpa"] >= prev)
        prev = f.state[KIND]["leak_kpa"].copy()
    assert prev.std() > 0 and prev.mean() > 5.0


def test_sink_rate_drives_load_fatigue_and_hard_landing_flag():
    soft = run_fixed(200, sink_rate_fps=2.0).state[KIND]
    firm = run_fixed(200, sink_rate_fps=4.0).state[KIND]
    assert firm["fatigue"].mean() > 1.5 * soft["fatigue"].mean()
    assert firm["crack_mm"].mean() > soft["crack_mm"].mean()
    hard = run_fixed(20, sink_rate_fps=8.0).state[KIND]
    assert hard["inspection_due"].all() and (hard["hard_count"] == 20).all()
    assert not soft["inspection_due"].any()
    f = run_fixed(20, n=8, sink_rate_fps=8.0)
    out = f.emit_all(make_ctx(8, 19, 3, sink_rate_fps=8.0))[KIND]
    assert (out["hard_landing_flag"] == 1.0).all()
    f.maintain(KIND, np.arange(8), "inspect")
    assert not f.state[KIND]["inspection_due"].any()


def test_crack_detected_only_at_inspection_with_pod_curve():
    f = Fleet(6, {KIND: 400})
    st = f.state[KIND]
    st["crack_mm"][:200] = 0.2   # tiny flaws
    st["crack_mm"][200:] = 15.0  # large cracks
    assert not st["crack_detected"].any()
    f.step_cycle(make_ctx(400, 0, 6))
    assert not st["crack_detected"].any()  # nothing is observable without inspection
    f.maintain(KIND, np.arange(400), "inspect")
    assert st["crack_detected"][:200].mean() < 0.05
    assert st["crack_detected"][200:].mean() > 0.95


def test_stress_ext_scales_leak_and_damage():
    base = run_fixed(150).state[KIND]
    f = Fleet(3, {KIND: 64})
    f.state[KIND]["stress_ext"][:] = 3.0
    for c in range(150):
        f.step_cycle(make_ctx(64, c, 3))
    st = f.state[KIND]
    assert st["leak_kpa"].mean() > 2.5 * base["leak_kpa"].mean()
    assert st["fatigue"].mean() > 2.9 * base["fatigue"].mean()


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


def test_maintain_semantics():
    f = Fleet(4, {KIND: 16})
    f.state[KIND]["stress_ext"][:] = 20.0
    for c in range(250):
        f.step_cycle(make_ctx(16, c, 4))
    st = f.state[KIND]
    leak0, fat0, crack0, hi0, age0 = st["leak_kpa"].copy(), st["fatigue"].copy(), st["crack_mm"].copy(), st["hi"].copy(), st["age_cycles"].copy()
    f.maintain(KIND, [0], "inspect")
    assert st["leak_kpa"][0] == leak0[0] and st["fatigue"][0] == fat0[0]
    f.maintain(KIND, [1], "repair", q=0.5)
    assert st["leak_kpa"][1] == pytest.approx(0.5 * leak0[1]) and st["fatigue"][1] < fat0[1]
    assert st["crack_mm"][1] < crack0[1] and st["age_cycles"][1] == age0[1] and st["hi"][1] > hi0[1]
    f.maintain(KIND, [2], "overhaul", q=1.0)
    assert st["leak_kpa"][2] == 0.0 and st["fatigue"][2] == pytest.approx(0.3 * fat0[2]) and st["crack_mm"][2] == st["crack_a0"][2]
    old = st["paris_c"][3]
    f.maintain(KIND, [3], "replace")
    assert st["leak_kpa"][3] == 0 and st["fatigue"][3] == 0 and st["age_cycles"][3] == 0 and st["hi"][3] > 0.95 and not st["failed"][3]
    assert st["paris_c"][3] != old and st["crack_mm"][3] == st["crack_a0"][3]
    assert st["leak_kpa"][4] == leak0[4]
    with pytest.raises(ValueError):
        f.maintain(KIND, [0], "bogus")


def test_speed_240_units_2000_cycles():
    f = Fleet(1, {KIND: 240})
    t0 = time.perf_counter()
    for c in range(2000):
        f.step_cycle(make_ctx(240, c, 1))
    assert time.perf_counter() - t0 < 5.0
