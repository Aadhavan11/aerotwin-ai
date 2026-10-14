"""Reviewer-hardening behaviours: NFF return semantics, engine performance reason, infant mortality, induced faults,
gear servicing, and the v2-record Weibull fit. All priors are ASSUMED (no citation); tests check direction and bounds."""
from __future__ import annotations

import random
from datetime import timedelta

import numpy as np
import pytest

from app.insights import reliability, reliability_pack as rp
from app.sim.clock import START
from app.sim.v2 import world as W
from app.sim.v2.maintenance import REMOVAL_REASONS, _PLANNED
from app.sim.v2.models import avionics as av
from app.sim.v2.world import World


INFANT0 = dict(W.INFANT)


def _tails(n: int) -> list[str]:
    return [f"SIM-{i:03d}" for i in range(n)]


# ---- 3. NFF returns the unit ----
def test_nff_return_keeps_state_serial_and_counters():
    w = World(5, _tails(4), faults=False, auto_maintain=False)
    w.run(20)
    kind = "avionics"
    before_state = {k: v.copy() for k, v in w.fleet.state[kind].items()}
    part = w.registry.on_unit(kind, 0)
    serial, tsn, csn = part.serial, part.tsn_h, part.csn
    w.apply_action(kind, 0, "replace", reason="nff")
    rec = w.removals()[-1]
    assert rec.nff and rec.unscheduled and rec.action == "nff_return" and rec.new_serial is None and rec.serial == serial
    after = w.registry.on_unit(kind, 0)
    assert (after.serial, after.tsn_h, after.csn) == (serial, tsn, csn)
    for k, v in before_state.items():
        assert np.array_equal(w.fleet.state[kind][k][0], v[0]), k


def test_nff_counts_in_mtbur_not_mtbf():
    t0 = START
    recs = [rp.RemovalRec("avionics", "T1", "avionics", "nff_return", "nff", True, True, 0.9, t0),
            rp.RemovalRec("avionics", "T1", "avionics", "replace", "failure", True, False, 0.0, t0 + timedelta(days=3))]
    ds = rp.Dataset(recs, rp.Exposure({"avionics": 1}, {"avionics": 1000.0}, {"avionics": 500.0}, 100.0))
    k = rp.compute(ds)["metrics"]["per_kind"][0]
    assert k["unscheduled_removals"] == 2 and k["failures"] == 1 and k["nff_removals"] == 1
    assert k["mtbur_fh"] == 500.0 and k["mtbf_fh"] == 1000.0
    assert k["unit_definition"] and "aggregate" in k["unit_definition"]


def test_false_call_priors_ordered_by_kind_class():
    fc = W.FALSE_CALL
    rate = {k: r * p for k, (r, p) in fc.items()}
    assert max(rate[k] for k in ("brakes", "landing_gear", "tires", "engine")) < min(rate[k] for k in ("hydraulics", "electrical", "ecs", "apu"))
    assert 0.0 < av.PARAMS["nff_base"] <= 0.5 < 1.0   # avionics NFF probability is the highest class (shop draw)


# ---- 5. performance reason ----
def test_engine_performance_shop_visit_reason():
    assert "performance" in REMOVAL_REASONS and "performance" not in _PLANNED
    w = World(3, _tails(2), faults=False)
    w.raise_state("engine", 0, "progress", 0.95)
    w.step()
    recs = [r for r in w.removals() if r.kind == "engine"]
    assert recs and recs[0].removal_reason == "performance" and recs[0].action == "overhaul"
    assert recs[0].unscheduled and not recs[0].nff
    assert not rp.RemovalRec("engine", "T", "engine_1", "overhaul", "performance", True, False, 0.1, START).failure


# ---- 6. infant mortality + maintenance-induced fault ----
@pytest.mark.parametrize("kind", ["avionics", "hydraulics", "electrical"])
def test_infant_hazard_direction_and_bounded_size(kind, monkeypatch):
    def run(infant: dict) -> float:
        monkeypatch.setattr(W, "INFANT", infant)
        w = World(11, _tails(3), faults=False, auto_maintain=False, coupling=False)
        w.run(5)
        w.apply_action(kind, 0, "replace")
        w.run(150)
        return 1.0 - float(w.fleet.state[kind]["hi"][0])
    base = run({})
    with_infant = run({kind: INFANT0[kind]})
    mult = INFANT0[kind][0]
    assert with_infant > base > 0.0
    assert with_infant <= base * mult * 1.0001   # never more than the peak multiplier


