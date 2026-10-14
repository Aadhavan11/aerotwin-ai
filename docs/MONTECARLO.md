# Monte-Carlo plan risk

SIMULATED data, advisory only. Code: `app/whatif/montecarlo.py` (engine), `app/ops/mc_api.py` (routes, mounted by `app/insights/router.py`).

## API

`POST /ops/montecarlo` (roles ops, maintenance, admin when `AUTH_REQUIRED=1`)

```json
{ "plan_id": "P-3", "n": 500, "seed": 0, "params": { "part_delay_sigma": 0.8 } }
```
All optional. `plan_id` omitted = the current optimised plan (plans are solved if missing). `n` 1..5000 (default 500), `seed` >= 0 (default 0).
`params` overrides any `MCConfig` field (see below); unknown or invalid -> 422 `{error, detail}`; unknown plan -> 404.

`GET /ops/montecarlo/last` -> the last result, or 404 `{error:"not_found"}`.

## Response keys

| key | meaning |
|---|---|
| `simulated` | always `true` |
| `provenance` | `{source, state_source, as_of, seed, n, model, config, prior_constants, synthetic}` |
| `n`, `seed` | trials run, seed used |
| `cached` | `true` when served from the (plan hash, n, seed) cache |
| `elapsed_s` | wall time of the request |
| `plan` | `{id, mode, feasible, plan_hash, assignments, deterministic:{total_cost_usd, downtime_h, flights_affected, late_or_unscheduled}}` |
| `cost_usd` | total cost per trial: `{mean, p50, p90, p95, max, std, cvar95}` |
| `flights_delayed`, `flights_cancelled`, `delay_min`, `aog_h`, `deadline_misses` | per-trial distribution `{mean, p50, p90, p95, max}` |
| `p_miss_any_deadline` | P(at least one deadline missed or a task left unscheduled) |
| `p_unplanned_aog` | P(some component fails before its job is done) |
| `unscheduled_tasks` | tasks the plan does not schedule (priced at the deferral allowance in every trial) |
| `risk_ranking` | list, most expensive first: `{rank, task_id, tail, station, start, end, p_late, p_overrun_infeasible, p_failure_before_done, expected_delay_h, expected_cost_usd, share_of_risk_cost}` |
| `histogram` | `{metric:"total_cost_usd", bin_edges:[21 numbers], counts:[20 ints]}` |
| `comparison` | `null`, or `{optimised, baseline, delta_mean_cost_usd, delta_cvar95_usd, delta_p_miss_any_deadline, deterministic:[{name,plan_id,mode,feasible,total_cost_usd,late_or_unscheduled}], note}`; `optimised`/`baseline` = `{plan_id, mode, cost_usd:{...}, p_miss_any_deadline, flights_delayed_mean, flights_cancelled_mean, aog_h_mean, deadline_misses_mean}`. Both plans use the same seed (shared weather and per-task draws) |

`p_late` = P(job ends after its deadline); `p_overrun_infeasible` = P(job ends more than 3 h (`DEFAULT_MAX_EXT_H`) after the aircraft's next departure,
i.e. beyond the extension the solver allows). `expected_cost_usd` = the job's own risk cost (overrun, delay, deferral, AOG, cancellations; weather
excluded, overlaps between jobs not netted).

## Model

Per trial and assignment (vectorised, deterministic per `(seed, task id)`):

* **Part delivery**: shipped part (or `eta_hours` > 0) arrives `lead x LogNormal(0, part_delay_sigma)`; the excess delays the job start.
* **Technician no-show**: Bernoulli `noshow_prob`; start delayed by Triangular(2, 4, 8) h.
* **Overrun**: job hours x LogNormal(0, `duration_sigma`), or Triangular(0.9, 1.0, 1.6) with `duration_model="triangular"`.
* **Failure before the job**: lognormal through RUL p10/p50/p90 (cycles), converted to time with `cycle_deadline`. Failing before the job ends = unplanned
  AOG: hours from failure to job start + `aog_recovery_h`; the aircraft's departures in that span are cancelled.
* **Weather**: per station used, Bernoulli `weather_prob` of an event in the next 120 h, plus stored `/insights/weather` events, rolled along the rotation with
  `disruptions.propagate`. Only the aircraft the plan touches are counted.

Delay: the aircraft's next departure after the job slips by `max(0, actual end - that departure)`; it rolls along the next 6 legs, absorbed by buffers above 35 min.

Cost (`app/solver/costs.py` constants): plan's parts + labor + bay + overhead + shipping, + overrun hours x (crew x labor rate + bay rate),
+ delay minutes x `DELAY_COST_USD_H`/60, + `DEFERRAL_ALLOWANCE_USD` per missed deadline or unscheduled task, + `AOG_COST_USD_H` (10 000, prior) per AOG hour,
+ `CANCELLATION_COST_USD` (15 000, prior) per cancelled flight. The two priors are illustrative, not real prices.

`MCConfig.zero()` (no uncertainty) makes every trial equal; it equals `plan.metrics.total_cost_usd` when no job has a planned extension
(`PlanMetrics.cost_delay_usd` ignores cascades; this model prices them).

Cache: last 32 results keyed by (hash of assignments + metrics + state inputs + config, n, seed).
