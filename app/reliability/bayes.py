"""Hierarchical Weibull model for fleet removal data (MAP + Laplace, numpy/scipy only).

Per part number p: shape beta_p (log-normal prior), log-scale ``log eta = mu_p + u_tail + u_env + u_batch`` with
``u_g ~ N(0, sigma_g^2)`` and half-normal priors on each sigma_g. The likelihood handles right censoring and left
truncation (late entry: conditional on survival to ``start``).

Inference: the random effects ``u`` are integrated out by a Laplace approximation; the hyper-parameters
``phi = (log beta_p, mu_p, log sigma_g)`` are found by L-BFGS on that marginal log-posterior (MAP). A joint Gaussian
(Laplace) approximation over ``(u, mu, log beta)`` at sigma-hat then gives posterior covariances (empirical Bayes: sigma
uncertainty is not propagated into the effect intervals, which makes them slightly optimistic). Synthetic recovery is not
field validity.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy.optimize import minimize
from scipy.special import gammaln

Z90 = 1.6448536269514722
GROUPS = ("tail", "env", "batch")


@dataclass(frozen=True)
class Record:
    """One part life: observed from ``start`` (late entry / left truncation) to ``end``; ``failed`` False = right-censored."""

    part_number: str
    tail: str | None
    start: float
    end: float
    failed: bool
    env: str | None = None
    batch: str | None = None
    serial: str | None = None
    unit: str = "FH"  # time unit label: FH (flight hours) or FC (cycles)


@dataclass
class FitConfig:
    groups: tuple[str, ...] = GROUPS
    beta_prior_median: float = 1.8  # weak prior around 1-3
    beta_prior_sd_log: float = 0.6
    mu_prior_sd: float = 3.0  # on the scaled log-eta
    sigma_prior_scale: float = 1.0  # half-normal scale on each group sigma
    interval: float = 0.90
    n_draws: int = 2000
    seed: int = 20260101
    min_events_warn: int = 5
    sigma_boundary: float = 0.03  # sigma-hat below this (in log-eta units) is reported as not distinguishable from 0


@dataclass
class _Data:
    pn: np.ndarray  # int index per obs
    logt: np.ndarray
    logt0: np.ndarray  # -inf where no late entry
    has0: np.ndarray
    d: np.ndarray
    X: np.ndarray  # (n_obs, n_u) incidence
    gidx: np.ndarray  # group index per u column
    n_pn: int
    n_g: int


def _loglik_terms(z: np.ndarray, beta: np.ndarray, dat: _Data) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-observation loglik, d/dz, d2/dz2 where z = log eta_i, beta per obs."""
    he = np.exp(np.minimum(beta * (dat.logt - z), 700.0))
    h0 = np.where(dat.has0, np.exp(np.minimum(beta * (np.where(dat.has0, dat.logt0, 0.0) - z), 700.0)), 0.0)
    ll = dat.d * (np.log(beta) + (beta - 1.0) * dat.logt - beta * z) - he + h0
    g = -dat.d * beta + beta * (he - h0)
    h = -(beta**2) * (he - h0)
    return ll, g, h


