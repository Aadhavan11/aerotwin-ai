"""Seeded invariants for small v2 fleets, including live and resumed worlds."""

from __future__ import annotations

from collections import Counter
from dataclasses import fields, replace
from datetime import datetime, timedelta
from typing import Any

import numpy as np
import pytest

from app.ml.sensors import TELEMETRY_KEYS, kind_from_component_id
from app.sim.clock import START
from app.sim.fleet import generate_fleet
from app.sim.profile import brake_energy_mj, profile_for_cycle
from app.sim.v2 import core, maintenance
from app.sim.v2.core import Fleet
from app.sim.v2.drivers import LEGS_PER_DAY, OperatingContext, build_context
from app.sim.v2.faults import LIBRARY, GroundTruthFault
from app.sim.v2.live import V2Source
from app.sim.v2.maintenance import REMOVAL_REASONS
from app.sim.v2.models.landing_gear import PARAMS as GEAR_PARAMS
from app.sim.v2.world import World

SEEDS = [1, 7, 99]
KINDS = tuple(k for k in core.kinds() if not k.startswith("_"))
# World._rows documents these outer bookkeeping keys; they are not sensor channels.
ROW_KEYS = {"tail", "component_id", "cycle", "ts", "values"}
TRUTH_KEYS = {f.name for f in fields(GroundTruthFault)}


@pytest.fixture(params=SEEDS)
def seed(request: pytest.FixtureRequest) -> int:
    return int(request.param)


@pytest.fixture(params=[2, 6])
def fleet_size(request: pytest.FixtureRequest) -> int:
    return int(request.param)


def _tails(n: int) -> list[str]:
    return [f"SIM-{i + 1:03d}" for i in range(n)]


def _sample_context(seed: int, cycle: int, rng: np.random.Generator, prev: OperatingContext | None = None) -> OperatingContext:
    tails = _tails(6)
    profiles = []
    # Alternate nominal/cold and extreme hot/wet operation, with high utilisation.
    for tail in tails:
        p = profile_for_cycle(seed, tail, cycle)
        hot = cycle % 2 == 0
        oat = float(rng.uniform(40.0, 55.0) if hot else rng.uniform(-25.0, 30.0))
        wet = hot or bool(rng.integers(2))
        runway = float(rng.uniform(4500.0, 12000.0))
        weight = float(rng.uniform(0.6, 0.98))
        elevation = float(rng.uniform(0.0, 8000.0))
        profiles.append(replace(p, oat_arr_c=oat, wet_runway=wet, runway_len_ft=runway,
                                landing_weight_frac=weight, payload_frac=float(rng.uniform(0.55, 0.98)),
                                takeoff_derate=float(rng.uniform(0.0, 0.15)), sink_rate_fps=float(rng.uniform(2.0, 6.0)),
                                field_elev_arr_ft=elevation, density_alt_arr_ft=elevation + 120.0 * (oat - (15.0 - 1.98 * elevation / 1000.0)),
                                brake_energy_mj=brake_energy_mj(weight, runway, wet)))
    return build_context(profiles, tails, seed, ["MIA", "DEN", "DFW", "SFO", "ATL", "JFK"], utilisation=2.5, prev=prev)


def _assert_health(health: dict[str, np.ndarray], cycle: int) -> None:
    for kind, hi in health.items():
        assert np.isfinite(hi).all(), (kind, cycle, hi)
        assert ((0.0 <= hi) & (hi <= 1.0)).all(), (kind, cycle, hi)


def _assert_channels(kind: str, values: dict[str, Any], fleet: Fleet, complete: bool = True) -> None:
    declared = set(fleet.models[kind].channels)
    if complete:
        assert set(TELEMETRY_KEYS[kind]) <= set(values), (kind, set(TELEMETRY_KEYS[kind]) - set(values))
        assert set(values) == declared, (kind, set(values) ^ declared)
    else:
        # app.sim.sensor_faults.apply explicitly omits channels during dropout.
        assert set(values) <= declared, (kind, set(values) - declared)
    assert not (set(values) & (TRUTH_KEYS | set(LIBRARY))), (kind, values.keys())
    assert not any(key.startswith("fault_") for key in values), (kind, values.keys())
    for channel, value in values.items():
        assert np.isfinite(value).all(), (kind, channel, value)


def test_model_steps_are_finite_and_health_never_recovers(seed: int) -> None:
    assert set(KINDS) == set(TELEMETRY_KEYS)  # Do not silently omit a missing production model (especially engine).
    fleet = Fleet(seed, dict.fromkeys(KINDS, 6))
    rng = np.random.default_rng(seed)
    ctx = None
    previous = {k: hi.copy() for k, hi in fleet.health_index().items()}
    for cycle in range(1, 121):
        ctx = _sample_context(seed, cycle, rng, ctx)
        fleet.step_cycle(ctx)  # No maintenance between any checkpoints, including for engine.
        current = fleet.health_index()
        _assert_health(current, cycle)
        for kind, hi in current.items():
            assert (hi <= previous[kind] + 1e-12).all(), (kind, cycle, previous[kind], hi)
        previous = {k: hi.copy() for k, hi in current.items()}
        for kind, values in fleet.emit_all(ctx).items():
            _assert_channels(kind, values, fleet)


