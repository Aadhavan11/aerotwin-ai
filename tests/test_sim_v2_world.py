"""Integration tests for the v2 world: faults really change emissions, coupling is live, runs are deterministic."""

from __future__ import annotations

import io
import time

import numpy as np
import pytest

from app.ml.sensors import TELEMETRY_KEYS
from app.sim.profile import profile_for_cycle
from app.sim.v2 import core
from app.sim.v2.core import Fleet
from app.sim.v2.drivers import build_context
from app.sim.v2.faults import LIBRARY
from app.sim.v2.world import World

STATIONS = ["ATL", "DFW", "JFK", "ORD"]


def _tails(n: int) -> list[str]:
    return [f"SIM-{i:03d}" for i in range(n)]


def _ctx(seed: int, tails: list[str], cycle: int, prev=None):
    return build_context([profile_for_cycle(seed, t, cycle) for t in tails], tails, seed, [STATIONS[i % 4] for i in range(len(tails))], prev=prev)


def _twin_run(kind: str, mode: str | None, n_cycles: int = 250, stress: float | None = None):
    """Run one kind with severity 1.0 of ``mode`` forced every cycle (or stress_ext forced); returns (emitted per cycle, hi per cycle)."""
    tails = _tails(4)
    fleet = Fleet(11, {kind: 4})
    prev = None
    emitted: list[dict[str, np.ndarray]] = []
    his: list[np.ndarray] = []
    for c in range(1, n_cycles + 1):
        prev = _ctx(11, tails, c, prev)
        if mode is not None:
            fleet.state[kind][f"fault_{mode}"] = np.ones(4)
        if stress is not None:
            fleet.state[kind]["stress_ext"] = np.full(4, stress)
        fleet.step_cycle(prev)
        emitted.append(fleet.emit_all(prev)[kind])
        his.append(fleet.state[kind]["hi"].copy())
    return emitted, his


# mode -> (emitted channel or "hi", expected sign of (faulted - baseline))
EXPECT = {
    "hpt_blade_erosion": ("egt_margin_c", -1),
    "compressor_fouling": ("hpc_eff_pct", -1),
    "stuck_brake": ("disc_temp_c", +1),
    "rejected_takeoff_overheat": ("disc_temp_c", +1),
    "slow_leak": ("tire_pressure_psi", -1),
    "fod_cut": ("tread_depth_mm", -1),
    "strut_seal_leak": ("strut_pressure_kpa", -1),
    "hard_landing_damage": ("hi", -1),
    "pump_cavitation": ("pump_pressure_kpa", -1),
    "line_leak": ("fluid_qty_pct", -1),
    "cell_short": ("battery_capacity_pct", -1),
    "idg_bearing": ("idg_oil_temp_c", +1),
    "intermittent_connector": ("bite_fault_count", +1),
    "overheating": ("box_temp_c", +1),
    "starter_failure": ("apu_start_time_s", +1),
    "egt_overtemp": ("apu_peak_start_egt_c", +1),
    "valve_stuck": ("pack_flow_kgs", -1),
    "hx_fouling": ("pack_outlet_temp_c", +1),
}


def test_expect_table_covers_library():
    assert set(EXPECT) == set(LIBRARY)


@pytest.mark.parametrize("mode", sorted(LIBRARY))
def test_every_fault_mode_changes_output_in_expected_direction(mode):
    kind = LIBRARY[mode].kind
    if kind not in core.kinds():
        pytest.skip(f"{kind} model not registered")
    chan, sign = EXPECT[mode]
    base_e, base_h = _twin_run(kind, None)
    flt_e, flt_h = _twin_run(kind, mode)
    if chan == "hi":
        a, b = np.mean(base_h[-50:]), np.mean(flt_h[-50:])
    else:
        a = np.mean([e[chan] for e in base_e[-50:]])
        b = np.mean([e[chan] for e in flt_e[-50:]])
    assert sign * (b - a) > 0.0, f"{mode}: {chan} baseline {a} faulted {b}"
    for ch in LIBRARY[mode].detect_channels:  # documented detection channels must be real emitted channels
        assert ch in base_e[0], f"{mode}: detect channel {ch} is not emitted by {kind}"


@pytest.mark.parametrize("kind", [k for k in core.kinds() if not k.startswith("_")])
def test_every_model_consumes_stress_ext(kind):
    _, h1 = _twin_run(kind, None, n_cycles=60, stress=1.0)
    _, h2 = _twin_run(kind, None, n_cycles=60, stress=2.0)
    assert np.mean(h2[-1]) < np.mean(h1[-1])


def test_world_components_and_unit_mapping():
    w = World(3, _tails(3), stochastic=False)
    assert "brakes" in w.kinds and "hydraulics" in w.kinds
    if "engine" in w.kinds:
        assert w.counts["engine"] == 6
        assert w.component_id("engine", 3) == "SIM-001/engine_2"
    assert w.component_id("brakes", 2) == "SIM-002/brakes"


def test_runtime_24_tails_365_cycles():
    w = World(5, _tails(24))
    t = time.perf_counter()
    rows = w.run(365)
    dt = time.perf_counter() - t
    assert dt < 30.0, dt
    assert len(rows) == 365 * sum(w.counts.values())
    assert w.cycle == 365