class _Model:
    def __init__(self, dat: _Data, cfg: FitConfig) -> None:
        self.dat, self.cfg = dat, cfg
        self.u_warm = np.zeros(dat.X.shape[1])

    # parameter layout phi = [logbeta (P), mu (P), logsigma (G)]
    def split(self, phi: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        P, G = self.dat.n_pn, self.dat.n_g
        return phi[:P], phi[P:2 * P], phi[2 * P:2 * P + G]

    def log_hyperprior(self, phi: np.ndarray) -> float:
        c = self.cfg
        lb, mu, ls = self.split(phi)
        lp = -0.5 * np.sum(((lb - math.log(c.beta_prior_median)) / c.beta_prior_sd_log) ** 2)
        lp += -0.5 * np.sum((mu / c.mu_prior_sd) ** 2)
        s = np.exp(ls)
        lp += np.sum(-0.5 * (s / c.sigma_prior_scale) ** 2 + ls)  # half-normal on sigma, Jacobian to log sigma
        return float(lp)

    def inner(self, phi: np.ndarray) -> tuple[float, np.ndarray, float]:
        """Maximise over u at fixed phi. Returns (log p(y,u_hat|phi) incl. u prior, u_hat, log|-H_u|)."""
        dat = self.dat
        lb, mu, ls = self.split(phi)
        beta = np.exp(lb)[dat.pn]
        base = mu[dat.pn]
        prec = (np.exp(-2.0 * ls))[dat.gidx] if dat.X.shape[1] else np.zeros(0)
        lognorm = float(np.sum(-ls[dat.gidx] - 0.5 * math.log(2 * math.pi))) if dat.X.shape[1] else 0.0
        X = dat.X
        if X.shape[1] == 0:
            ll, _, h = _loglik_terms(base, beta, dat)
            return float(ll.sum()), np.zeros(0), 0.0
        u = self.u_warm.copy()

        def obj(uv: np.ndarray) -> float:
            ll, _, _ = _loglik_terms(base + X @ uv, beta, dat)
            return float(ll.sum() - 0.5 * np.sum(prec * uv**2))

        f = obj(u)
        for _ in range(60):
            ll, g, h = _loglik_terms(base + X @ u, beta, dat)
            grad = X.T @ g - prec * u
            H = -(X.T @ (X * (-h)[:, None])) - np.diag(prec)  # negative definite
            try:
                step = np.linalg.solve(-H, grad)
            except np.linalg.LinAlgError:
                step = grad / (np.abs(np.diag(H)) + 1e-9)
            t = 1.0
            while t > 1e-8:
                fn = obj(u + t * step)
                if np.isfinite(fn) and fn >= f - 1e-12:
                    break
                t *= 0.5
            u, fprev, f = u + t * step, f, fn
            if abs(f - fprev) < 1e-10 and np.max(np.abs(t * step)) < 1e-7:
                break
        self.u_warm = u
        ll, _, h = _loglik_terms(base + X @ u, beta, dat)
        negH = (X.T @ (X * (-h)[:, None])) + np.diag(prec)
        sign, logdet = np.linalg.slogdet(negH)
        return float(ll.sum() - 0.5 * np.sum(prec * u**2) + lognorm), u, float(logdet if sign > 0 else np.inf)

    def neg_log_post(self, phi: np.ndarray) -> float:
        lj, u, logdet = self.inner(phi)
        nu = len(u)
        val = lj - 0.5 * logdet + 0.5 * nu * math.log(2 * math.pi) + self.log_hyperprior(phi)
        return -val if np.isfinite(val) else 1e12

    def joint_neg_log_post(self, theta: np.ndarray, ls: np.ndarray) -> float:
        """theta = [logbeta (P), mu (P), u (n_u)] at fixed log-sigma."""
        dat, c = self.dat, self.cfg
        P = dat.n_pn
        lb, mu, u = theta[:P], theta[P:2 * P], theta[2 * P:]
        beta = np.exp(lb)[dat.pn]
        ll, _, _ = _loglik_terms(mu[dat.pn] + dat.X @ u, beta, dat)
        lp = -0.5 * np.sum(((lb - math.log(c.beta_prior_median)) / c.beta_prior_sd_log) ** 2) - 0.5 * np.sum((mu / c.mu_prior_sd) ** 2)
        if len(u):
            lp += -0.5 * np.sum((u / np.exp(ls)[dat.gidx]) ** 2)
        return -float(ll.sum() + lp)

    def joint_grad(self, theta: np.ndarray, ls: np.ndarray) -> np.ndarray:
        dat, c = self.dat, self.cfg
        P = dat.n_pn
        lb, mu, u = theta[:P], theta[P:2 * P], theta[2 * P:]
        beta = np.exp(lb)[dat.pn]
        z = mu[dat.pn] + dat.X @ u
        ll, gz, _ = _loglik_terms(z, beta, dat)
        he = np.exp(np.minimum(beta * (dat.logt - z), 700.0))
        h0 = np.where(dat.has0, np.exp(np.minimum(beta * (np.where(dat.has0, dat.logt0, 0.0) - z), 700.0)), 0.0)
        # d ll / d logbeta = beta * d/dbeta
        d_lb_obs = dat.d * (1.0 + beta * (dat.logt - z)) - beta * (dat.logt - z) * he
        d_lb_obs = d_lb_obs + beta * np.where(dat.has0, (np.where(dat.has0, dat.logt0, 0.0) - z), 0.0) * h0
        g = np.zeros_like(theta)
        np.add.at(g[:P], dat.pn, d_lb_obs)
        g[:P] -= (lb - math.log(c.beta_prior_median)) / c.beta_prior_sd_log**2
        np.add.at(g[P:2 * P], dat.pn, gz)
        g[P:2 * P] -= mu / c.mu_prior_sd**2
        if len(u):
            g[2 * P:] = dat.X.T @ gz - u / np.exp(2 * ls)[dat.gidx]
        return -g


def _num_hessian(grad, x: np.ndarray, eps: float = 1e-5) -> np.ndarray:
    n = len(x)
    H = np.zeros((n, n))
    for i in range(n):
        e = np.zeros(n)
        e[i] = eps * max(1.0, abs(x[i]))
        H[:, i] = (grad(x + e) - grad(x - e)) / (2 * e[i])
    return 0.5 * (H + H.T)


def _num_hessian_scalar(f, x: np.ndarray, eps: float = 1e-3) -> np.ndarray:
    n = len(x)
    H = np.zeros((n, n))
    f0 = f(x)
    for i in range(n):
        ei = np.zeros(n)
        ei[i] = eps
        for j in range(i, n):
            ej = np.zeros(n)
            ej[j] = eps
            if i == j:
                v = (f(x + ei) - 2 * f0 + f(x - ei)) / eps**2
            else:
                v = (f(x + ei + ej) - f(x + ei - ej) - f(x - ei + ej) + f(x - ei - ej)) / (4 * eps**2)
            H[i, j] = H[j, i] = v
    return H


def _pinv_cov(H: np.ndarray, floor: float = 1e-8) -> tuple[np.ndarray, float, bool]:
    """Covariance from a precision matrix with a stable pseudo-inverse. Returns (cov, min_eig, singular)."""
    w, V = np.linalg.eigh(0.5 * (H + H.T))
    scale = max(float(np.max(np.abs(w))), 1.0)
    bad = w < floor * scale
    winv = np.where(bad, 0.0, 1.0 / np.where(bad, 1.0, w))
    return (V * winv) @ V.T, float(w.min()), bool(bad.any())


@dataclass
class FitResult:
    config: FitConfig
    part_numbers: list[str]
    group_levels: dict[str, list[str]]
    params: dict[str, Any]
    _theta: np.ndarray = field(repr=False)
    _cov: np.ndarray = field(repr=False)
    _sigma: dict[str, float] = field(repr=False)
    _counts: dict[str, dict[str, tuple[int, int]]] = field(repr=False)
    _diag: dict[str, Any] = field(repr=False)
    _scale: float = field(repr=False, default=1.0)

    # ---- accessors
    def diagnostics(self) -> dict[str, Any]:
        return dict(self._diag)

    def _u_index(self, group: str, level: str) -> int:
        off = 2 * len(self.part_numbers)
        for g in self.config.groups:
            if g not in self.group_levels:
                continue
            if g == group:
                return off + self.group_levels[g].index(level)
            off += len(self.group_levels[g])
        raise KeyError(group)

    def effects(self, group: str) -> list[dict[str, Any]]:
        """Per-level effect on log eta (multiplicative on eta: exp(effect)) with 90% intervals and shrinkage information."""
        if group not in self.group_levels:
            return []
        s = self._sigma[group]
        z = Z90 if abs(self.config.interval - 0.90) < 1e-9 else float(_norm_ppf(0.5 + self.config.interval / 2))
        out = []
        for lv in self.group_levels[group]:
            i = self._u_index(group, lv)
            m, sd = float(self._theta[i]), float(math.sqrt(max(self._cov[i, i], 0.0)))
            n, ev = self._counts[group][lv]
            weak = n < 2 or ev < 1
            out.append({
                "level": lv, "effect": m, "sd": sd, "lo": m - z * sd, "hi": m + z * sd, "eta_multiplier": math.exp(m),
                "n_records": n, "n_events": ev,
                "credibility": float(min(max(1.0 - sd**2 / s**2, 0.0), 1.0)) if s > 0 else 0.0,  # 0 = fully pooled, 1 = data-dominated
                "weakly_identified": bool(weak),
            })
        return out

    def _draws(self, part_number: str, group_values: Mapping[str, str] | None, n: int, rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray]:
        if part_number not in self.part_numbers:
            raise KeyError(f"unknown part_number {part_number!r}")
        p = self.part_numbers.index(part_number)
        P = len(self.part_numbers)
        idx = [p, P + p]
        extra = np.zeros(n)
        for g, lv in (group_values or {}).items():
            if g not in self.config.groups or lv is None:
                continue
            if g in self.group_levels and lv in self.group_levels[g]:
                idx.append(self._u_index(g, lv))
            elif g in self._sigma:  # unseen level: population-predictive draw
                extra += rng.normal(0.0, self._sigma[g], n)
        sel = np.array(idx)
        mean = self._theta[sel]
        C = self._cov[np.ix_(sel, sel)]
        w, V = np.linalg.eigh(0.5 * (C + C.T))
        L = V * np.sqrt(np.clip(w, 0, None))
        d = mean + rng.standard_normal((n, len(sel))) @ L.T
        beta = np.exp(d[:, 0])
        log_eta = d[:, 1:].sum(axis=1) + extra + math.log(self._scale)
        return beta, log_eta

    def predict_reliability(self, part_number: str, t: float, group_values: Mapping[str, str] | None = None) -> dict[str, float]:
        """R(t), MTBUR (Weibull mean) and B10 for a part number / group combination, with interval from Laplace draws.

        Omitted groups mean a typical unit (u = 0); an unseen level is drawn from the population distribution.
        """
        rng = np.random.default_rng(self.config.seed)
        beta, le = self._draws(part_number, group_values, self.config.n_draws, rng)
        eta = np.exp(le)
        lo_q, hi_q = 50 * (1 - self.config.interval), 100 - 50 * (1 - self.config.interval)

        def stat(b: np.ndarray, e: np.ndarray) -> dict[str, np.ndarray]:
            return {"reliability": np.exp(-((t / e) ** b)), "mtbur": e * np.exp(gammaln(1 + 1 / b)), "b10": e * (-math.log(0.9)) ** (1 / b)}

        pt = stat(*[np.array([x]) for x in self._point(part_number, group_values)])
        dr = stat(beta, eta)
        out: dict[str, float] = {}
        for k in ("reliability", "mtbur", "b10"):
            out[k] = float(pt[k][0])
            out[f"{k}_lo"] = float(np.percentile(dr[k], lo_q))
            out[f"{k}_hi"] = float(np.percentile(dr[k], hi_q))
        out["beta"] = float(np.exp(self._theta[self.part_numbers.index(part_number)]))
        return out

    def _point(self, part_number: str, group_values: Mapping[str, str] | None) -> tuple[float, float]:
        p = self.part_numbers.index(part_number)
        P = len(self.part_numbers)
        le = self._theta[P + p] + math.log(self._scale)
        for g, lv in (group_values or {}).items():
            if g in self.group_levels and lv in self.group_levels[g]:
                le += self._theta[self._u_index(g, lv)]
        return float(np.exp(self._theta[p])), float(np.exp(le))


def _norm_ppf(p: float) -> float:
    from scipy.stats import norm

    return float(norm.ppf(p))


def _build(records: Sequence[Record], cfg: FitConfig, groups: tuple[str, ...]) -> tuple[_Data, list[str], dict[str, list[str]], float, dict[str, dict[str, tuple[int, int]]], list[str]]:
    warns: list[str] = []
    pns = sorted({r.part_number for r in records})
    pidx = {p: i for i, p in enumerate(pns)}
    ends = np.array([r.end for r in records], float)
    scale = float(np.exp(np.mean(np.log(np.maximum(ends, 1e-9)))))
    levels: dict[str, list[str]] = {}
    for g in groups:
        vals = sorted({getattr(r, g) for r in records if getattr(r, g) is not None})
        if len(vals) == 1:
            warns.append(f"group '{g}' has a single level ({vals[0]}): its effect is not identifiable apart from the part-number mean; dropped from the model")
        elif len(vals) > 1:
            levels[g] = vals
    cols: list[tuple[str, str]] = [(g, lv) for g in levels for lv in levels[g]]
    cidx = {c: i for i, c in enumerate(cols)}
    X = np.zeros((len(records), len(cols)))
    for i, r in enumerate(records):
        for g in levels:
            v = getattr(r, g)
            if v is not None:
                X[i, cidx[(g, v)]] = 1.0
    counts = {g: {lv: (0, 0) for lv in levels[g]} for g in levels}
    for r in records:
        for g in levels:
            v = getattr(r, g)
            if v is not None:
                n, e = counts[g][v]
                counts[g][v] = (n + 1, e + int(r.failed))
    gi = {g: i for i, g in enumerate(levels)}
    dat = _Data(
        pn=np.array([pidx[r.part_number] for r in records]),
        logt=np.log(np.maximum(ends, 1e-9) / scale),
        logt0=np.array([math.log(max(r.start, 1e-9) / scale) if r.start > 0 else 0.0 for r in records]),
        has0=np.array([r.start > 0 for r in records]),
        d=np.array([1.0 if r.failed else 0.0 for r in records]),
        X=X, gidx=np.array([gi[g] for g, _ in cols], dtype=int), n_pn=len(pns), n_g=len(levels),
    )
    return dat, pns, levels, scale, counts, warns


def _optimise(model: _Model, phi0: np.ndarray) -> tuple[np.ndarray, Any]:
    best = None
    for start in (phi0, phi0 + np.r_[np.zeros(2 * model.dat.n_pn), np.full(model.dat.n_g, 0.7)]):
        model.u_warm = np.zeros_like(model.u_warm)
        res = minimize(model.neg_log_post, start, method="L-BFGS-B", options={"maxiter": 300, "ftol": 1e-10, "gtol": 1e-6})
        if best is None or res.fun < best.fun - 1e-9:
            best = res
    assert best is not None
    return best.x, best


def _fit_core(records: Sequence[Record], cfg: FitConfig, groups: tuple[str, ...]):
    dat, pns, levels, scale, counts, warns = _build(records, cfg, groups)
    model = _Model(dat, cfg)
    P, G = dat.n_pn, dat.n_g
    phi0 = np.r_[np.full(P, math.log(cfg.beta_prior_median)), np.zeros(P), np.full(G, math.log(0.3))]
    phi, res = _optimise(model, phi0)
    # hyper-parameter Laplace evidence
    Hphi = _num_hessian_scalar(model.neg_log_post, phi, eps=2e-3)
    w = np.linalg.eigvalsh(0.5 * (Hphi + Hphi.T))
    wpos = np.clip(w, 1e-8, None)
    log_evidence = -float(res.fun) + 0.5 * len(phi) * math.log(2 * math.pi) - 0.5 * float(np.sum(np.log(wpos)))
    # marginal loglik at phi-hat (without hyperprior) for AIC
    lj, u_hat, logdet = model.inner(phi)
    marg_ll = lj - 0.5 * logdet + 0.5 * len(u_hat) * math.log(2 * math.pi)
    return model, dat, pns, levels, scale, counts, warns, phi, res, log_evidence, marg_ll, float(w.min())


def fit(records: Iterable[Record], config: FitConfig | None = None) -> FitResult:
    """Fit the hierarchical Weibull model. Deterministic. Raises ValueError on empty / invalid data."""
    cfg = config or FitConfig()
    recs = list(records)
    if not recs:
        raise ValueError("no records")
    for r in recs:
        if not (r.end > 0) or r.start < 0 or r.end < r.start:
            raise ValueError(f"invalid record (need 0 <= start <= end, end > 0): {r}")
    groups = tuple(g for g in cfg.groups if g in GROUPS)
    model, dat, pns, levels, scale, counts, warns, phi, res, logev, marg_ll, min_eig_phi = _fit_core(recs, cfg, groups)
    P, G = dat.n_pn, dat.n_g
    lb, mu, ls = model.split(phi)
    _, u_hat, _ = model.inner(phi)
    theta = np.r_[lb, mu, u_hat]
    # joint Laplace covariance at sigma-hat
    H = _num_hessian(lambda x: model.joint_grad(x, ls), theta)
    cov, min_eig, singular = _pinv_cov(H)
    glist = list(levels)
    sigma = {g: float(np.exp(ls[i])) for i, g in enumerate(glist)}

    # null model (no group effects) for comparison
    null = _fit_core(recs, cfg, ())
    n_ev = int(dat.d.sum())
    k_full, k_null = 2 * P + G, 2 * P
    aic_full, aic_null = 2 * k_full - 2 * marg_ll, 2 * k_null - 2 * null[10]

    for g in glist:
        small = [lv for lv, (n, _) in counts[g].items() if n < 2]
        if small:
            warns.append(f"group '{g}': {len(small)} level(s) with a single record are weakly identified (effect is prior/shrinkage driven)")
        if sigma[g] < cfg.sigma_boundary:
            warns.append(f"group '{g}': sigma-hat ~ {sigma[g]:.3f} is at the boundary; no between-level variation detected (effects pooled to ~0)")
    if n_ev < cfg.min_events_warn * P:
        warns.append(f"only {n_ev} failures for {P} part number(s): shape and scale are prior-dominated")
    if singular:
        warns.append("posterior Hessian is singular/ill-conditioned: some directions are weakly identified, pseudo-inverse used")
    if not res.success:
        warns.append(f"optimiser did not fully converge: {res.message}")

    params = {
        "part_numbers": {
            p: {"beta": float(np.exp(lb[i])), "beta_sd_log": float(math.sqrt(max(cov[i, i], 0))),
                "mu": float(mu[i] + math.log(scale)), "mu_sd": float(math.sqrt(max(cov[P + i, P + i], 0))),
                "eta": float(math.exp(mu[i]) * scale)}
            for i, p in enumerate(pns)
        },
        "sigma": sigma,
        "time_scale": scale,
    }
    diag = {
        "n_records": len(recs), "n_events": n_ev, "n_censored": len(recs) - n_ev, "n_left_truncated": int(dat.has0.sum()),
        "converged": bool(res.success), "log_posterior": -float(res.fun), "marginal_loglik": marg_ll, "log_evidence": logev,
        "aic": aic_full, "aic_null": aic_null, "delta_aic": aic_null - aic_full, "log_evidence_null": null[9],
        "log_bayes_factor": logev - null[9], "effects_improve_fit": bool(aic_null - aic_full > 2.0 and logev - null[9] > 0),
        "hessian_min_eig": min_eig, "hessian_singular": singular, "warnings": warns,
    }
    return FitResult(config=cfg, part_numbers=pns, group_levels=levels, params=params, _theta=theta, _cov=cov, _sigma=sigma,
                     _counts=counts, _diag=diag, _scale=scale)


# ---------------------------------------------------------------- adapters
def _flag(v: Any) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes") if isinstance(v, str) else bool(v)


def _get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, Mapping):
        return row.get(key, default)
    return getattr(row, key, default)


