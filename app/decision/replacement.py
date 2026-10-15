"""Replacement timing under RUL uncertainty (decision-theoretic; SIMULATED priors, advisory only).

Pipeline: (p10, p50, p90) -> fitted life posterior (lognormal default, Weibull optional; right-censored at the model RUL cap) ->
for each maintenance opportunity ``t_k`` (cycles from now) the expected cost ``C_p + C_u * P(T <= t_k)`` plus residual-life waste, and
the renewal-reward cost rate (cost per cycle / flight hour) -> pick the option minimising the cost rate subject to HARD floors
(LLP limit, MEL expiry, risk cap ``P(fail before t) <= alpha``). ``C_u`` is the INCREMENTAL penalty of an unplanned failure (AOG, delay,
cancellation, secondary damage) on top of the replacement itself, so a failure costs ``C_p + C_u``.

Selection criterion (average-cost optimality for renewal processes): minimise ``net(t) = C_p + C_u F(t) - lam * E[min(T, t)]`` where
``lam`` is the long-run optimal cost rate (``lam_ref``, or the minimum renewal-reward ratio of the component's own posterior). With
``lam`` equal to the minimum ratio this is the same argmin as the textbook age-replacement ratio ``(C_p + C_u F)/E[min(T,t)]``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Sequence, Union

import numpy as np
from scipy.special import gammainc, gamma as _gamma

from app.solver import costs as _costs
from app.whatif.montecarlo import AOG_COST_USD_H, CANCELLATION_COST_USD, MCConfig

try:  # the health model's RUL cap (cycles); fall back to its documented value
    from app.ml.health import RUL_CAP as _RUL_CAP
except Exception:  # pragma: no cover
    _RUL_CAP = 125

RUL_CAP: float = float(_RUL_CAP)
Z90 = 1.2815515655446004          # standard normal 90th percentile
PRIOR_SIGMA = 0.8                 # PRIOR: log-sd of life when the estimate is censored/uninformative (wide)
FLIGHT_HOURS_PER_CYCLE = 1.5      # PRIOR: converts per-cycle rates to per flight hour
DEFAULT_ALPHA = 0.05              # policy max-risk cap P(fail before t)


# --------------------------------------------------------------------------------------------- costs
@dataclass(frozen=True)
class CostModel:
    """Cost priors (USD). ILLUSTRATIVE: the operator must set these; defaults come from solver/costs.py and whatif/montecarlo.py."""
    planned_cost_usd: float                                  # C_p: part + labor + bay + overhead of a planned replacement
    aog_hours: float = MCConfig().aog_recovery_h             # PRIOR: hours on ground after an unplanned failure
    aog_cost_usd_h: float = AOG_COST_USD_H                   # PRIOR (whatif)
    delay_hours: float = 2.0                                 # PRIOR: delay hours of the failure event
    delay_cost_usd_h: float = _costs.DELAY_COST_USD_H
    cancelled_flights: float = 1.0                           # PRIOR: expected cancellations per failure
    cancellation_cost_usd: float = CANCELLATION_COST_USD
    secondary_damage_factor: float = 0.25                    # PRIOR: extra damage as a fraction of C_p
    unscheduled_ground_usd: float = CANCELLATION_COST_USD    # PRIOR: extra cost of grounding "now" outside any window
    fh_per_cycle: float = FLIGHT_HOURS_PER_CYCLE

    @property
    def unplanned_penalty_usd(self) -> float:
        """C_u (incremental)."""
        return (self.aog_hours * self.aog_cost_usd_h + self.delay_hours * self.delay_cost_usd_h
                + self.cancelled_flights * self.cancellation_cost_usd + self.secondary_damage_factor * self.planned_cost_usd)

    @staticmethod
    def from_job(part: Optional[str], part_qty: int, duration_h: float, **kw) -> "CostModel":
        c_p = (_costs.PART_PRICE_USD.get(part, 0.0) * part_qty if part else 0.0) + _costs.JOB_OVERHEAD_USD \
            + duration_h * (_costs.CREW_SIZE * _costs.LABOR_RATE_USD_H + _costs.BAY_RATE_USD_H)
        return CostModel(planned_cost_usd=c_p, **kw)


@dataclass(frozen=True)
class Limits:
    """Hard floors. All in cycles from now. ``None`` = no such limit."""
    llp_remaining_cycles: Optional[float] = None
    mel_expiry_cycles: Optional[float] = None
    max_risk_alpha: float = DEFAULT_ALPHA

    def horizon(self) -> float:
        vals = [v for v in (self.llp_remaining_cycles, self.mel_expiry_cycles) if v is not None]
        return min(vals) if vals else math.inf


@dataclass(frozen=True)
class Opportunity:
    t_cycles: float
    label: str = ""


@dataclass(frozen=True)
class InspectionSpec:
    """Borescope / pad check: measures ln(life) with Gaussian error ``sigma_log`` (ASSUMED), costing ``cost_usd``."""
    sigma_log: float = 0.15
    cost_usd: float = _costs.PART_PRICE_USD["BORESCOPE-KIT"] + _costs.JOB_OVERHEAD_USD


# --------------------------------------------------------------------------------------------- posterior
def _phi(x):
    from scipy.special import ndtr
    return ndtr(x)


@dataclass(frozen=True)
class LifePosterior:
    """Life (remaining cycles) distribution. family 'lognormal': (a=mu, b=sigma); 'weibull': (a=shape k, b=scale eta)."""
    family: str
    a: float
    b: float
    censored: str = "none"      # "none" | "upper" (p90 capped; sigma from lower half) | "all" (median capped; prior used)
    note: str = ""

    def cdf(self, t):
        t = np.asarray(t, dtype=float)
        tt = np.maximum(t, 1e-12)
        if self.family == "lognormal":
            out = _phi((np.log(tt) - self.a) / self.b)
        else:
            out = 1.0 - np.exp(-((tt / self.b) ** self.a))
        return np.where(t <= 0, 0.0, out)

    def ppf(self, q: float) -> float:
        if self.family == "lognormal":
            from scipy.special import ndtri
            return float(math.exp(self.a + self.b * float(ndtri(q))))
        return float(self.b * (-math.log1p(-q)) ** (1.0 / self.a))

    @property
    def mean(self) -> float:
        if self.family == "lognormal":
            return math.exp(self.a + 0.5 * self.b ** 2)
        return self.b * float(_gamma(1.0 + 1.0 / self.a))

    def mean_min(self, t):
        """E[min(T, t)] = integral_0^t S(u) du (closed form)."""
        t = np.asarray(t, dtype=float)
        tt = np.maximum(t, 1e-12)
        if self.family == "lognormal":
            m, s = self.a, self.b
            lt = np.log(tt)
            out = math.exp(m + 0.5 * s * s) * _phi((lt - m - s * s) / s) + tt * (1.0 - _phi((lt - m) / s))
        else:
            k, eta = self.a, self.b
            out = eta * float(_gamma(1.0 + 1.0 / k)) * gammainc(1.0 + 1.0 / k, (tt / eta) ** k) + tt * np.exp(-((tt / eta) ** k))
        return np.where(t <= 0, 0.0, out)

    def lognormal_equiv(self) -> "LifePosterior":
        """Lognormal with the same p50 and p10/p90 (used by the preposterior VOI calculation)."""
        if self.family == "lognormal":
            return self
        p10, p50, p90 = self.ppf(0.1), self.ppf(0.5), self.ppf(0.9)
        return LifePosterior("lognormal", math.log(p50), (math.log(p90) - math.log(p10)) / (2 * Z90), self.censored, self.note)


def fit_posterior(p10: float, p50: float, p90: float, cap: float = RUL_CAP, family: str = "lognormal",
                  prior_sigma: float = PRIOR_SIGMA) -> LifePosterior:
    """Fit the life posterior to the RUL quantiles (cycles).

    lognormal: mu = ln p50, sigma = (ln p90 - ln p10) / (2 z90). weibull: shape/scale from the p10 and p90 quantile equations.
    Capped values are LOWER BOUNDS: p90 >= cap -> sigma taken from the lower half only (p50, p10); p50 >= cap -> the wide prior
    (sigma = ``prior_sigma``) placed so that its own p10 equals the cap.
    """
    if family not in ("lognormal", "weibull"):
        raise ValueError("family must be 'lognormal' or 'weibull'")
    p10 = max(float(p10), 0.5)
    p50 = max(float(p50), p10)
    p90 = max(float(p90), p50)
    tol = 1e-9
    if p50 >= cap - tol:
        mu = math.log(cap) + Z90 * prior_sigma
        note = f"RUL p50 {p50:g} at the cap {cap:g}: censored, lower bound only; wide prior (sigma={prior_sigma:g}) used"
        return _from_lognormal(mu, prior_sigma, family, "all", note)
    if p90 >= cap - tol:
        s = max((math.log(p50) - math.log(p10)) / Z90, 0.05)
        return _from_lognormal(math.log(p50), s, family, "upper",
                               f"RUL p90 at the cap {cap:g}: censored, sigma from p10..p50 only")
    s = max((math.log(p90) - math.log(p10)) / (2 * Z90), 0.05)
    if family == "weibull":  # exact p10 and p90
        k = (math.log(-math.log(0.1)) - math.log(-math.log(0.9))) / (math.log(p90) - math.log(p10))
        return LifePosterior("weibull", k, p90 / (math.log(10.0) ** (1.0 / k)))
    return _from_lognormal(math.log(p50), s, family, "none", "")


def _from_lognormal(mu: float, sigma: float, family: str, censored: str, note: str) -> LifePosterior:
    if family == "lognormal":
        return LifePosterior("lognormal", mu, sigma, censored, note)
    # Weibull with the same p10 / p90 as that lognormal
    p10, p90 = math.exp(mu - Z90 * sigma), math.exp(mu + Z90 * sigma)
    x10, x90 = math.log(-math.log(0.9)), math.log(-math.log(0.1))
    k = (x90 - x10) / (math.log(p90) - math.log(p10))
    eta = p90 / (math.log(10.0) ** (1.0 / k))   # matches p10 and p90 exactly; p50 is then implied
    return LifePosterior("weibull", k, eta, censored, note)


# --------------------------------------------------------------------------------------------- table
@dataclass(frozen=True)
class Row:
    label: str
    t_cycles: float
    p_fail: float
    expected_cost_usd: float        # C_p + C_u F + residual-life waste
    waste_usd: float
    expected_cycles: float          # E[min(T, t)]
    rate_per_cycle: float           # renewal-reward ratio (C_p + C_u F) / E[min(T,t)]
    rate_per_fh: float
    net_usd: float                  # C_p + C_u F - lam E[min]  (selection criterion)
    feasible: bool
    floor: str = ""                 # why infeasible


def _grid(post: LifePosterior, horizon: float = math.inf) -> np.ndarray:
    hi = max(post.ppf(0.9999), 5.0)
    if math.isfinite(horizon):
        hi = min(hi, max(horizon, 1.0))
    return np.geomspace(0.25, hi, 600)


def optimal_rate(post: LifePosterior, cm: CostModel, horizon: float = math.inf) -> tuple[float, float]:
    """(min renewal-reward ratio per cycle, argmin age) over a dense grid; age is inf if the ratio is still falling at the end."""
    g = _grid(post, horizon)
    r = (cm.planned_cost_usd + cm.unplanned_penalty_usd * post.cdf(g)) / post.mean_min(g)
    i = int(np.argmin(r))
    t_opt = math.inf if i >= len(g) - 2 else float(g[i])
    return float(r[i]), t_opt


def expected_cost_table(post: LifePosterior, cm: CostModel, times: Sequence[Union[float, Opportunity]],
                        limits: Optional[Limits] = None, lam: Optional[float] = None) -> list[Row]:
    """Pure: one Row per opportunity (cycles from now). ``lam`` defaults to the posterior's own optimal rate."""
    limits = limits or Limits()
    if lam is None:
        lam = optimal_rate(post, cm)[0]
    cp, cu = cm.planned_cost_usd, cm.unplanned_penalty_usd
    mean = post.mean
    rows: list[Row] = []
    for k, o in enumerate(times):
        t = o.t_cycles if isinstance(o, Opportunity) else float(o)
        label = (o.label if isinstance(o, Opportunity) and o.label else f"t{k}")
        f = float(post.cdf(t))
        em = float(post.mean_min(t))
        waste = cp / mean * max(mean - em, 0.0) if mean > 0 else 0.0
        floor = ""
        if limits.llp_remaining_cycles is not None and t > limits.llp_remaining_cycles + 1e-9:
            floor = "beyond LLP limit"
        elif limits.mel_expiry_cycles is not None and t > limits.mel_expiry_cycles + 1e-9:
            floor = "beyond MEL expiry"
        elif f > limits.max_risk_alpha + 1e-12:
            floor = f"P(fail) {f:.3f} > cap {limits.max_risk_alpha:g}"
        base = cp + cu * f
        rate = base / em if em > 1e-12 else math.inf
        rows.append(Row(label, t, f, base + waste, waste, em, rate, rate / cm.fh_per_cycle, base - lam * em, floor == "", floor))
    return rows


