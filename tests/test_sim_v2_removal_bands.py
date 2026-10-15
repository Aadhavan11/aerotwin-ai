"""Simulator removal-rate bands over five seeds: 24 tails, one year (2190 cycles), ``auto_maintain=True``.

Construction is the reliability pack's (``build_world({"seed": s, "fleet": 24})``, initial_wear=True).
Counts are *unscheduled* removals (``replace`` / ``overhaul`` / ``nff_return`` with ``removal.unscheduled``: failure,
inspection_finding, nff, performance); "planned" means ``not removal.unscheduled`` (scheduled, wear_limit, opportunistic,
deferred_defect_rectification).

The bands are ASSUMED priors (typical MTBUR orders of magnitude, no citation), NOT calibration against any operator's data,
and were not tuned on a single seed: for every kind the MEDIAN over SEEDS must lie inside the band and at least 4 of the 5
seeds must. Per-seed numbers: docs/RELIABILITY.md.
"""
import dataclasses
import statistics
from collections import Counter

import pytest

from app.sim.v2.runner import build_world

REMOVAL_ACTIONS = ("replace", "overhaul", "nff_return")
SEEDS = (1, 7, 99, 1234, 4242)
TAILS = 24
SCALE = 24 / TAILS
BANDS = {   # unscheduled removals per 24 tails per year
    "hydraulics": (5, 15), "electrical": (5, 15), "landing_gear": (1, 5), "apu": (6, 20), "ecs": (4, 15),
    "avionics": (25, 80), "engine": (0, 3), "brakes": (0, 20), "tires": (0, 60),
}
MODERATE_NFF = ("hydraulics", "electrical", "ecs", "apu")
MECHANICAL_NFF = ("brakes", "landing_gear", "tires", "engine")


def _removals(world):
    return [r for r in world.removals() if r.action in REMOVAL_ACTIONS]


@pytest.fixture(scope="module")
def runs():
    out = {}
    for s in SEEDS:
        w = build_world({"seed": s, "fleet": TAILS})
        w.run(2190)
        out[s] = _removals(w)
    return out


@pytest.fixture(scope="module")
def year(runs):
    return runs[1234]


@pytest.mark.parametrize("kind", sorted(BANDS))
def test_unscheduled_removals_median_in_prior_band(runs, kind):
    lo, hi = BANDS[kind]
    per_seed = [SCALE * sum(1 for r in runs[s] if r.kind == kind and r.unscheduled) for s in SEEDS]
    med = statistics.median(per_seed)
    inside = sum(1 for n in per_seed if lo <= n <= hi)
    assert lo <= med <= hi, f"{kind}: median {med} (per seed {per_seed}) outside prior band {lo}-{hi}"
    assert inside >= 4, f"{kind}: only {inside}/5 seeds inside band {lo}-{hi}: {per_seed}"


def test_nff_fraction_bands(runs):
    """NFF share of unscheduled removals: avionics high, hydraulics/electrical/ecs/apu moderate, mechanical low."""
    def share(kinds):
        n = u = 0
        for rs in runs.values():
            for r in rs:
                if r.kind in kinds and r.unscheduled:
                    u += 1
                    n += bool(r.nff)
        return n / u if u else 0.0, u
    av, _ = share(("avionics",))
    mod, _ = share(MODERATE_NFF)
    mech, _ = share(MECHANICAL_NFF)
    assert 0.2 <= av <= 0.65, av
    assert 0.03 <= mod <= 0.35, mod
    assert mech <= 0.12, mech
    assert mech < mod < av


def test_nff_returns_unit_unchanged_and_not_a_failure(runs):
    rs = [r for rs_ in runs.values() for r in rs_ if r.nff]
    assert rs
    for r in rs:
        assert r.action == "nff_return" and r.removal_reason == "nff" and r.unscheduled and r.new_serial is None
        assert r.health_before == r.health_after


@pytest.mark.parametrize("kind", ["brakes", "tires"])
def test_wear_kinds_mostly_planned(year, kind):
    rs = [r for r in year if r.kind == kind]
    assert rs
    share = sum(1 for r in rs if not r.unscheduled) / len(rs)
    assert share > 0.7


@pytest.mark.parametrize("kind", ["brakes", "tires"])
def test_wear_limit_removals_exist(year, kind):
    assert any(r.kind == kind and r.removal_reason == "wear_limit" for r in year)


def test_scheduled_plus_unscheduled_equals_total(year):
    uns = Counter(r.kind for r in year if r.unscheduled)
    sch = Counter(r.kind for r in year if not r.unscheduled)
    tot = Counter(r.kind for r in year)
    for k in tot:
        assert uns[k] + sch[k] == tot[k]
    assert sum(uns.values()) + sum(sch.values()) == len(year)


def test_removals_deterministic():
    def run():
        w = build_world({"seed": 77, "fleet": 6})
        w.run(400)
        return [dataclasses.asdict(r) for r in w.removals()]
    a, b = run(), run()
    assert a == b
