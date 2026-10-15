# Replacement timing under RUL uncertainty

Code: `app/decision/replacement.py`. Flag: `REPLACEMENT_ECONOMICS` (default OFF). Advisory only.

## Method
1. **Posterior.** (p10, p50, p90) in cycles is fitted to a lognormal (default: mu = ln p50, sigma from the p10..p90 spread) or a Weibull
   (shape and scale from the p10 and p90 equations). Values at the model RUL cap (`app.ml.health.RUL_CAP`) are right-censored lower
   bounds: p90 at cap -> sigma from p10..p50 only; p50 at cap -> a wide prior (sigma 0.8) whose own p10 equals the cap.
2. **Per opportunity t_k** (cycles from now): `P(fail) = F(t_k)`, expected cost `C_p + C_u F(t_k)` plus waste
   `C_p / mean_life * E[(T - t_k)+]`, renewal-reward rate `(C_p + C_u F) / E[min(T, t_k)]` per cycle and per flight hour.
   `C_u` is the incremental penalty of an unplanned failure (a failure costs `C_p + C_u`).
3. **Selection.** Minimise `net = C_p + C_u F - lam E[min(T,t)]` (average-cost optimality; `lam` is the optimal long-run rate, own
   posterior unless `lam_ref` given) over options that satisfy the hard floors: LLP remaining cycles, MEL expiry, and
   `P(fail before t) <= alpha` (default 5%). "Replace now" (unscheduled ground penalty) is always available; if no window is feasible
   it is the answer. Actions: `replace_at`, `replace_now`, `defer` (best option is the last listed window), `inspect`.
4. **Regret vs the p10 rule** (latest window not after floor(p10), else the earliest): difference in `net`, plus the expected-cost and
   rate columns. The full table is returned.
5. **VOI of an inspection.** Assumed measurement `y = ln T + e`, `e ~ N(0, sigma_log^2)`; Bayesian update of the lognormal posterior,
   preposterior expectation by 21-node Gauss-Hermite, re-deciding on the same feasible set (so VOI >= 0 by Jensen). Inspect when
   `VOI - inspection cost > 0`. Inspection downtime is not modelled.
6. **Solver integration** (flag ON): RUL task deadline = floor(min(time P(fail)=alpha, cost-optimal age, LLP/MEL floor)) cycles,
   mapped to time by the existing `cycle_deadline`; the rationale is added to the task basis. Flag OFF: output is byte-identical to
   `tests/snapshots/golden_tasks.json` (tested). Note: with alpha 5% the economic deadline is earlier than p10.

## Assumptions
- Cost priors (`CostModel`: AOG hours/rate, delay, cancellations, secondary-damage factor, ground-now penalty, flight hours per
  cycle) are ILLUSTRATIVE, reused from `solver/costs.py` and `whatif/montecarlo.py`. The operator must set them.
- The component is renewed "as new" with the same life distribution; the per-component posterior is residual life.
- 1 flight = 1 cycle; windows are converted with the aircraft's scheduled rotation (`windows_to_cycles`).

## Failure modes
- Wrong or overconfident RUL quantiles; a miscalibrated sigma moves the risk cap quantile directly.
- Censoring at the cap hides true life: the fallback prior is deliberately wide and may be too conservative.
- Lognormal vs Weibull tails differ, especially for the early-failure probability the cap relies on.
- Cost priors dominate: halving `C_u` can move the optimal age materially; no sensitivity is implied by one point estimate.
- Failure modes without wear-out (exponential) get no preventive benefit by construction; the tool will push replacement later.

## Validity
Closed-loop tests draw lives from a ground-truth Weibull (shape 3, scale 100 cycles, 400 lives per seed, 30 seeds, common random
numbers) and compare realised cost per cycle with the p10 rule. Observed (`pytest -s tests/test_replacement.py`): well-specified
29/30 seeds better, mean cost reduction about 5%; mis-specified (+40% optimistic, overconfident) 30/30, about 8% in this setup.
**Synthetic evaluation is not field validity**: it shows the algorithm is internally consistent, not that real RUL estimates or cost
priors are right. Validate against fleet removal and failure data before use.
