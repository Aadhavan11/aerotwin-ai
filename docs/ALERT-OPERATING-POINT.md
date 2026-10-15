# Alert operating point (SIMULATED, advisory)

`app/ml/operating_point.py`, `GET /insights/operating-point`, CLI `python -m app.ml.operating_point [--full]` (writes JSON, Markdown and SVG cost curves to `out/operating-point/`).

## Method
1. Re-run the back-test worlds (`app/ml/backtest.py`: faulted worlds with known onsets plus fault-free control twins) and keep the scorer's raw
   `anomaly_score` per scored cycle (sim-trained scorer if artifacts exist, else the rule scorer; engines use the CMAPSS model).
2. Sweep the alert threshold (0.05 to 0.95 step 0.05, plus the scorer's own default). An alert is `score >= threshold`.
3. Per threshold: TPR (failures detected before failure / failures), alert-episode precision, false-alarm episodes per 1000 FH
   (fault-free units and faulted units before onset), median lead time (cycles and FH).
4. Cost per 1000 FH = 1000 x (false alarms x `false_alarm_cost` + sum over failures of `miss_cost` if undetected, else
   `max(0, miss_cost - lead_value x min(lead, lead_cap_cycles))`) / scored FH.
5. Optimum = feasible threshold with minimum cost (feasible: false alarms <= `max_false_alarms_per_1000fh` and median lead >=
   `min_lead_cycles`; ties go to the less sensitive threshold). Regret = cost(default threshold) - cost(optimum).
6. 200+ unit-cluster bootstrap resamples (cluster = seed, kind, unit; a unit's faulted and control streams stay together, seed fixed):
   90% intervals on cost, on the optimal threshold and on regret.

## How to read
Per kind: `grid` (the cost curve with `cost_ci90`), `optimum` (threshold, its CI, cost, constraint values), `default`, `regret_per_1000fh`,
`constraint_status` (`satisfied`, `infeasible`, `not_applicable`) and `flags`. A lower optimal threshold than default means the airline
should alert earlier at its stated costs. Kinds with no functional failure in the horizon are flagged `no failures observed: cannot
optimise`: only false-alarm cost is shown, no optimum is claimed.

## What the airline must set
`false_alarm_cost` (inspection/removal + NFF shop), `miss_cost` (AOG, delay, cancellation, secondary damage), `lead_value` and
`lead_cap_cycles` (planned vs unplanned saving per cycle of lead), `max_false_alarms_per_1000fh` (inspection capacity),
`min_lead_cycles` (planning lead needed to get a slot). Defaults are illustrative priors, not airline data.

## Caveats
* Circular evaluation: the same simulator generated scorer training data, injected faults and test streams.
* Every unit in the back-test receives the fault, so failure incidence per FH is far above a real fleet; read cost as relative between
  thresholds, not as an absolute budget. Rescale `miss_cost` or fault incidence for fleet-level money.
* Lead time and detection are quantised by the scoring stride; quick mode uses few units (wide intervals, coarse optimum).
* The threshold is applied to the anomaly score only; RUL-based rules and per-component calibration are not swept.
* Real maintenance records are needed before acting on any number here.