# --------------------------------------------------------------------------------------------- VOI
@dataclass(frozen=True)
class VOI:
    value_usd: float            # expected reduction in net cost from re-deciding after the inspection (>= 0)
    inspection_cost_usd: float
    net_value_usd: float        # value - cost
    sigma_prior: float
    sigma_post: float


def value_of_inspection(post: LifePosterior, cm: CostModel, times: Sequence[Union[float, Opportunity]],
                        limits: Optional[Limits], spec: InspectionSpec, lam: Optional[float] = None,
                        include_now: bool = True) -> VOI:
    """Preposterior analysis. ASSUMED: inspection returns y = ln T + e, e ~ N(0, sigma_log^2); prior ln T ~ N(mu, s^2)
    (lognormal equivalent of the posterior). Posterior sd s' = (1/s^2 + 1/sigma_log^2)^-1/2 and the posterior location is
    N(mu, s^2 - s'^2) over possible measurements (Gauss-Hermite, 21 nodes). The feasible option set is fixed to the prior's so
    VOI >= 0 by Jensen (min of linear functionals is concave). Inspection time/downtime is NOT modelled."""
    ln = post.lognormal_equiv()
    if lam is None:
        lam = optimal_rate(post, cm)[0]
    s = ln.b
    sl = max(spec.sigma_log, 1e-9)
    s_post = 1.0 / math.sqrt(1.0 / s ** 2 + 1.0 / sl ** 2)
    sd_mu = math.sqrt(max(s * s - s_post * s_post, 0.0))
    rows = [r for r in expected_cost_table(ln, cm, times, limits, lam) if r.feasible]
    ts = np.array([r.t_cycles for r in rows], dtype=float)
    cp, cu = cm.planned_cost_usd, cm.unplanned_penalty_usd
    now_net = cp + cm.unscheduled_ground_usd

    def best(p: LifePosterior) -> float:
        opts = [now_net] if include_now else []
        if len(ts):
            opts += list(cp + cu * p.cdf(ts) - lam * p.mean_min(ts))
        return min(opts)

    x, w = np.polynomial.hermite_e.hermegauss(21)
    w = w / w.sum()
    after = sum(wi * best(LifePosterior("lognormal", ln.a + sd_mu * xi, s_post)) for xi, wi in zip(x, w))
    value = max(0.0, best(ln) - float(after))
    return VOI(value, spec.cost_usd, value - spec.cost_usd, s, s_post)


