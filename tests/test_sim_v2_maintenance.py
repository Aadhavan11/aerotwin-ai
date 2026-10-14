from datetime import datetime, timedelta

import numpy as np

from app.sim.v2 import Fleet
from app.sim.v2.maintenance import (
    CheckDef, InspectionRecord, InstallRegistry, MaintenanceEngine, MelBook, ScheduledCheckGenerator, pod_curve,
)
from tests.test_sim_v2_core import _ctx

NOW = datetime(2025, 10, 1)


def _fleet(n=4, cycles=100):
    f = Fleet(3, {"_reference": n})
    ctx = None
    for _ in range(cycles):
        ctx = _ctx(n=n, cycle=f.cycle, prev=ctx)
        f.step_cycle(ctx)
    return f


def test_replace_removal_record_and_flags():
    f = _fleet()
    eng = MaintenanceEngine(seed=1)
    eng.registry.populate({"_reference": 4}, {"_reference": ["T0", "T1", "T2", "T3"]})
    old = eng.registry.on_unit("_reference", 1).serial
    r = eng.apply(f, "_reference", [1], "replace", reason="failure", now=NOW)[0]
    assert r.provenance == "SIMULATED" and r.serial == old and r.unscheduled and r.cost_class == "replace"
    assert r.new_serial and r.new_serial != old and r.tail == "T1"
    assert eng.registry.on_unit("_reference", 1).serial == r.new_serial
    assert eng.registry.parts[old].status == "removed"
    assert f.state["_reference"]["damage"][1] == 0.0
    assert eng.registry.history(old) == [r]
    assert r.as_dict()["removal_reason"] == "failure"


def test_nff_and_scheduled_flags():
    f = _fleet(cycles=5)  # healthy
    eng = MaintenanceEngine()
    before = f.state_hash()
    serial = eng.registry.on_unit("_reference", 0).serial if eng.registry.on_unit("_reference", 0) else None
    nff = eng.apply(f, "_reference", [0], "replace", reason="nff", now=NOW)[0]
    # no-fault-found: unit returned unchanged (state bit-identical, same serial, no new serial), unscheduled, flagged NFF
    assert nff.nff and nff.unscheduled and nff.action == "nff_return" and nff.new_serial is None
    assert f.state_hash() == before
    assert serial is None or eng.registry.on_unit("_reference", 0).serial == serial == nff.serial
    # an explicit failure removal of a healthy unit is a real replacement, not NFF
    rep = eng.apply(f, "_reference", [3], "replace", reason="failure", now=NOW)[0]
    assert rep.unscheduled and not rep.nff and rep.new_serial and rep.new_serial != rep.serial
    sch = eng.apply(f, "_reference", [1], "overhaul", reason="scheduled", now=NOW)[0]
    assert not sch.unscheduled and not sch.nff and sch.cost_class == "overhaul"
    assert eng.apply(f, "_reference", [2], "repair", reason="nff")[0].nff


def test_q_semantics_and_overhaul_resets_tso():
    f = _fleet()
    eng = MaintenanceEngine()
    d0 = f.state["_reference"]["damage"].copy()
    eng.apply(f, "_reference", [0], "repair", q=0.25)
    np.testing.assert_allclose(f.state["_reference"]["damage"][0], 0.25 * d0[0])
    eng.registry.populate({"_reference": 4})
    p = eng.registry.on_unit("_reference", 2)
    p.accrue(10, 5)
    r = eng.apply(f, "_reference", [2], "overhaul", q=0.5)[0]
    assert p.tso_h == 0 and p.cso == 0 and p.tsn_h == 10 and r.q == 0.5


def test_pod_monotone_and_inspection():
    s = np.linspace(0, 1, 50)
    p = pod_curve(s)
    assert np.all(np.diff(p) >= 0) and p[0] < 0.1 and p[-1] > 0.9
    f = _fleet(cycles=300)
    eng = MaintenanceEngine(seed=2)
    recs = eng.apply(f, "_reference", [0, 1, 2, 3], "inspect", now=NOW)
    assert all(isinstance(r, InspectionRecord) and r.provenance == "SIMULATED" for r in recs)
    assert eng.removals == []
    eng2 = MaintenanceEngine(seed=2)
    assert [r.detected for r in eng2.apply(f, "_reference", [0, 1, 2, 3], "inspect")] == [r.detected for r in recs]


def test_mel_categories_and_expiry():
    book = MelBook()
    b = book.defer("apu", 0, "B", NOW, "APU inop", tail="T0")
    c = book.defer("ecs", 0, "C", NOW)
    a = book.defer("tires", 0, "A", NOW, limit_days=2.0)
    d = book.defer("avionics", 0, "D", NOW)
    assert [x.interval_days for x in (a, b, c, d)] == [2.0, 3.0, 10.0, 120.0]
    assert b.restriction == "APU inoperative: ground power unit required"
    assert b.as_dict()["interval_text"] == "B: 3 days"
    assert not book.expired(NOW + timedelta(days=1))
    assert {x.id for x in book.expired(NOW + timedelta(days=3))} == {a.id, b.id}
    book.close("apu", 0, NOW + timedelta(days=2))
    assert {x.id for x in book.expired(NOW + timedelta(days=11))} == {a.id, c.id}
    assert "APU inoperative: ground power unit required" not in book.restrictions()


def test_scheduled_whichever_first():
    g = ScheduledCheckGenerator([CheckDef("c1", "brakes", interval_fh=100, interval_fc=50, interval_days=30)])
    assert g.due("brakes", 0, 50, 20, 10) == []
    d = g.due("brakes", 0, 60, 50, 10)
    assert d[0].trigger == "fc"
    assert g.due("brakes", 0, 120, 30, 10)[0].trigger == "fh"
    assert g.due("brakes", 0, 10, 10, 31)[0].trigger == "days"
    g.accomplish("c1", 0, 120, 30, 31)
    assert g.due("brakes", 0, 130, 40, 35) == []
    assert g.due("brakes", 1, 130, 40, 35)[0].unit == 1
    assert g.due("engine", 0, 1e9, 1e9, 1e9) == []


def test_install_registry_duplicate():
    r = InstallRegistry()
    r.install("engine", 0)
    try:
        r.install("engine", 0)
    except ValueError:
        return
    raise AssertionError("expected ValueError")
