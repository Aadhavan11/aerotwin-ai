"""Simulator removal-rate bands: 24 tails, one year (2190 cycles), seed 1234, ``auto_maintain=True``.

Construction is the reliability pack's (``build_world({"seed": 1234, "fleet": 24})``, initial_wear=True).
landing_gear is 2-20: a prior chosen by the lead; 18 per ~105k FH (one per ~5.8k FH) is acceptable.
"planned" means ``not removal.unscheduled`` (scheduled, wear_limit, opportunistic, deferred_defect_rectification).

The bands below are industry-plausible PRIORS (typical MTBUR orders of magnitude), NOT calibration against any
operator's data. Removals are ``replace``/``overhaul`` actions (on-wing repairs excluded), as in the reliability pack.
"""
import dataclasses
from collections import Counter

import pytest

from app.sim.v2.runner import build_world

REMOVAL_ACTIONS = ("replace", "overhaul")
BANDS = {
    "hydraulics": (8, 30), "electrical": (6, 25), "landing_gear": (2, 20), "apu": (6, 25), "ecs": (5, 20),
    "avionics": (25, 80), "engine": (0, 4), "brakes": (0, 20), "tires": (0, 60),
}


def _removals(world):
    return [r for r in world.removals() if r.action in REMOVAL_ACTIONS]


@pytest.fixture(scope="module")
def year():
    w = build_world({"seed": 1234, "fleet": 24})
    w.run(2190)
    return _removals(w)


@pytest.mark.parametrize("kind", sorted(BANDS))
def test_unscheduled_removals_in_prior_band(year, kind):
    lo, hi = BANDS[kind]
    n = sum(1 for r in year if r.kind == kind and r.unscheduled)
    assert lo <= n <= hi, f"{kind}: {n} unscheduled removals outside prior band {lo}-{hi}"


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
