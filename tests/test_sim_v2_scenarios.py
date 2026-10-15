"""Stress / edge scenario suite on the v2 World: expectations (direction + rough size), not validation.

Default run: a scaled fleet (4 tails, 1 year, one seed, shared module fixture). The full 24-tail variants are marked
``slow`` and run only with ``RUN_SLOW=1`` (about 40 s each).
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from app.sim.v2 import drivers, scenarios as S
from app.sim.v2.drivers import LEGS_PER_DAY
from app.sim.v2.world import World

SEED = 1234
N_TAILS = 4
slow = pytest.mark.skipif(not os.environ.get("RUN_SLOW"), reason="full 24-tail variant; set RUN_SLOW=1")


@pytest.fixture(scope="module")
def suite() -> tuple[S.Metrics, dict[str, S.Outcome]]:
    base, outs = S.run_suite(SEED, N_TAILS)
    return base, {o.scenario.id: o for o in outs}


IDS = [s.id for s in S.SCENARIOS]


def test_scenario_registry_complete() -> None:
    assert len(IDS) == 9 and len(set(IDS)) == 9
    assert set(IDS) == {"heat_plus15", "ageing_plus5y", "utilisation_x1.4", "hot_dusty", "part_shortage", "double_engine_fault",
                        "hydraulic_failure", "wet_winter", "combined_worst"}


def test_baseline_sane(suite) -> None:
    base, _ = suite
    assert base.invariants_ok, base.violations
    assert base.cycles == int(round(365 * LEGS_PER_DAY)) == 2190
    assert base.flight_hours > 0 and base.unsched_total > 0
    assert 0.0 <= base.nff_share < 1.0


@pytest.mark.parametrize("sid", IDS)
def test_no_crash_no_nan_invariants(suite, sid: str) -> None:
    _, outs = suite
    m = outs[sid].metrics
    assert m.nan_free and m.invariants_ok, m.violations
    assert 0.0 <= m.hi_min <= m.hi_p5 <= 1.0
    assert np.isfinite(m.hi_p5) and m.flight_hours > 0
    assert sum(m.nff_by_kind.values()) + sum(m.unsched_by_kind.values()) == m.unsched_total


@pytest.mark.parametrize("sid", IDS)
def test_expected_direction(suite, sid: str) -> None:
    _, outs = suite
    assert outs[sid].failures == [], outs[sid].failures


def test_utilisation_cycle_accounting(suite) -> None:
    base, outs = suite
    m = outs["utilisation_x1.4"].metrics
    assert m.cycles == int(round(365 * LEGS_PER_DAY * 1.4)) == 3066
    # same calendar year, 1.4x the legs: flight hours scale with cycles (per-cycle physics untouched)
    assert m.flight_hours / base.flight_hours == pytest.approx(1.4, rel=0.02)


def test_hydraulic_fault_couples_into_brakes_and_gear(suite) -> None:
    base, outs = suite
    m = outs["hydraulic_failure"].metrics
    for k in ("brakes", "landing_gear"):
        assert m.stress_peak[k] >= base.stress_peak[k]
        assert m.stress_target_mean[k] > base.stress_target_mean[k]


def test_double_engine_fault_is_two_engine_events(suite) -> None:
    _, outs = suite
    m = outs["double_engine_fault"].metrics
    assert m.engine_faults_target >= 2 and m.engine_events_target >= 2


def test_shortage_exposes_without_losing_units(suite) -> None:
    base, outs = suite
    m = outs["part_shortage"].metrics
    assert m.deferred_replacements > 0 and m.deferral_exposure_cycles > 0
    assert m.deferrals > base.deferrals
    assert m.failed_in_service >= base.failed_in_service


def test_scenario_world_without_perturbation_matches_plain_world() -> None:
    """Common random numbers: the harness itself adds no randomness or state change."""
    plain = World(SEED, S.tails_for(2), None, stochastic=False)
    plain.run(120)
    w, _ = S.run_world(SEED, 2, S.Setup(), days=120 / LEGS_PER_DAY)
    assert w.state_hash() == plain.state_hash()


def test_env_patch_restores_station_table() -> None:
    before = dict(drivers.STATION_ENV)
    s = S.Setup()
    S.BY_ID["hot_dusty"].perturb(s)
    S.run_world(SEED, 2, s, days=2.0)
    assert drivers.STATION_ENV == before


def test_wet_share_and_oat_transforms_applied() -> None:
    s = S.Setup()
    S.BY_ID["wet_winter"].perturb(s)
    w = S.ScenarioWorld(SEED, S.tails_for(2), s)
    wets = 0
    for c in range(1, 201):
        p, _ = w._profile("SIM-001", c)
        assert p.oat_arr_c == -10.0
        wets += int(p.wet_runway)
    assert 0.45 < wets / 200 < 0.75


@slow
@pytest.mark.slow
@pytest.mark.parametrize("sid", ["heat_plus15", "ageing_plus5y", "utilisation_x1.4", "double_engine_fault", "hydraulic_failure"])
def test_full_fleet_expectations(sid: str) -> None:
    base = S.run_scenario(SEED, S.FULL_TAILS, None)
    out = S.run_suite(SEED, S.FULL_TAILS, [S.BY_ID[sid]], base=base)[1][0]
    assert out.metrics.invariants_ok, out.metrics.violations
    assert out.failures == [], out.failures