def test_infant_decays_to_nothing():
    m, tau = INFANT0["avionics"]
    assert 1.0 + (m - 1.0) * np.exp(-10.0) < 1.0 + 1e-4          # gone after ~10 time constants
    assert all(1.0 < v[0] <= 2.0 and v[1] > 0 for v in INFANT0.values())
    # total extra wear = (M - 1) * tau cycles; keep it well below the part's life (avionics eta 3000 cycles)
    assert (m - 1.0) * tau < 0.05 * 3000.0
    assert set(INFANT0) & {"brakes", "tires"} == set()


def test_maintenance_induced_fault(monkeypatch):
    assert all(0.0 < p <= 0.1 for p in W.MX_INDUCED_P.values()) and 0.0 < W.MX_INDUCED_SEVERITY <= 0.5
    monkeypatch.setattr(W, "MX_INDUCED_P", {"repair": 1.0, "overhaul": 1.0})
    w = World(9, _tails(2), faults=False, auto_maintain=False)
    w.run(3)
    ctx = w._preview_ctx()
    w._do("hydraulics", 0, "repair", "inspection_finding", "x", ctx, induce=True)
    ev = w.events[-1]
    assert ev.get("induced_fault") in {"pump_cavitation", "line_leak"}
    truth = [g for g in w.ground_truth() if g["kind"] == "hydraulics" and g["unit"] == 0]
    assert truth and truth[-1]["mode"] == ev["induced_fault"]
    # explicit operator actions never inject faults
    w._do("hydraulics", 1, "repair", "scheduled", "x", ctx)
    assert "induced_fault" not in w.events[-1]


def test_induced_fault_rate_is_small_by_default():
    w = World(21, _tails(2), faults=False, auto_maintain=False)
    w.run(2)
    hits = 0
    n = 600
    for i in range(n):
        w.fleet.cycle = 10 + i
        hits += w._maybe_induce("ecs", i % 2, "repair") is not None
    assert hits / n < 0.12


# ---- 4. gear servicing keeps leak findings on wing ----
def test_gear_leak_is_serviced_on_wing_not_removed():
    from app.sim.v2.core import Fleet
    from app.sim.v2.models import landing_gear as lg
    f = Fleet(1, {"landing_gear": 4})
    st = f.state["landing_gear"]
    st["leak_kpa"][:] = [10.0, 100.0, 300.0, 100.0]
    f.maintain("landing_gear", np.arange(4), "inspect", 1.0)
    assert st["leak_kpa"][0] == 10.0                                  # below the servicing threshold: untouched
    assert (st["leak_kpa"][1:] == 0.0).all() and (st["n_serviced"][1:] == 1.0).all()
    assert not st["failed"].any() and (st["hi"] > 0.9).all() and lg.PARAMS["SERVICE_LEAK_KPA"] > 10.0


# ---- 1. Weibull on v2 removal records ----
def _synthetic_lives(beta: float, eta: float, n: int, window: float, seed: int = 4):
    rng = random.Random(seed)
    lives = []
    for _ in range(n):
        t = rng.weibullvariate(eta, beta)
        lives.append(("hydraulics", min(t, window), t <= window))   # failed if it died inside the observation window
    return lives


def test_weibull_recovers_beta_from_v2_style_records():
    lives = _synthetic_lives(beta=2.5, eta=2500.0, n=400, window=2190.0)
    out = reliability.fit_lives(lives, {"hydraulics": 400}, rp.UNIT_DEFINITIONS)
    assert len(out) == 1
    row = out[0]
    assert row["kind"] == "hydraulics" and row["failures"] + row["censored"] == 400 and row["censored"] > 0
    assert abs(row["beta"] - 2.5) / 2.5 < 0.15
    assert abs(row["eta_cycles"] - 2500.0) / 2500.0 < 0.1
    assert row["unit_definition"] == rp.UNIT_DEFINITIONS["hydraulics"] and "right-censored MLE" in row["basis"]


def test_pack_weibull_uses_dataset_removals_not_v1_slot_life():
    ds = rp.run_backfill(11, years=0.5, fleet=4)
    assert ds.lives and all(life > 0 for _, life, _ in ds.lives)
    fits = rp.weibull_fits(ds)
    assert all(f["basis"] == reliability.BASIS_V2 for f in fits)
    engine_lives = [life for k, life, _ in ds.lives if k == "engine"]
    assert engine_lives and max(engine_lives) < 4 * 2190   # v2 engine counters from the run, not an 8200-flight slot life


def test_lives_chain_restarts_clock_on_replace_and_overhaul():
    rows = [("avionics", 0, "replace", 100.0, True, False), ("avionics", 0, "nff_return", 150.0, True, True),
            ("avionics", 0, "overhaul", 300.0, False, False), ("avionics", 0, "replace", 450.0, True, False)]
    out = rp._lives(rows, [("avionics", 0, 40.0)])
    assert out == [("avionics", 100.0, True), ("avionics", 300.0, False), ("avionics", 150.0, True), ("avionics", 40.0, False)]
