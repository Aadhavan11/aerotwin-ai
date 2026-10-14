"""Calendar consistency (1 year of cycles == 365 days) and wear_limit removals are planned, not unscheduled."""
from app.sim.v2.world import World
from app.sim.v2.drivers import LEGS_PER_DAY
from app.sim.v2.maintenance import _PLANNED, REMOVAL_REASONS


def test_year_of_cycles_is_365_days():
    w = World(1234, ["SIM-001", "SIM-002", "SIM-003"], stochastic=True, auto_maintain=False)
    n = int(round(365 * LEGS_PER_DAY))
    w.run(n)
    days = w._ctx.calendar_days
    assert abs(float(days.max()) - 365.0) < 1e-6
    assert abs(float(days.min()) - 365.0) < 1e-6


def test_wear_limit_is_planned_reason():
    assert "wear_limit" in REMOVAL_REASONS and "wear_limit" in _PLANNED