@pytest.fixture
def aged_fleet(seed: int) -> Fleet:
    fleet = Fleet(seed, dict.fromkeys(KINDS, 6))
    rng = np.random.default_rng(seed)
    ctx = None
    for cycle in range(1, 37):
        ctx = _sample_context(seed, cycle, rng, ctx)
        fleet.step_cycle(ctx)
    return fleet


def test_replace_resets_age_and_new_unit_health(aged_fleet: Fleet) -> None:
    for kind in KINDS:
        aged_fleet.maintain(kind, np.arange(6), "replace")
        state = aged_fleet.state[kind]
        for key in ("age_cycles", "age_hours", "age_virtual"):
            np.testing.assert_array_equal(state[key], np.zeros(6), err_msg=f"{kind}/{key}")
        expected = np.ones(6)
        if kind == "landing_gear":
            # LandingGearModel.maintain('replace') uses _hi with the sampled initial crack, rather than init's cached hi=1.
            expected = np.clip(1.0 - state["crack_a0"] / GEAR_PARAMS["CRACK_A_CRIT_MM"], 0.0, 1.0)
        np.testing.assert_allclose(aged_fleet.health_index()[kind], expected, rtol=0.0, atol=1e-12, err_msg=kind)


@pytest.mark.parametrize("q", [0.0, 0.25, 0.6, 1.0])
def test_repair_retains_at_least_q_times_age(aged_fleet: Fleet, q: float) -> None:
    for kind in KINDS:
        before = {key: aged_fleet.state[kind][key].copy() for key in ("age_cycles", "age_hours", "age_virtual")}
        aged_fleet.maintain(kind, np.arange(6), "repair", q=q)
        for key, age in before.items():
            after = aged_fleet.state[kind][key]
            assert (after + 1e-12 >= q * age).all(), (kind, key, q, age, after)


def test_repair_health_and_age_semantics(aged_fleet: Fleet) -> None:
    """`q` is the residual-damage fraction: repair(q=0) restores health fully, so health may rise.

    Invariants: health stays finite in [0, 1], never drops, and age_after >= q * age_before.
    """
    snap = aged_fleet.snapshot()
    for q in (0.0, 0.25, 0.6, 1.0):
        aged_fleet.restore(snap)
        for kind in KINDS:
            before = aged_fleet.health_index()[kind].copy()
            age_before = aged_fleet.state[kind]["age_virtual"].copy()
            aged_fleet.maintain(kind, np.arange(6), "repair", q=q)
            after = aged_fleet.health_index()[kind]
            assert np.isfinite(after).all() and ((0.0 <= after) & (after <= 1.0)).all(), (kind, q, after)
            assert (after >= before - 1e-12).all(), (kind, q, before, after)
            assert (aged_fleet.state[kind]["age_virtual"] + 1e-12 >= q * age_before).all(), (kind, q)