# --------------------------------------------------------------------------------------------- recommendation
@dataclass
class Recommendation:
    action: str                                 # "replace_at" | "replace_now" | "defer" | "inspect"
    opportunity: Optional[Opportunity]
    expected_cost_usd: float
    p_fail_before: float
    rate_per_fh: float
    p10_rule_opportunity: Optional[Opportunity]
    p10_rule_expected_cost_usd: float
    regret_vs_p10_usd: float                    # net-cost difference (selection criterion units); >= 0 when p10 rule feasible
    regret_vs_p10_rate_per_fh: float
    table: list[Row]
    voi: Optional[VOI]
    posterior: LifePosterior
    rationale: str
    notes: list[str] = field(default_factory=list)


def _p10_rule(rows: Sequence[Row], p10: float) -> Optional[Row]:
    """Today's rule: the latest opportunity not after floor(p10) cycles; else the earliest one (best effort)."""
    if not rows:
        return None
    ok = [r for r in rows if r.t_cycles <= math.floor(p10 + 1e-9) + 1e-9]
    return max(ok, key=lambda r: r.t_cycles) if ok else min(rows, key=lambda r: r.t_cycles)


def recommend(component_state, rul, costs: CostModel, windows: Sequence[Union[float, Opportunity]],
              limits: Optional[Limits] = None, *, family: str = "lognormal", cap: float = RUL_CAP,
              inspection: Optional[InspectionSpec] = None, lam_ref: Optional[float] = None) -> Recommendation:
    """Recommend what to do about one component. ``rul`` has p10/p50/p90 (cycles); ``component_state`` is only used for the label.
    ``windows`` are opportunities in cycles from now (see ``windows_to_cycles`` for datetimes)."""
    limits = limits or Limits()
    post = fit_posterior(rul.p10, rul.p50, rul.p90, cap=cap, family=family)
    cid = getattr(component_state, "component_id", "component")
    opps = sorted((w if isinstance(w, Opportunity) else Opportunity(float(w), f"t={float(w):g}") for w in windows),
                  key=lambda o: o.t_cycles)
    lam = lam_ref if lam_ref is not None else optimal_rate(post, costs, limits.horizon())[0]
    rows = expected_cost_table(post, costs, opps, limits, lam)
    notes = [post.note] if post.note else []
    cp = costs.planned_cost_usd
    now_net = cp + costs.unscheduled_ground_usd
    feas = [r for r in rows if r.feasible]
    best = min(feas, key=lambda r: (r.net_usd, r.t_cycles)) if feas else None
    p10row = _p10_rule(rows, rul.p10)
    voi: Optional[VOI] = None
    if inspection is not None:
        voi = value_of_inspection(post, costs, opps, limits, inspection, lam)
    horizon_bad = limits.llp_remaining_cycles is not None and limits.llp_remaining_cycles <= 0

    if best is None or best.net_usd > now_net or horizon_bad:
        action, opp = "replace_now", Opportunity(0.0, "now")
        exp_cost, pf, rate = cp + costs.unscheduled_ground_usd, 0.0, math.inf
        why = ("no opportunity satisfies the hard floors" if best is None else "grounding now is cheaper than any feasible window")
        rationale = f"{cid}: replace now ({why}; floors: LLP/MEL/risk cap {limits.max_risk_alpha:g})"
    else:
        opp = Opportunity(best.t_cycles, best.label)
        exp_cost, pf, rate = best.expected_cost_usd, best.p_fail, best.rate_per_fh
        action = "defer" if (len(opps) >= 2 and best.t_cycles == opps[-1].t_cycles) else "replace_at"
        rationale = (f"{cid}: {action.replace('_', ' ')} {best.label} (t={best.t_cycles:g} cyc): P(fail before)={pf:.1%}, "
                     f"expected cost ${exp_cost:,.0f}, rate ${rate:,.0f}/FH; C_p ${cp:,.0f}, C_u ${costs.unplanned_penalty_usd:,.0f}")
    if voi is not None and voi.net_value_usd > 0 and action != "replace_now":
        action = "inspect"
        rationale += f"; inspect first: expected value ${voi.value_usd:,.0f} > cost ${voi.inspection_cost_usd:,.0f}"
    regret = regret_rate = 0.0
    p10_cost = math.nan
    p10_opp = None
    if p10row is not None:
        p10_opp = Opportunity(p10row.t_cycles, p10row.label)
        p10_cost = p10row.expected_cost_usd
        ref = best.net_usd if best is not None and action != "replace_now" else now_net
        regret = max(0.0, p10row.net_usd - ref) if action != "replace_now" else p10row.net_usd - ref
        regret_rate = (p10row.rate_per_fh - rate) if math.isfinite(rate) else 0.0
        rationale += f"; vs p10 rule at {p10row.label}: regret ${regret:,.0f}"
    return Recommendation(action, opp, exp_cost, pf, rate, p10_opp, p10_cost, regret, regret_rate, rows, voi, post, rationale, notes)