def from_removal_records(rows: Iterable[Any], instances: Iterable[Any] | None = None, unit: str = "FH",
                         env_by_tail: Mapping[str, str] | None = None, batch_by_serial: Mapping[str, str] | None = None,
                         entry_by_serial: Mapping[str, float] | None = None) -> list[Record]:
    """Map this repo's v2 ``Removal`` rows (objects or dicts, e.g. from removals.csv) and in-service ``PartInstance`` rows to Records.

    Life is measured since the slot's last restoration (cumulative ``tsn_h``/``csn`` minus the age at the previous overhaul; replace resets to 0). A removal is
    a failure iff unscheduled and not NFF; planned removals and units still installed are right-censored. ``nff_return`` rows
    produce no life. Part number is the PartInstance ``part_no`` when ``instances`` resolves the serial, else the ``kind``.
    ``entry_by_serial`` supplies the age at which a used part entered observation (left truncation); default 0.
    """
    inst = list(instances or [])
    pn_of = {str(_get(i, "serial")): str(_get(i, "part_no")) for i in inst}
    cum = "tsn_h" if unit == "FH" else "csn"
    since_ovh = "tso_h" if unit == "FH" else "cso"
    out: list[Record] = []

    def mk(serial: str, kind: str, tail: Any, end: float, failed: bool) -> None:
        start = float((entry_by_serial or {}).get(serial, 0.0))
        if end <= 0 or end < start:
            return
        t = None if tail in (None, "") else str(tail)
        out.append(Record(part_number=pn_of.get(serial, kind), tail=t, start=start, end=float(end), failed=failed,
                          env=(env_by_tail or {}).get(t) if t else None, batch=(batch_by_serial or {}).get(serial),
                          serial=serial or None, unit=unit))

    base: dict[tuple[str, str], float] = {}  # cumulative age at the slot's last restoration (rows are chronological)
    for r in rows:
        action = str(_get(r, "action", ""))
        if action not in ("replace", "overhaul"):
            continue
        key = (str(_get(r, "kind", "")), str(_get(r, "unit", "")))
        age = float(_get(r, cum, 0.0) or 0.0)
        life = age - base.get(key, 0.0)
        failed = _flag(_get(r, "unscheduled", False)) and not _flag(_get(r, "nff", False))
        mk(str(_get(r, "serial", "")), key[0], _get(r, "tail"), life, failed)
        base[key] = 0.0 if action == "replace" else age
    for i in inst:
        if str(_get(i, "status", "installed")) != "installed":
            continue
        mk(str(_get(i, "serial", "")), str(_get(i, "kind", "")), _get(i, "installed_on"), float(_get(i, since_ovh, 0.0) or 0.0), False)
    return out