def test_world_cycle_invariants(seed: int, fleet_size: int) -> None:
    world = World(seed, _tails(fleet_size), stochastic=seed != 7)
    previous = {k: hi.copy() for k, hi in world.health_index().items()}
    initial_serials = dict(world.registry.installed)
    hours = dict.fromkeys(initial_serials, 0.0)
    for cycle in range(1, 181):
        counters = {key: (p.tso_h, p.cso) for key, serial in world.registry.installed.items() for p in [world.registry.parts[serial]]}
        event_start = len(world.events)
        rows = world.step()
        _assert_health(world.health_index(), cycle)
        events = world.events[event_start:]
        touched = {(e["kind"], e["unit"]) for e in events}
        resets = {(e["kind"], e["unit"]) for e in events if e["action"] in ("overhaul", "replace")}
        current = world.health_index()
        for kind, hi in current.items():
            for unit, value in enumerate(hi):
                if (kind, unit) not in touched:
                    assert value <= previous[kind][unit] + 1e-12, (kind, unit, cycle, previous[kind][unit], value)
            stress = world.fleet.state[kind]["stress_ext"]
            assert np.isfinite(stress).all() and ((1.0 <= stress) & (stress <= world.coupling.max_total)).all(), (kind, cycle, stress)
        previous = {k: hi.copy() for k, hi in current.items()}
        accruals = {tail: sum(minutes for _, minutes in profile_for_cycle(seed, tail, cycle).phases) / 60.0 for tail in world.tails}
        for key, initial in initial_serials.items():
            kind, unit = key
            tail = world.tails[int(world.unit_tail[kind][unit])]
            hours[key] += accruals[tail]
            part = world.registry.on_unit(kind, unit)
            assert part is not None
            if part.serial == initial:
                assert part.tsn_h == pytest.approx(hours[key]), (key, cycle)
                assert part.csn == cycle, (key, cycle)
            if key not in resets:
                tso, cso = counters[key]
                assert part.tso_h == pytest.approx(tso + accruals[tail]), (key, cycle)
                assert part.cso == cso + 1.0, (key, cycle)
        serials = list(world.registry.installed.values())
        assert len(serials) == len(set(serials)) == sum(world.counts.values())
        for row in rows:
            assert set(row) == ROW_KEYS
            assert row["cycle"] == cycle
            assert datetime.fromisoformat(row["ts"]) == START + timedelta(days=cycle / LEGS_PER_DAY)
            _assert_channels(kind_from_component_id(row["component_id"]), row["values"], world.fleet, complete=not world.stochastic)
    np.testing.assert_allclose(world.snapshot()["ctx/calendar_days"], world.cycle / LEGS_PER_DAY, rtol=0.0, atol=1e-11)
    removals = world.removals()
    for rec in removals:
        assert rec.removal_reason in REMOVAL_REASONS
        assert rec.unscheduled == (rec.removal_reason not in maintenance._PLANNED)
        assert not rec.nff or rec.unscheduled, rec
    timestamps = [datetime.fromisoformat(rec.time) for rec in removals]
    assert timestamps == sorted(timestamps)


def test_removal_install_conservation(seed: int, fleet_size: int) -> None:
    """Records are written for every action; only 'replace' installs a new serial."""
    world = World(seed, _tails(fleet_size), faults=False, auto_maintain=False)
    world.run(4)
    for kind in world.kinds:
        for action in ("repair", "overhaul", "replace"):
            world.apply_action(kind, 0, action)
    installs = Counter(p.kind for p in world.registry.parts.values())
    replaces = Counter(r.kind for r in world.registry.removals if r.action == "replace")
    for kind, initial_installs in world.counts.items():
        assert replaces[kind] == installs[kind] - initial_installs, (kind, replaces[kind], installs[kind])
    for rec in world.registry.removals:
        assert rec.action in maintenance.ACTIONS and rec.removal_reason in REMOVAL_REASONS, rec
        if rec.action == "replace":
            assert rec.new_serial is not None and rec.new_serial != rec.serial, rec
        else:
            assert rec.new_serial is None, rec
            assert world.registry.parts[rec.serial].serial == rec.serial
        if rec.action == "overhaul":
            assert (rec.tso_h, rec.cso) == (0.0, 0.0), rec


@pytest.mark.parametrize("reason", REMOVAL_REASONS)
def test_removal_reason_flags(seed: int, fleet_size: int, reason: str) -> None:
    world = World(seed, _tails(fleet_size), faults=False, auto_maintain=False)
    world.step()
    for kind in world.kinds:
        world.apply_action(kind, 0, "replace", reason=reason)
    assert len(world.removals()) == len(world.kinds)
    for rec in world.removals():
        assert rec.removal_reason == reason
        assert rec.unscheduled == (reason not in maintenance._PLANNED)
        assert not rec.nff or rec.unscheduled, rec
        if reason == "nff":
            assert rec.nff and rec.unscheduled, rec
    times = [datetime.fromisoformat(rec.time) for rec in world.removals()]
    assert times == sorted(times)
    serials = list(world.registry.installed.values())
    assert len(serials) == len(set(serials))


def test_explicit_actions_preserve_total_counters(seed: int, fleet_size: int) -> None:
    world = World(seed, _tails(fleet_size), faults=False, auto_maintain=False)
    for action in ("inspect", "repair", "overhaul", "replace"):
        world.run(3)
        for kind in world.kinds:
            before = world.registry.on_unit(kind, 0)
            assert before is not None
            serial, tsn, csn, tso, cso = before.serial, before.tsn_h, before.csn, before.tso_h, before.cso
            world.apply_action(kind, 0, action)
            after = world.registry.on_unit(kind, 0)
            assert after is not None
            if action == "replace":
                assert after.serial != serial
                assert (after.tsn_h, after.csn, after.tso_h, after.cso) == (0.0, 0.0, 0.0, 0.0)
                assert (before.tsn_h, before.csn) == (tsn, csn)
            else:
                assert (after.serial, after.tsn_h, after.csn) == (serial, tsn, csn)
                assert (after.tso_h, after.cso) == ((0.0, 0.0) if action == "overhaul" else (tso, cso))