def windows_to_cycles(window_starts: Sequence[datetime], legs, tail: str, now: datetime) -> list[Opportunity]:
    """Convert ground-window start times to cycles from now using the aircraft's scheduled rotation (solver machinery)."""
    from app.solver.common import aware
    from app.solver.tasks import _future_legs

    now = aware(now)
    future = _future_legs(list(legs), tail, now)
    out: list[Opportunity] = []
    for ws in sorted(window_starts):
        ws = aware(ws)
        n = sum(1 for leg in future if aware(leg.sched_arr) <= ws)
        out.append(Opportunity(float(n), ws.strftime("%Y-%m-%dT%H:%MZ")))
    return out


# --------------------------------------------------------------------------------------------- solver integration
def economic_deadline_cycles(rul, costs: CostModel, limits: Optional[Limits] = None, family: str = "lognormal",
                             cap: float = RUL_CAP) -> tuple[float, str]:
    """Latest acceptable cycles-from-now: min(time P(fail) hits alpha, cost-optimal age, LLP/MEL floor)."""
    limits = limits or Limits()
    post = fit_posterior(rul.p10, rul.p50, rul.p90, cap=cap, family=family)
    t_alpha = post.ppf(limits.max_risk_alpha)
    _, t_opt = optimal_rate(post, costs, limits.horizon())
    t = min(t_alpha, t_opt, limits.horizon())
    if t == t_opt and t_opt < t_alpha:
        why = f"cost-optimal age {t_opt:.0f} cyc (C_p ${costs.planned_cost_usd:,.0f}, C_u ${costs.unplanned_penalty_usd:,.0f})"
    elif t == t_alpha:
        why = f"P(fail) reaches {limits.max_risk_alpha:.0%} at {t_alpha:.0f} cyc"
    else:
        why = "LLP/MEL floor"
    extra = f"; {post.note}" if post.note else ""
    return t, f"economics ({post.family} fit): deadline {t:.1f} cyc, {why}{extra}"
