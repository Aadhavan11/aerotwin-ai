import time
from datetime import datetime, timedelta

import numpy as np
import pytest

from app.contracts.models import Leg
from app.sim.profile import profile_for
from app.sim.v2 import ComponentModel, Fleet, StreamRng, build_context, get, kinds, tail_utilisation
from app.sim.v2.core import Registry

T0 = datetime(2025, 10, 1, 8, 0)


def _ctx(n=8, seed=1, arr="DEN", cycle=0, prev=None, dep="DFW", tails=None):
    tails = tails or [f"T{i}" for i in range(n)]
    profs = []
    for i, t in enumerate(tails):
        leg = Leg(id=f"L{cycle}-{i}", tail=t, dep=dep, arr=arr, sched_dep=T0 + timedelta(days=cycle), sched_arr=T0 + timedelta(days=cycle, hours=2))
        profs.append(profile_for(seed, leg))
    return build_context(profs, tails, seed, [arr] * len(tails), prev=prev)


def _run(fleet, cycles, batch, seed=1):
    ctx = None
    done = 0
    while done < cycles:
        for _ in range(min(batch, cycles - done)):
            ctx = _ctx(n=fleet.counts["_reference"], seed=seed, cycle=fleet.cycle, prev=ctx)
            fleet.step_cycle(ctx)
            done += 1
    return ctx


def test_determinism():
    a, b = Fleet(7, {"_reference": 16}), Fleet(7, {"_reference": 16})
    _run(a, 50, 50)
    _run(b, 50, 50)
    assert a.state_hash() == b.state_hash()
    c = Fleet(8, {"_reference": 16})
    _run(c, 50, 50)
    assert c.state_hash() != a.state_hash()


@pytest.mark.parametrize("batch", [1, 7, 40])
def test_resume_bit_identity(batch):
    straight = Fleet(3, {"_reference": 12})
    ctx = _run(straight, 40, 40, seed=3)
    f = Fleet(3, {"_reference": 12})
    ctx2 = _run(f, 20, batch, seed=3)
    snap = f.snapshot()
    g = Fleet(3, {"_reference": 12})
    g.restore(snap)
    for _ in range(20):
        ctx2 = _ctx(n=12, seed=3, cycle=g.cycle, prev=ctx2)
        g.step_cycle(ctx2)
    assert g.state_hash() == straight.state_hash()
    assert np.array_equal(g.emit_all(ctx2)["_reference"]["ref_signal"], straight.emit_all(ctx)["_reference"]["ref_signal"])
    with pytest.raises(ValueError):
        Fleet(4, {"_reference": 12}).restore(snap)


def test_snapshot_npz_serialisable(tmp_path):
    f = Fleet(1, {"_reference": 4})
    _run(f, 3, 3)
    np.savez(tmp_path / "s.npz", **f.snapshot())
    loaded = dict(np.load(tmp_path / "s.npz"))
    g = Fleet(1, {"_reference": 4})
    g.restore(loaded)
    assert g.state_hash() == f.state_hash()


def test_stream_rng_independence():
    a = StreamRng(1, "x").at(5).standard_normal(1000)
    assert np.array_equal(a, StreamRng(1, "x").at(5).standard_normal(1000))
    for other in (StreamRng(1, "y").at(5), StreamRng(2, "x").at(5), StreamRng(1, "x", block=1).at(5), StreamRng(1, "x").at(6)):
        b = other.standard_normal(1000)
        assert abs(np.corrcoef(a, b)[0, 1]) < 0.15
    assert abs(np.corrcoef(a[:500], a[500:])[0, 1]) < 0.15


def test_driver_distributions():
    den = _ctx(n=40, arr="DEN")
    mia = _ctx(n=40, arr="MIA")
    assert den.density_alt_ft.mean() > mia.density_alt_ft.mean()
    assert (den.apu_hours > 0).all() and (den.taxi_distance_km > 0).all()
    assert den.n == 40 and den.brake_applications.dtype.kind == "i"
    # brake energy rises with landing weight (same arrival station)
    order = np.argsort(den.landing_weight_frac)
    lo, hi = order[:10], order[-10:]
    assert den.brake_energy_mj[hi].mean() > den.brake_energy_mj[lo].mean()
    # wet runways: more applications and energy than dry (pooled over many legs)
    many = _ctx(n=300, arr="MIA", seed=5)
    w = many.wet_runway
    assert w.any() and (~w).any()
    assert many.brake_applications[w].mean() > many.brake_applications[~w].mean()
    assert many.brake_energy_mj[w].mean() > many.brake_energy_mj[~w].mean()


def test_utilisation_and_counters():
    assert tail_utilisation(1, "N1") == tail_utilisation(1, "N1")
    vals = [tail_utilisation(1, f"N{i}") for i in range(100)]
    assert min(vals) >= 0.7 and max(vals) <= 1.3 and len(set(vals)) > 50
    c1 = _ctx(n=3)
    c2 = _ctx(n=3, cycle=1, prev=c1)
    assert (c2.flight_cycles == 2).all() and (c2.total_apu_hours > c1.total_apu_hours).all()
    assert (c2.calendar_days > c1.calendar_days).all() and (c2.total_flight_hours > c1.total_flight_hours).all()


def test_maintain_and_monotone_hi():
    f = Fleet(2, {"_reference": 10})
    ctx = None
    prev = f.health_index()["_reference"].copy()
    for _ in range(500):
        ctx = _ctx(n=10, seed=2, cycle=f.cycle, prev=ctx)
        f.step_cycle(ctx)
        cur = f.health_index()["_reference"]
        assert (cur <= prev + 1e-12).all()
        prev = cur.copy()
    st = f.state["_reference"]
    idx = np.array([0, 1])
    before_v = st["age_virtual"][idx].copy()
    rate = st["wear_rate"][idx].copy()
    f.maintain("_reference", idx, "repair", 0.5)
    assert np.allclose(st["age_virtual"][idx], before_v * 0.5)
    assert (st["hi"][idx] >= prev[idx]).all()
    f.maintain("_reference", idx, "replace")
    assert (st["age_cycles"][idx] == 0).all() and (st["age_virtual"][idx] == 0).all() and (st["hi"][idx] == 1).all()
    assert not np.allclose(st["wear_rate"][idx], rate)
    with pytest.raises(ValueError):
        f.maintain("_reference", idx, "nope")


def test_registry():
    assert "_reference" in kinds()
    m = get("_reference")
    assert isinstance(m, ComponentModel) and m.kind == "_reference"
    with pytest.raises(KeyError):
        get("nope")
    r = Registry()

    @r.register("k")
    class K:
        pass

    assert r.kinds() == ["k"]
    with pytest.raises(ValueError):
        r.register("k")(K)


def test_performance_gate():
    n = 240
    f = Fleet(1, {"_reference": n})
    ctx = _ctx(n=n)
    t = time.perf_counter()
    for _ in range(2000):
        f.step_cycle(ctx)
    f.emit_all(ctx)
    assert time.perf_counter() - t < 5.0