def test_seed_and_snapshot_determinism(seed: int, fleet_size: int) -> None:
    tails = _tails(fleet_size)
    straight, first = World(seed, tails), World(seed, tails)
    assert straight.state_hash() == first.state_hash()
    expected = straight.run(90)
    prefix = first.run(43)
    snap = first.snapshot()
    resumed = World(seed, tails)
    resumed.restore(snap)
    assert first.state_hash() == resumed.state_hash()
    np.testing.assert_allclose(snap["ctx/calendar_days"], 43 / LEGS_PER_DAY, rtol=0.0, atol=1e-12)
    suffix = resumed.run(47)
    assert prefix + suffix == expected
    first.run(47)
    assert straight.state_hash() == first.state_hash() == resumed.state_hash()
    np.testing.assert_allclose(resumed.snapshot()["ctx/calendar_days"], 90 / LEGS_PER_DAY, rtol=0.0, atol=1e-12)


def test_fault_truth_never_leaks(seed: int, fleet_size: int) -> None:
    world = World(seed, _tails(fleet_size), stochastic=False, auto_maintain=False)
    # Onset at a positive cycle ensures each forced ramp is active before rows are checked.
    world.run(2)
    for mode_id, mode in LIBRARY.items():
        world.inject_fault(mode.kind, 0, mode_id, severity=float(mode.severity(1)))
    truth = world.ground_truth()
    assert {record["mode"] for record in truth} == set(LIBRARY)
    assert all(set(record) == TRUTH_KEYS for record in truth)
    for row in world.run(12):
        assert set(row) == ROW_KEYS
        _assert_channels(kind_from_component_id(row["component_id"]), row["values"], world.fleet)


@pytest.fixture
def live_source(seed: int, fleet_size: int) -> V2Source:
    state = generate_fleet(seed)
    tails = set(_tails(fleet_size))
    # Keep real FleetState contracts, with small nonzero initial wear so age_world is exercised cheaply by bind.
    state = state.model_copy(update={"aircraft": [a for a in state.aircraft if a.tail in tails],
                                     "components": [c.model_copy(update={"health": 0.99, "cycles": 3}) for c in state.components if c.tail in tails],
                                     "legs": [leg for leg in state.legs if leg.tail in tails]})
    source = V2Source(seed)
    source.bind(state)
    assert source.world is not None
    assert any(np.any(hi < 1.0) for hi in source.world.health_index().values())
    return source


def test_live_remaining_life_is_nonnegative(live_source: V2Source) -> None:
    world = live_source.world
    assert world is not None
    for kind in world.kinds:
        for unit in range(world.counts[kind]):
            cid = world.component_id(kind, unit)
            if kind == "engine":
                # Cross the LLP limit in a short preview instead of simulating 25,000 flights.
                world.raise_state(kind, unit, "llp_used", float(world.fleet.state[kind]["llp_limit"][unit]) - 0.5)
            rows = live_source.advance(world.tails[int(world.unit_tail[kind][unit])], cid, 8)
            assert len(rows) == 8
            for row in rows:
                assert set(row.values) == set(TELEMETRY_KEYS[kind])
                assert all(np.isfinite(value) for value in row.values.values())
            for values in world.preview(kind, unit, 8):
                if kind == "engine":
                    assert values["llp_cycles_remaining"] >= 0.0, values
    _assert_health(world.health_index(), world.cycle)


@pytest.mark.xfail(strict=False, reason="GAP: v2 sim does not enforce LLP limits; enforcement lives in "
                   "app/ops/llp.py (flag-gated solver link)")
def test_live_registry_llp_remaining_is_nonnegative(live_source: V2Source) -> None:
    world = live_source.world
    assert world is not None
    part = world.registry.on_unit("engine", 0)
    assert part is not None
    part.llp_limit_cycles = 3.0
    tail = world.tails[0]
    leg = next(leg for leg in generate_fleet(world.seed).legs if leg.tail == tail)
    ids = [world.component_id(kind, unit) for kind in world.kinds for unit in range(world.counts[kind])
           if world.tails[int(world.unit_tail[kind][unit])] == tail]
    for _ in range(4):
        live_source.advance_leg(leg, ids)
    assert part.llp_remaining is not None
    assert part.llp_remaining >= 0.0, (part.llp_limit_cycles, part.csn, part.llp_remaining)


def test_llp_remaining_accessor_and_ops_expiry() -> None:
    from app.ops import llp
    from app.sim.v2.maintenance import InstallRegistry

    reg = InstallRegistry()
    part = reg.install("engine", 0, llp_limit_cycles=100.0)
    part.csn = 130.0
    assert part.llp_remaining == pytest.approx(-30.0)
    part.csn = 40.0
    assert part.llp_remaining == pytest.approx(60.0)
    assert llp.status_for(part.llp_limit_cycles - 130.0) == "expired"
    assert llp.status_for(0.0) == "expired"
    assert llp.status_for(1e6) == "ok"