def test_hash_determinism_and_seed_sensitivity():
    a, b, c = World(9, _tails(6)), World(9, _tails(6)), World(10, _tails(6))
    ra, rb = a.run(80), b.run(80)
    c.run(80)
    assert a.state_hash() == b.state_hash()
    assert ra == rb
    assert a.state_hash() != c.state_hash()


def test_resume_bit_identical_after_maintenance_event():
    tails = _tails(12)
    straight = World(21, tails)
    first = World(21, tails)
    cut = None
    for _ in range(400):
        first.step()
        if first.removals():
            cut = first.cycle
            break
    assert cut is not None, "no removal within 400 cycles"
    first.run(5)
    snap = first.snapshot()
    buf = io.BytesIO()
    np.savez(buf, **snap)  # npz-safe: only arrays, no pickled objects
    buf.seek(0)
    loaded = dict(np.load(buf, allow_pickle=False))
    resumed = World(21, tails)
    resumed.restore(loaded)
    assert resumed.state_hash() == first.state_hash()
    tail_a = first.run(60)
    tail_b = resumed.run(60)
    assert tail_a == tail_b
    straight.run(first.cycle)
    assert straight.state_hash() == first.state_hash() == resumed.state_hash()
    assert [r.as_dict() for r in resumed.removals()] == [r.as_dict() for r in straight.removals()]
    assert resumed.ground_truth() == straight.ground_truth()


def test_coupling_raises_brake_and_gear_wear_when_hydraulics_degraded():
    def run(coupling: bool):
        w = World(4, _tails(8), stochastic=False, coupling=coupling, faults=False)
        w.fleet.state["hydraulics"]["eta_loss"] += 0.4  # degraded hydraulics on every aircraft
        w.run(150)
        brakes = w.fleet.state["brakes"]
        gear = w.fleet.state["landing_gear"]
        return float(brakes["wear_mm"].sum() + 12.0 * len(w.removals())), float((gear["leak_kpa"] + gear["fatigue"]).sum()), w

    wear_on, gear_on, w_on = run(True)
    wear_off, gear_off, _ = run(False)
    assert wear_on > wear_off
    assert gear_on > gear_off
    assert np.all(w_on.fleet.state["brakes"]["stress_ext"] > 1.0)
    rep = w_on.coupling.report()
    assert rep["hydraulics>brakes"]["applied_count"] == 150 and rep["hydraulics>brakes"]["skipped_reason"] is None


def test_all_registered_edges_apply_on_real_models():
    w = World(6, _tails(4), stochastic=False)
    w.run(20)
    for name, r in w.coupling.report().items():
        src = name.split(">")[0]
        if src in w.kinds:
            assert r["applied_count"] == 20, (name, r)
            assert r["skipped_reason"] is None


def test_removal_fractions_in_plausible_bands():
    # Priors, not calibrated: scheduled inspections generate findings (counted as unscheduled by the
    # maintenance engine), a minority of removals are planned, and repeat-BITE avionics removals produce some NFF.
    w = World(7, _tails(24))
    w.run(365)
    rem = [r for r in w.removals() if r.kind != "engine"]  # engine behaviour belongs to the engine model
    assert len(rem) >= 10
    unsched = sum(r.unscheduled for r in rem) / len(rem)
    nff = sum(r.nff for r in rem) / len(rem)
    assert 0.1 <= unsched <= 0.98, unsched   # most consumable/condition removals are planned once gear servicing is on wing
    assert 0.0 <= nff <= 0.5, nff
    assert any(r.nff for r in w.removals())


def test_hi_monotone_between_maintenance_events():
    w = World(8, _tails(6))
    kinds = [k for k in w.kinds if k != "engine"]
    prev = {k: w.fleet.state[k]["hi"].copy() for k in kinds}
    n_ev = 0
    for _ in range(200):
        w.step()
        touched = {(e["kind"], e["unit"]) for e in w.events if e["cycle"] == w.cycle}
        n_ev += len(touched)
        for k in kinds:
            cur = w.fleet.state[k]["hi"]
            for i in range(len(cur)):
                if (k, i) not in touched:
                    assert cur[i] <= prev[k][i] + 1e-12, (k, i, w.cycle, prev[k][i], cur[i])
            prev[k] = cur.copy()
    assert n_ev > 0


def test_telemetry_rows_carry_all_v1_keys():
    w = World(2, _tails(3), stochastic=False)
    rows = w.run(5)
    seen = set()
    for r in rows:
        assert set(r) == {"tail", "component_id", "cycle", "ts", "values"}
        kind = "engine" if "/engine_" in r["component_id"] else r["component_id"].split("/")[1]
        seen.add(kind)
        missing = [k for k in TELEMETRY_KEYS[kind] if k not in r["values"]]
        assert not missing, (kind, missing)
    assert seen >= {"brakes", "tires", "apu", "ecs", "electrical", "hydraulics", "avionics", "landing_gear"}


def test_ground_truth_logged_and_hidden_from_telemetry():
    w = World(12, _tails(12))
    rows = w.run(300)
    truth = w.ground_truth()
    assert truth, "expected at least one injected fault in 12 tails x 300 cycles"
    modes = {t["mode"] for t in truth}
    for t in truth:
        assert t["kind"] in w.kinds and t["provenance"] == "SIMULATED"
    leaked = [k for r in rows for k in r["values"] if k.startswith("fault_") or k in modes or "severity" in k]
    assert not leaked
    assert set(rows[0]) == {"tail", "component_id", "cycle", "ts", "values"}
