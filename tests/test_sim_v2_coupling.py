import numpy as np
import pytest

from app.sim.v2.coupling import CouplingGraph, Edge
from app.sim.v2.core import Fleet, Registry


def _reg():
    reg = Registry()

    class Base:
        channels: tuple[str, ...] = ()

        def init(self, n, rng):
            return {"hi": np.ones(n)}

        def step(self, state, ctx, rng):
            pass

        def emit(self, state, ctx, rng):
            return {}

        def health_index(self, state):
            return state["hi"]

        def maintain(self, state, idx, action, q, rng):
            pass

    @reg.register("hydraulics")
    class H(Base):
        def init(self, n, rng):
            return {"hi": np.ones(n), "eta_loss": np.array([0.0, 0.2, 1.0, 0.5][:n]), "leak": np.zeros(n), "precharge_loss": np.zeros(n)}

    @reg.register("brakes")
    class B(Base):
        pass

    @reg.register("electrical")
    class E(Base):
        pass

    @reg.register("tires")
    class T(Base):
        pass

    @reg.register("avionics")
    class A(Base):
        pass

    return reg


def test_gain_clamp_and_missing_kinds():
    reg = _reg()
    f = Fleet(1, {"hydraulics": 4, "brakes": 4}, registry=reg)
    g = CouplingGraph()
    out = g.apply(f)
    np.testing.assert_allclose(out["brakes"], [1.0, 1.2, 1.5, 1.5])  # gain 1, clamp 0.5
    np.testing.assert_allclose(f.state["brakes"]["stress_ext"], out["brakes"])
    assert "tires" not in f.state["brakes"]


def test_missing_key_skipped_and_disable():
    reg = _reg()
    f = Fleet(1, {"electrical": 2, "avionics": 2, "hydraulics": 2, "brakes": 2}, registry=reg)
    g = CouplingGraph()
    out = g.apply(f)
    assert "avionics" not in out and np.all(f.state["avionics"]["stress_ext"] == 1.0)  # no bus voltage key: skipped, visibly
    rep = g.report()["electrical>avionics"]
    assert rep["applied_count"] == 0 and "bus_voltage_v" in rep["skipped_reason"]
    with pytest.raises(KeyError):
        CouplingGraph(strict=True).apply(f)
    f.state["electrical"]["bus_voltage_v"] = np.array([27.5, 24.7])
    np.testing.assert_allclose(g.apply(f)["avionics"], [1.0, 1.4])
    assert g.report()["electrical>avionics"]["applied_count"] == 1
    g.set_enabled("electrical>avionics", False)
    assert "avionics" not in g.apply(f)
    assert g.report()["electrical>avionics"]["skipped_reason"] == "disabled"


def test_additive_edges_and_total_clamp():
    reg = _reg()
    f = Fleet(1, {"hydraulics": 4, "brakes": 4, "tires": 4}, registry=reg)
    f.state["brakes"]["temp_c"] = np.array([200.0, 500.0, 700.0, 900.0])
    g = CouplingGraph(edges=[
        Edge("a", "hydraulics", "tires", lambda s, c: s["eta_loss"] * 0 + 1.0, gain=1.0, clamp=0.9),
        Edge("b", "brakes", "tires", lambda s, c: s["temp_c"] / 1000.0, gain=1.0, clamp=2.0),
    ], max_total=2.5)
    out = g.apply(f)["tires"]
    np.testing.assert_allclose(out, [2.1, 2.4, 2.5, 2.5])


def test_unaligned_units_skipped_and_tail_mapping():
    reg = _reg()
    f = Fleet(1, {"hydraulics": 4, "brakes": 3}, registry=reg)
    assert CouplingGraph().apply(f) == {}
    f2 = Fleet(1, {"hydraulics": 4, "brakes": 2}, unit_tail={"hydraulics": np.array([0, 1, 2, 3]), "brakes": np.array([1, 3])}, registry=reg)
    np.testing.assert_allclose(CouplingGraph().apply(f2)["brakes"], [1.2, 1.5])


def test_default_brake_heat_to_tires():
    reg = _reg()
    f = Fleet(1, {"brakes": 2, "tires": 2}, registry=reg)
    f.state["brakes"]["temp_c"] = np.array([250.0, 500.0])
    np.testing.assert_allclose(CouplingGraph().apply(f)["tires"], [1.0, 1.25])
