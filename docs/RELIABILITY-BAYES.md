# Hierarchical Weibull reliability model (`app/reliability/bayes.py`)

**Synthetic recovery != field validity.** Everything below is validated on simulated fleets where the truth is known and the
model form is correct by construction. It says the estimator is implemented correctly and calibrated *if* the world is
Weibull with Gaussian random effects. It says nothing about whether real fleet removals look like that.

## Model

Per part number `p`, record `i` (a part life observed from `start` to `end`, `failed` or right-censored):

- `T_i ~ Weibull(beta_p, eta_i)`, `log eta_i = mu_p + u_tail + u_env + u_batch`
- `u_g ~ Normal(0, sigma_g^2)` per group level; `sigma_g ~ HalfNormal(scale 1.0)`
- `log beta_p ~ Normal(log 1.8, 0.6^2)` (weak, mass over roughly 1-3); `mu_p ~ Normal(0, 3^2)` on log eta scaled by the data's geometric-mean time
- Likelihood: failures contribute the density, censored lives the survival function, and late entry (`start > 0`) conditions on
  survival to `start` (left truncation): `log L = d*log h(end) - H(end) + H(start)`.
- Time unit: flight hours (`FH`) or cycles (`FC`); do not mix units in one fit.

## Inference

1. Random effects `u` are integrated out with a Laplace approximation (inner Newton); hyper-parameters
   `(log beta, mu, log sigma)` are found by L-BFGS on that marginal log-posterior (MAP, multi-start).
2. A joint Gaussian approximation over `(log beta, mu, u)` at sigma-hat (numerical Hessian of an analytic gradient,
   eigen-decomposition pseudo-inverse) gives posterior covariances. Sigma uncertainty is not propagated (empirical Bayes), so
   effect intervals are somewhat optimistic when data are thin.
3. Model vs null (`diagnostics()`): AIC of the marginal likelihood with and without group effects (`delta_aic > 0` favours
   effects), and a Laplace log evidence over the hyper-parameters for both (`log_bayes_factor > 0` favours effects).
   `effects_improve_fit` requires `delta_aic > 2` and a positive log Bayes factor.

Fully deterministic (fixed seed for prediction draws; no stochastic optimisation).

## API

```python
from app.reliability import FitConfig, Record, fit, from_removal_records
res = fit(records, FitConfig(groups=("tail", "env", "batch")))
res.params                      # per part number: beta, mu (log eta), eta, sds; sigma per group
res.effects("tail")             # level, effect (log eta), sd, lo/hi 90%, eta_multiplier, n_records, n_events, credibility, weakly_identified
res.predict_reliability("PN", t=1500, group_values={"tail": "N123"})  # reliability, mtbur, b10 with _lo/_hi
res.diagnostics()               # counts, convergence, AIC/evidence vs null, Hessian conditioning, warnings
```

`from_removal_records(rows, instances=None, unit="FH", env_by_tail=..., batch_by_serial=..., entry_by_serial=...)` maps v2
`Removal` rows (objects or dicts, e.g. `removals.csv.gz`) and in-service `PartInstance` rows: life is since the slot's last
restoration, failure = unscheduled and not NFF, planned removals and units still installed are right-censored, `nff_return`
rows give no life. The simulator does not record install age of used parts, so late entry must be supplied via
`entry_by_serial`; env and batch labels are not in the simulator output and are caller-supplied.

## Reading the output

- **Effect** is on `log eta`: `exp(effect)` multiplies the characteristic life. Negative = removed earlier than typical.
  The interval is a 90% credible interval; an interval spanning 0 means no distinguishable tail effect.
- **Shrinkage ("borrowing strength")**: a tail with few records or no failures is pulled toward 0, and its interval is close
  to the population spread. `credibility` runs from 0 (fully pooled, data say nothing) to 1 (data-dominated).
  Do not rank tails by raw effect without looking at `credibility` / the interval.
- **Predictions**: omitted groups mean a typical unit (u = 0); an unseen level is drawn from the population distribution.
  MTBUR is the Weibull mean `eta * Gamma(1 + 1/beta)`, B10 the 10th percentile life. Intervals come from 2000 draws of the
  Laplace Gaussian.
- **Warnings** in `diagnostics()["warnings"]`: single-level groups (dropped, not identifiable apart from `mu`), levels with a
  single record (`weakly_identified`), very few failures (prior-dominated), sigma at the boundary (no between-level
  variation detected), singular Hessian (pseudo-inverse used), optimiser non-convergence.

## Validation observed (tests/test_bayes_recovery.py)

Synthetic fleets of 24-60 tails, 1-5 parts per tail, half with late entry, about 68% right-censored, true beta 2.0,
`mu = ln 3000`, sigma_tail 0.4 / env 0.2 / batch 0.15. Observed:

| replications | beta bias | mu bias | 90% coverage tail effects | 90% coverage beta |
|---|---|---|---|---|
| 60 (default) | -0.024 | +0.071 | 0.882 | 0.950 |
| 200 (`RUN_SLOW=1`) | -0.011 | +0.050 | 0.874 | 0.955 |

Unflattering points: tail-effect coverage is a little below nominal (0.87-0.88; sigma and its uncertainty are not
propagated, and thin tails are prior-sensitive); `mu` is biased about +0.05 to +0.07 in log eta (5-7% in eta) under this
censoring; beta coverage is slightly conservative. Tests also check that ignoring censoring biases `mu` low by more than 0.3,
that ignoring late entry biases it, that small-count tails shrink more than large-count ones, and that identifiability
warnings fire. Runtime: a fit takes about 0.05-0.1 s; the test file runs in about 20 s (35 s with the full 200 reps).

## Limits

- Weibull and additive Gaussian effects on log eta are assumptions, not findings. No competing-risk, no wear-out/infant
  mixture, no covariate shifts besides group effects, effects are shared across part numbers within a fit.
- Laplace is poor with very few failures, strong skew, or sigma near zero; the evidence over hyper-parameters is crude there.
- Removal reason quality (NFF, mis-coded failures) is an input problem this model cannot detect.
- Censoring is assumed non-informative: units are not retired because they looked bad.
- Synthetic recovery != field validity: do not quote these intervals as field accuracy.
