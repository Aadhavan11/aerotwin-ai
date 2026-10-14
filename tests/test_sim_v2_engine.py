from __future__ import annotations

import numpy as np
import pytest

from app.ml.sensors import SENSOR_NAMES
from app.sim.profile import profile_for_cycle
from app.sim.v2 import Fleet, build_context
from app.sim.v2.core import get, kinds
from app.sim.v2.models import engine as eng

STATIONS = ("ATL", "DFW", "JFK", "ORD", "LAX", "SFO", "DEN", "MIA")


def _ctx(n: int, c: int, prev=None, seed: int = 5):
    tails = [f"E{i}" for i in range(n)]
    profs = [profile_for_cycle(seed, t, c) for t in tails]
    return build_context(profs, tails, seed, [STATIONS[(i + c) % 8] for i in range(n)], prev=prev)


def _run(fleet: Fleet, cycles: int, collect: bool = True):
    prev, out = None, []
    n = fleet.counts["engine"]
    for c in range(cycles):
        prev = _ctx(n, c, prev)
        fleet.step_cycle(prev)
        if collect:
            out.append(fleet.emit_all(prev)["engine"])
    return out, prev


def test_registered_with_channel_order() -> None:
    assert "engine" in kinds()
    m = get("engine")
    assert m.channels[:14] == tuple(SENSOR_NAMES)
    assert m.channels[14:] == ("egt_margin_c", "llp_cycles_remaining", "hpc_eff_pct")


def test_emit_shape_finite_and_hi_monotone() -> None:
    f = Fleet(3, {"engine": 6})
    prev_hi = f.health_index()["engine"].copy()
    prev = None
    for c in range(60):
        prev = _ctx(6, c, prev)
        f.step_cycle(prev)
        em = f.emit_all(prev)["engine"]
        assert set(em) == set(get("engine").channels)
        assert all(np.isfinite(v).all() and v.shape == (6,) for v in em.values())
        hi = f.health_index()["engine"]
        assert ((hi >= 0) & (hi <= 1)).all()
        assert (hi <= prev_hi + 1e-12).all()
        prev_hi = hi.copy()


def test_deterministic_and_snapshot_roundtrip() -> None:
    a, b = Fleet(9, {"engine": 4}), Fleet(9, {"engine": 4})
    _run(a, 25, collect=False)
    _run(b, 25, collect=False)
    assert a.state_hash() == b.state_hash()
    snap = a.snapshot()
    c = Fleet(9, {"engine": 4})
    c.restore(snap)
    assert c.state_hash() == a.state_hash()


def test_cycles_to_failure_loose_guard() -> None:
    n = 24
    f = Fleet(11, {"engine": n})
    life = np.full(n, -1)
    prev = None
    for c in range(600):
        prev = _ctx(n, c, prev, seed=11)
        f.step_cycle(prev)
        fin = f.state["engine"]["failed"] & (life < 0)
        life[fin] = c + 1
        if (life > 0).all():
            break
    assert (life > 0).all()
    assert 150 < life.mean() < 260  # FD001 train mean is ~206 (loose regression guard, not a validity claim)
    assert 15 < life.std() < 90


def test_stress_ext_and_hot_accelerate_wear() -> None:
    lo, hi = Fleet(2, {"engine": 8}), Fleet(2, {"engine": 8})
    hi.state["engine"]["stress_ext"][:] = 2.0
    _run(lo, 40, collect=False)
    _run(hi, 40, collect=False)
    assert hi.state["engine"]["progress"].mean() > 1.8 * lo.state["engine"]["progress"].mean()


def test_hpt_erosion_fault_lowers_health_and_egt_margin() -> None:
    clean, sick = Fleet(4, {"engine": 5}), Fleet(4, {"engine": 5})
    sick.state["engine"]["fault_hpt_blade_erosion"] = np.full(5, 0.8)
    _, ctx = _run(clean, 20, collect=False)
    _run(sick, 20, collect=False)
    assert (sick.health_index()["engine"] < clean.health_index()["engine"]).all()
    ec = clean.models["engine"].emit(clean.state["engine"], ctx, np.random.default_rng(0))
    es = sick.models["engine"].emit(sick.state["engine"], ctx, np.random.default_rng(0))
    assert (es["egt_margin_c"] < ec["egt_margin_c"]).all()
    # a fault that later clears must not heal the engine without maintenance
    h = sick.health_index()["engine"].copy()
    sick.state["engine"]["fault_hpt_blade_erosion"] = np.zeros(5)
    _run(sick, 1, collect=False)
    assert (sick.health_index()["engine"] <= h + 1e-12).all()


def test_maintenance_actions() -> None:
    f = Fleet(8, {"engine": 4})
    _, _ = _run(f, 80, collect=False)
    st = f.state["engine"]
    p0, llp0 = st["progress"].copy(), st["llp_used"].copy()
    f.maintain("engine", [0], "inspect")
    assert st["progress"][0] == p0[0] and st["inspected"][0]
    f.maintain("engine", [1], "repair", 0.5)
    assert st["progress"][1] < p0[1]
    f.maintain("engine", [2], "overhaul", 0.5)
    assert st["progress"][2] < 0.5 * p0[2]
    assert st["llp_used"][2] == llp0[2]  # overhaul does not reset the LLP counter
    f.maintain("engine", [3], "replace")
    assert st["progress"][3] == 0.0 and st["llp_used"][3] == 0.0 and st["age_cycles"][3] == 0.0
    assert (f.health_index()["engine"][[1, 2, 3]] > 1.0 - p0[[1, 2, 3]] - 1e-9).all()
    with pytest.raises(ValueError):
        f.maintain("engine", [0], "polish")


def test_llp_exhaustion_fails_unit() -> None:
    f = Fleet(1, {"engine": 2})
    f.state["engine"]["llp_used"][:] = f.state["engine"]["llp_limit"] - 2.0
    _run(f, 3, collect=False)
    assert f.state["engine"]["failed"].all()
    assert (f.health_index()["engine"] == 0.0).all()
    em = f.emit_all(_ctx(2, 3))["engine"]
    assert (em["llp_cycles_remaining"] == 0.0).all()


def test_variant_switch_multicondition_emits_wide_baselines() -> None:
    f = Fleet(6, {"engine": 20})
    eng.EngineModel.set_variant(f.state["engine"], np.arange(20), "fd002", np.random.default_rng(1))
    out, _ = _run(f, 5)
    t30 = np.concatenate([o["T30"] for o in out])
    f1 = Fleet(6, {"engine": 20})
    out1, _ = _run(f1, 5)
    t30_1 = np.concatenate([o["T30"] for o in out1])
    assert t30.std() > 5 * t30_1.std()  # 6 operating conditions vs one
