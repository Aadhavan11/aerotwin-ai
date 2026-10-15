"""Regression tests for the stress-scenario findings (docs/SCENARIOS.md): early fault injection, strength of the
pump-cavitation and HPT-erosion mechanisms, the on-wing engine repair cap and the cold/heat directional checks.

All data is SIMULATED; the expectations are direction-and-rough-size sanity checks, not validation.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.sim.v2 import scenarios as S
from app.sim.v2.faults import LIBRARY, NO_ONSET, fault_rate
from app.sim.v2.models import engine as eng
from app.sim.v2.world import World

TAILS = S.tails_for(2)


def _world(seed: int = 99, **kw) -> World:
    return World(seed, TAILS, None, stochastic=False, **kw)


# ---------------------------------------------------------------- 1. early injection is not inert
@pytest.mark.parametrize("cycle", [0, 10])
def test_early_injection_is_active_immediately(cycle: int) -> None:
    w = _world()
    w.run(cycle)
    base = _world()
    base.run(cycle)
    w.inject_fault("hydraulics", 0, "pump_cavitation", 1.0)   # ramp 250: used to need >= 250 elapsed cycles
    w.inject_fault("engine", 0, "hpt_blade_erosion", 1.0)     # ramp 1500
    assert w.injector.onset[("hydraulics", "pump_cavitation")][0] != NO_ONSET
    assert w.injector.onset[("hydraulics", "pump_cavitation")][0] < 0 <= cycle   # back-dated before cycle 0
    assert float(w.fleet.state["hydraulics"]["fault_pump_cavitation"][0]) == pytest.approx(1.0)
    assert float(w.fleet.state["engine"]["fault_hpt_blade_erosion"][0]) == pytest.approx(1.0)
    assert float(base.fleet.state["hydraulics"].get("fault_pump_cavitation", np.zeros(2))[0]) == 0.0
    w.run(40)
    base.run(40)
    hy, hb = w.fleet.state["hydraulics"], base.fleet.state["hydraulics"]
    assert hy["eta_loss"][0] > hb["eta_loss"][0] * 3.0           # cavitation drift multiplier is visibly acting
    assert hy["eta_loss"][1] == hb["eta_loss"][1]                # other unit untouched (common random numbers)
    en, eb = w.fleet.state["engine"], base.fleet.state["engine"]
    assert en["progress"][0] > eb["progress"][0] and en["hi"][0] < eb["hi"][0] - 0.5
    assert en["hi"][1] == eb["hi"][1]
    gt = [g for g in w.ground_truth() if g["mode"] in ("pump_cavitation", "hpt_blade_erosion")]
    assert len(gt) == 2 and all(g["onset_cycle"] < 0 for g in gt)


def test_injected_fault_ramps_over_documented_length() -> None:
    w = _world()
    w.run(10)
    w.inject_fault("engine", 0, "hpt_blade_erosion", 0.0)        # linear 1500-cycle ramp starting now
    mode = LIBRARY["hpt_blade_erosion"]

    def sev(k: int) -> float:
        return float(w.injector.severity("engine", mode, 10 + k)[0])

    assert sev(0) == 0.0
    assert sev(mode.ramp_cycles // 2) == pytest.approx(0.5, abs=0.01)
    assert sev(mode.ramp_cycles) == pytest.approx(1.0)
    assert sev(mode.ramp_cycles * 2) == pytest.approx(1.0)
    # a half-severity injection reaches 1 after the remaining half of the ramp
    w2 = _world()
    w2.run(10)
    w2.inject_fault("engine", 0, "hpt_blade_erosion", 0.5)
    m2 = float(w2.injector.severity("engine", mode, 10)[0])
    assert m2 == pytest.approx(0.5, abs=0.01)
    assert float(w2.injector.severity("engine", mode, 10 + mode.ramp_cycles // 2 + 1)[0]) == pytest.approx(1.0, abs=0.01)


def test_negative_onset_survives_snapshot_restore_bit_identically() -> None:
    a = _world()
    a.run(5)
    a.inject_fault("hydraulics", 0, "pump_cavitation", 1.0)
    a.run(30)
    b = _world()
    b.run(5)
    b.inject_fault("hydraulics", 0, "pump_cavitation", 1.0)
    b.run(12)
    snap = b.snapshot()
    c = _world()
    c.restore(snap)
    c.run(18)
    assert c.state_hash() == a.state_hash()


# ---------------------------------------------------------------- 2. pump cavitation strength
def test_cavitation_rate_multiplier_scales_with_severity() -> None:
    sev = {"fault_pump_cavitation": np.array([0.0, 0.3, 1.0])}
    r = fault_rate(sev, "pump_cavitation")
    assert r[0] == 1.0
    assert 1.2 < r[1] < 2.5          # severity 0.3 stays modest
    assert r[2] > 15.0               # severity 1: order-of-magnitude faster efficiency loss


def test_severity_one_cavitation_is_detected_and_removed() -> None:
    w = _world(seed=99)
    w.run(10)
    w.inject_fault("hydraulics", 0, "pump_cavitation", 1.0)
    w.run(700)
    ev = [e for e in w.events if e["kind"] == "hydraulics" and e["unit"] == 0]
    below = [e for e in ev if e["hi_before"] < 0.3 and e["cycle"] <= 600]
    assert below, "hydraulics health never fell below the finding threshold within 600 cycles"
    assert any(e["action"] != "inspect" for e in ev), ev
    first_removal = min(e["cycle"] for e in ev if e["action"] != "inspect")
    assert first_removal <= 1000
    # untouched hydraulics unit on the other tail is not removed in the same window
    assert not any(e["action"] != "inspect" for e in w.events if e["kind"] == "hydraulics" and e["unit"] == 1)


def test_cavitation_raises_case_drain_flow() -> None:
    w = _world(seed=99, auto_maintain=False)
    w.run(10)
    w.inject_fault("hydraulics", 0, "pump_cavitation", 1.0)
    w.run(150)
    last = [r for r in w.rows if r["cycle"] == w.fleet.cycle]
    drain = {r["component_id"]: r["values"]["case_drain_flow_lpm"] for r in last if "case_drain_flow_lpm" in r["values"]}
    d0, d1 = drain[w.component_id("hydraulics", 0)], drain[w.component_id("hydraulics", 1)]
    assert d0 > d1 + 0.3


# ---------------------------------------------------------------- 3. HPT erosion and on-wing repair cap
def test_severity_one_hpt_erosion_leads_to_performance_shop_visit() -> None:
    w = _world(seed=99)
    w.run(10)
    w.inject_fault("engine", 0, "hpt_blade_erosion", 1.0)
    w.run(1500)
    ev = [e for e in w.events if e["kind"] == "engine" and e["unit"] == 0]
    perf = [e for e in ev if e["reason"] == "performance" and e["action"] in ("overhaul", "replace")]
    assert perf, [(e["cycle"], e["action"], e["reason"], round(e["hi_before"], 2)) for e in ev]
    assert 400 <= perf[0]["cycle"] <= 1500
    assert perf[0]["hi_before"] < 0.15
    # an unfaulted engine on the same fleet does not get a shop visit in that window
    assert not [e for e in w.events if e["kind"] == "engine" and e["unit"] == 2 and e["action"] != "inspect"]


def test_engine_repair_health_gain_is_capped() -> None:
    m = eng.EngineModel()
    rng = np.random.default_rng(3)
    cap = 1.0 - eng.PARAMS["repair_keep"]
    assert 0.1 <= cap <= 0.15 + 1e-9
    for q in (0.0, 0.5, 0.8, 1.0):
        st = m.init(6, rng)
        st["progress"][:] = np.array([0.0, 0.2, 0.4, 0.6, 0.8, 0.95])
        st["fault_hpt_peak"][:] = np.array([1.0, 1.0, 0.5, 1.0, 0.2, 0.0])
        st["fault_foul_peak"][:] = np.array([0.0, 1.0, 0.5, 1.0, 1.0, 1.0])
        st["fault_peak"] = eng.PARAMS["fault_progress_hpt"] * st["fault_hpt_peak"] + eng.PARAMS["fault_progress_foul"] * st["fault_foul_peak"]
        st["llp_used"][:] = 0.0
        m._update(st)
        hi0 = st["hi"].copy()
        m.maintain(st, np.arange(6), "repair", q, rng)
        gain = st["hi"] - hi0
        assert (gain >= -1e-12).all()
        assert (gain <= cap + 1e-9).all(), (q, gain)


def test_on_wing_engine_repair_is_wash_only_for_fouling() -> None:
    w = _world(seed=99)
    w.run(10)
    w.inject_fault("engine", 0, "hpt_blade_erosion", 1.0)
    w.inject_fault("engine", 0, "compressor_fouling", 0.5)
    w.run(300)    # first 45-day borescope (cycle ~271) finds the eroded engine and repairs it
    rep = [e for e in w.events if e["kind"] == "engine" and e["unit"] == 0 and e["action"] == "repair"]
    assert rep, "no on-wing repair"
    assert rep[0]["hi_after"] - rep[0]["hi_before"] <= 0.15 + 1e-9
    assert w.injector.active("engine", "hpt_blade_erosion", 0)          # blade erosion persists (no shop visit yet)
    assert not w.injector.active("engine", "compressor_fouling", 0)     # a wash clears fouling


# ---------------------------------------------------------------- 4/5. cold and heat directional checks (multi-seed)
SEEDS = (1, 7, 99)


@pytest.fixture(scope="module")
def climate() -> dict[str, list[S.Metrics]]:
    out: dict[str, list[S.Metrics]] = {"base": [], "wet_winter": [], "heat_plus15": []}
    for seed in SEEDS:
        for sid in out:
            out[sid].append(S.run_scenario(seed, 4, None if sid == "base" else S.BY_ID[sid]))
    return out


def _tot(ms: list[S.Metrics], kinds: tuple[str, ...], field: str = "removals_by_kind") -> int:
    return sum(sum(getattr(m, field).get(k, 0) for k in kinds) for m in ms)


def _wear_rates(sid: str | None, seed: int, n: int = 300) -> tuple[float, float]:
    """Mean brake wear (mm / cycle) and tire tread loss (mm / cycle) with maintenance off: the wear mechanism alone."""
    setup = S.Setup()
    if sid:
        S.BY_ID[sid].perturb(setup)
    w = S.ScenarioWorld(seed, S.tails_for(4), setup)
    w.auto_maintain = False
    w.run(n)
    return (float(w.fleet.state["brakes"]["wear_mm"].mean()) / n, float(w.fleet.state["tires"]["tread_loss_mm"].mean()) / n)


@pytest.mark.parametrize("seed", SEEDS)
def test_wet_winter_raises_brake_and_tire_wear_rate(seed: int) -> None:
    """Wet runway (more energy, wet factor) outweighs the halved carbon-oxidation term at -10 C: brake wear per cycle
    rises ~15-20% and tread loss a few percent for every seed. Tread rises less because only the landing term takes the
    wet factor; cold does not add under-inflation wear (inflation is a COLD pressure, see docs/SCENARIOS.md)."""
    b0, t0 = _wear_rates(None, seed)
    b1, t1 = _wear_rates("wet_winter", seed)
    assert b1 > b0 * 1.05
    assert t1 > t0


def test_wet_winter_does_not_lower_real_brake_tire_removals(climate) -> None:
    """Counting every real removal (wear limit + findings + failures) over 3 seeds the total is not lower than baseline.
    The unscheduled-only count can fall: cold also lowers tire permeation leaks and heat-driven faults."""
    base, ws = climate["base"], climate["wet_winter"]
    tb, ts = _tot(base, ("brakes", "tires")), _tot(ws, ("brakes", "tires"))
    assert ts >= tb - 2.0 * tb ** 0.5
    assert ts >= tb


def test_heat_does_not_reduce_hot_sensitive_removals(climate) -> None:
    base, ht = climate["base"], climate["heat_plus15"]
    kinds = ("avionics", "ecs", "electrical", "apu")
    hb, hh = _tot(base, kinds), _tot(ht, kinds)
    assert hh >= hb - 2.0 * hb ** 0.5          # not lower than baseline by more than 2 Poisson sigma
    # electrical (battery calendar/cycle fade through the Arrhenius factor): summed over seeds, not lower
    assert _tot(ht, ("electrical",)) >= _tot(base, ("electrical",))
