"""Decision-theoretic replacement timing: closed-form sanity, monotonicity, hard floors, VOI, closed loop, flag-off identity."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest
from scipy import integrate, optimize

from app import settings
from app.contracts.models import RulEstimate
from app.decision.replacement import (
    CostModel, InspectionSpec, LifePosterior, Limits, Opportunity, expected_cost_table, fit_posterior,
    optimal_rate, recommend, value_of_inspection,
)
from app.solver import derive_tasks
from app.solver.golden import golden_state

SNAPSHOT = Path(__file__).parent / "snapshots" / "golden_tasks.json"
CM = CostModel(planned_cost_usd=20_000.0)
GRID = [float(t) for t in range(10, 201, 10)]


def _weibull(k: float, eta: float) -> LifePosterior:
    return LifePosterior("weibull", k, eta)


def test_fit_posterior_matches_quantiles():
    ln = fit_posterior(40, 80, 140, cap=500)
    assert ln.ppf(0.5) == pytest.approx(80, rel=1e-6)
    assert ln.ppf(0.9) / ln.ppf(0.1) == pytest.approx(140 / 40, rel=1e-6)   # the p10..p90 spread is matched
    wb = fit_posterior(40, 80, 140, cap=500, family="weibull")
    assert wb.ppf(0.1) == pytest.approx(40, rel=1e-6) and wb.ppf(0.9) == pytest.approx(140, rel=1e-6)


def test_censored_estimates_are_lower_bounds_with_wide_prior():
    p = fit_posterior(100, 125, 125, cap=125)
    assert p.censored == "all" and p.b >= 0.5
    assert p.ppf(0.1) == pytest.approx(125, rel=1e-6)          # cap is a lower bound, not the median
    q = fit_posterior(50, 90, 125, cap=125)
    assert q.censored == "upper" and q.note


def test_mean_min_matches_numeric_integral():
    for post in (fit_posterior(40, 80, 140, cap=500), _weibull(2.5, 100)):
        want = integrate.quad(lambda u: 1 - float(post.cdf(u)), 0, 90)[0]
        assert float(post.mean_min(90)) == pytest.approx(want, rel=1e-5)


def test_exponential_age_replacement_is_never_beneficial():
    post = _weibull(1.0, 100.0)
    rows = expected_cost_table(post, CM, GRID, Limits(max_risk_alpha=1.0))
    rates = [r.rate_per_cycle for r in rows]
    assert all(a > b for a, b in zip(rates, rates[1:]))        # later is always cheaper per cycle
    assert optimal_rate(post, CM)[1] == math.inf
    rec = recommend(None, RulEstimate(p10=10.5, p50=69.3, p90=230.3), CM, GRID, Limits(max_risk_alpha=1.0), family="weibull", cap=1e9)
    assert rec.opportunity.t_cycles == GRID[-1]                 # latest feasible, i.e. no preventive benefit


def test_wearout_optimum_matches_textbook_renewal_reward():
    k, eta = 3.0, 100.0
    post = _weibull(k, eta)
    cf = CM.planned_cost_usd + CM.unplanned_penalty_usd
    cp = CM.planned_cost_usd

    def foc(a: float) -> float:   # h(a) * int_0^a S - F(a) = cp / (cf - cp)
        s = integrate.quad(lambda u: math.exp(-((u / eta) ** k)), 0, a)[0]
        h = (k / eta) * (a / eta) ** (k - 1)
        return h * s - (1 - math.exp(-((a / eta) ** k))) - cp / (cf - cp)

    a_star = optimize.brentq(foc, 1e-3, 1000)
    _, t_opt = optimal_rate(post, CM)
    assert t_opt == pytest.approx(a_star, rel=0.03)
    # on an explicit grid containing a_star, the table's argmin is a_star
    grid = sorted(set(GRID + [a_star]))
    rows = expected_cost_table(post, CM, grid, Limits(max_risk_alpha=1.0))
    assert min(rows, key=lambda r: r.rate_per_cycle).t_cycles == pytest.approx(a_star)


def test_monotone_in_costs():
    post = fit_posterior(30, 70, 130, cap=500)
    opts = [float(t) for t in range(5, 200, 5)]
    lim = Limits(max_risk_alpha=1.0)

    def t_best(cm: CostModel) -> float:
        rows = expected_cost_table(post, cm, opts, lim)
        return min(rows, key=lambda r: r.net_usd).t_cycles

    base = t_best(CM)
    assert t_best(CostModel(planned_cost_usd=20_000, aog_hours=24)) <= base       # higher C_u => earlier
    assert t_best(CostModel(planned_cost_usd=20_000, aog_hours=1)) >= base
    assert t_best(CostModel(planned_cost_usd=60_000)) >= base                      # higher C_p => later


def test_hard_floors_respected():
    rul = RulEstimate(p10=60, p50=120, p90=124)
    cm = CostModel(planned_cost_usd=1_000.0, aog_hours=0, delay_hours=0, cancelled_flights=0, secondary_damage_factor=0,
                   unscheduled_ground_usd=1e9)  # failures free -> would wait; floors must still bind
    lim = Limits(llp_remaining_cycles=45.0, mel_expiry_cycles=30.0, max_risk_alpha=0.05)
    rec = recommend(None, rul, cm, [10, 20, 30, 40, 50], lim)
    assert rec.opportunity is not None and rec.opportunity.t_cycles <= 30.0
    assert rec.p_fail_before <= 0.05 + 1e-9
    # nothing feasible -> replace now, never a window beyond a floor
    rec2 = recommend(None, RulEstimate(p10=2, p50=3, p90=4), CM, [10, 20], Limits(max_risk_alpha=0.05))
    assert rec2.action == "replace_now" and rec2.opportunity.t_cycles == 0.0
    # LLP already exhausted
    assert recommend(None, rul, CM, [10], Limits(llp_remaining_cycles=0.0)).action == "replace_now"


def test_voi_nonnegative_and_zero_cases():
    post = fit_posterior(30, 70, 130, cap=500)
    lim = Limits(max_risk_alpha=0.2)
    v = value_of_inspection(post, CM, GRID, lim, InspectionSpec(sigma_log=0.1, cost_usd=0.0))
    assert v.value_usd > 0 and v.sigma_post < v.sigma_prior
    tight = fit_posterior(69, 70, 71, cap=500)
    assert value_of_inspection(tight, CM, GRID, lim, InspectionSpec(0.1, 0.0)).value_usd < 1.0
    # uninformative inspection (huge error) carries no information
    assert value_of_inspection(post, CM, GRID, lim, InspectionSpec(sigma_log=1e6, cost_usd=0.0)).value_usd == pytest.approx(0, abs=1e-6)
    for s in (0.05, 0.3, 1.0):
        assert value_of_inspection(post, CM, GRID, lim, InspectionSpec(s, 0.0)).value_usd >= 0.0


def test_recommend_can_say_inspect_when_cheap():
    rul = RulEstimate(p10=30, p50=70, p90=130)
    r = recommend(None, rul, CM, GRID, Limits(max_risk_alpha=0.2), cap=500, inspection=InspectionSpec(0.05, 1.0))
    assert r.voi is not None and r.voi.net_value_usd > 0 and r.action == "inspect"
    r2 = recommend(None, rul, CM, GRID, Limits(max_risk_alpha=0.2), cap=500, inspection=InspectionSpec(0.05, 1e9))
    assert r2.action != "inspect"


# ------------------------------------------------------------------------------------------- closed loop
def _simulate(seed: int, belief_bias: float, belief_sigma: float, true_sigma_obs: float = 0.25, n: int = 400):
    """Common random numbers: both policies see the same lives and estimates. Returns (econ, p10) average cost per cycle."""
    rng = np.random.default_rng(seed)
    k, eta = 3.0, 100.0
    life = eta * rng.weibull(k, n)
    # the estimator sees ln(life) with error true_sigma_obs; belief_* is what the POLICY assumes about it
    obs = life * np.exp(rng.normal(0.0, true_sigma_obs, n))
    cm = CostModel(planned_cost_usd=20_000.0)
    cp, cu = cm.planned_cost_usd, cm.unplanned_penalty_usd
    opps = [Opportunity(float(t), f"w{t}") for t in range(10, 251, 10)]
    lim = Limits(max_risk_alpha=0.05)
    cost = {"econ": 0.0, "p10": 0.0}
    cyc = {"econ": 0.0, "p10": 0.0}
    viol = 0
    for i in range(n):
        p50 = float(obs[i]) * belief_bias
        z = 1.2815515655446004
        rul = RulEstimate(p10=p50 * math.exp(-z * belief_sigma), p50=p50, p90=p50 * math.exp(z * belief_sigma))
        rec = recommend(None, rul, cm, opps, lim, cap=1e9)
        if rec.action == "replace_now":
            t_e = 0.0
        else:
            t_e = rec.opportunity.t_cycles
            viol += int(rec.p_fail_before > lim.max_risk_alpha + 1e-9)
        t_p = rec.p10_rule_opportunity.t_cycles
        for name, t in (("econ", t_e), ("p10", t_p)):
            failed = life[i] <= t
            cost[name] += cp + (cu if failed else 0.0) + (cm.unscheduled_ground_usd if (name == "econ" and t == 0.0) else 0.0)
            cyc[name] += min(life[i], t)
    return cost["econ"] / max(cyc["econ"], 1e-9), cost["p10"] / max(cyc["p10"], 1e-9), viol


@pytest.fixture(scope="module")
def closed_loop():
    well = [_simulate(s, 1.0, 0.25) for s in range(30)]
    mis = [_simulate(s, 1.4, 0.10) for s in range(30)]  # optimistic bias (+40%) and overconfident (sigma 0.10 vs 0.25)
    return well, mis


def test_closed_loop_well_specified_economics_beats_p10_rule(closed_loop, capsys):
    well, mis = closed_loop
    wins = sum(e < p for e, p, _ in well)
    red = float(np.mean([(p - e) / p for e, p, _ in well]))
    mwins = sum(e < p for e, p, _ in mis)
    mred = float(np.mean([(p - e) / p for e, p, _ in mis]))
    print(f"CLOSED-LOOP well-specified: wins {wins}/30, mean cost reduction {red:.1%}; "
          f"mis-specified: wins {mwins}/30, mean change {mred:+.1%}")
    assert wins >= 24
    assert sum(v for _, _, v in well) == 0   # risk cap never exceeded by a recommendation


def test_closed_loop_mis_specified_is_reported_not_asserted(closed_loop):
    _, mis = closed_loop
    assert all(math.isfinite(e) and math.isfinite(p) for e, p, _ in mis)


def test_flag_off_derive_tasks_is_byte_identical_to_snapshot(monkeypatch):
    monkeypatch.setattr(settings, "REPLACEMENT_ECONOMICS", False)
    now = [json.loads(t.model_dump_json()) for t in derive_tasks(golden_state())]
    assert now == json.loads(SNAPSHOT.read_text())


def test_flag_on_changes_deadline_and_basis(monkeypatch):
    base = {t.id: t for t in derive_tasks(golden_state())}
    monkeypatch.setattr(settings, "REPLACEMENT_ECONOMICS", True)
    on = {t.id: t for t in derive_tasks(golden_state())}
    rul_ids = [i for i, t in on.items() if t.source == "rul"]
    assert rul_ids
    for i in rul_ids:
        assert "economics" in on[i].basis
        assert on[i].deadline <= base[i].deadline
