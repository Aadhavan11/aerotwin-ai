# Sim-trained health models for the non-engine kinds (ML-GENERIC)

**All data here are SIMULATED.** Every number below compares a model trained on Simulator v2 output with a hold-out
from the *same* simulator. The evaluation is circular: it measures how well a model recovers the simulator's own
degradation laws, not how well it would predict real components. Nothing here is a real-world accuracy claim.

## What it is
* Kinds: brakes, landing_gear, hydraulics, avionics, apu, tires, ecs, electrical. The engine keeps its C-MAPSS model.
* Switch: env `HEALTH_MODEL=simtrained` makes `app.ml.health.score_generic` use these models. Default (unset or
  `rule`) is the rule-based demo model, unchanged. `model_version` is `sim-trained (SIMULATED data)`.
* Fallback: if the window lacks a whole channel, the kind has no artifact, or there are no rows, the rule model scores
  it and `data_quality.reasons` says `sim-trained model not used (...)`. Dropouts of a few rows are tolerated (NaN-aware
  features, gradient boosting handles NaN).
* Explanation: `HealthScore.explanation` (new, optional, additive; see CONTRACTS.md and CONTRACTS-CHANGELOG.md) lists
  the 3 features that move the RUL p50 most (occlusion: p50 with the feature as is minus p50 with that feature at its
  training median; `effect_cycles`). `top_signals` holds the sensor channels of those features.

## Data
* Worlds: `app.sim.v2.World`, 8 tails, one per seed, stochastic sensor faults on. Seeds: train [101, 102, 103, 104, 105, 106, 107, 108],
  calibration [201, 202, 203, 204], test [301, 302, 303, 304], shifted regime [401, 402, 403, 404]. Splits are by seed, so by
  unit serial; no row-level splitting. Seeds [102, 104, 106, 108, 202, 204, 302, 304, 402, 404] run with the maintenance policy
  (restorations, repairs), the others run to failure with `auto_maintain=False`. Run lengths: 3000 cycles to failure,
  1500 with maintenance. Every 2nd cycle is kept as a sample.
* In-distribution regime: per-unit external stress (`stress_ext`) uniform in [1.0, 3.0], utilisation x0.8-1.3,
  ambient offset -8..+8 C. **Held-out stress regime** (shifted): stress [3.5, 5.5], utilisation x1.4-1.8, +18 C,
  +15 pp payload, +10 pp landing weight, brake energy x1.35, sink rate x1.3, extra wet runways, arrivals at dusty stations.
  `stress_ext` multiplies wear in every kind including brakes (mechanical wear), landing gear (seal leak, fatigue, crack growth)
  and ECS (valve hours and event rate); the shift also changes the operating profile (energy, weights, wet runways, dust).
* Channels: only `TELEMETRY_KEYS[kind]` (two per kind: the wear signal and one extra), exactly what the live service
  emits; v2's other channels and the hidden truth are not used. 30-cycle window features per channel: mean, slope, last,
  std, min, max, plus age (cycles since restoration in training; the last row's `cycle` at serving time, which equals it only if
  the telemetry source resets `cycle` on restoration; not verified for the live service).
* Targets: RUL = cycles to end of life (failed or health index <= 0.15), capped at 400; rows
  whose end of life was not observed and whose known horizon is below the cap are dropped (censoring). Health = v2 health
  index. Anomaly label = a ground-truth fault of that unit is active with severity >= 0.1.

## Models and calibration
HistGradientBoosting quantile regressors (p10, p50, p90), a health regressor and a fault classifier per kind
(100 iterations, 15 leaves, fixed seed, deterministic). The p10-p90 band is calibrated with
**split conformal** (`app/ml/conformal.py`) on the calibration seeds, target coverage 80%: CQR (served), absolute
residual and normalised variants are all reported in `generic_meta.json`. The guarantee needs exchangeable
calibration and test points; consecutive rows of one unit are not independent and the shifted regime breaks
exchangeability, so the guarantee holds only approximately in-distribution and not at all under shift (see below).

## Results (hold-out; SIMULATED data; MAE in cycles; target coverage 80%)
**Retrained 2026-10-14** (artifacts written 17:43, `generic_meta.json`, after the phase-1 simulator changes: realistic removal rates,
no-fault-found returns, infant mortality). The simulator changed, so every number below was refreshed and differs from the earlier
tables; do not compare with older copies of this document.
Rule model figures are on up to 300 evenly spaced windows per set (its estimate is clipped at the cap); the
sim-trained figures are on the full hold-out. "At cap" is the share of rows whose true RUL is the cap (>= 400),
where predicting a large number is easy; the RUL<cap columns are the harder rows.

### In-distribution hold-out units (unseen seeds, same regime)
| kind | rows / units | at cap | MAE (all) | MAE (RUL<cap) | rule MAE (all) | p10-p90 raw | conformal (CQR) | CQR, RUL<cap rows | rule cov. | anomaly AUC | rule AUC |
|---|---|---|---|---|---|---|---|---|---|---|---|
| apu | 12159 / 32 | 45% | 32.4 | 53.6 | 61.9 | 76% | 79% | 64% | 44% | 0.71 | 0.48 |
| avionics | 17398 / 32 | 70% | 21.5 | 52.8 | 36.7 | 92% | 92% | 77% | 71% | 0.92 | 0.47 |
| brakes | 14107 / 32 | 69% | 20.1 | 59.3 | 36.2 | 88% | 88% | 65% | 70% | 0.99 | 0.57 |
| ecs | 28045 / 32 | 93% | 7.8 | 59.1 | 12.1 | 95% | 95% | 75% | 94% | 0.92 | 0.50 |
| electrical | 13445 / 32 | 51% | 15.3 | 26.8 | 53.8 | 86% | 86% | 76% | 65% | 0.63 | 0.44 |
| hydraulics | 17202 / 32 | 71% | 10.5 | 31.4 | 37.6 | 89% | 89% | 68% | 72% | 0.80 | 0.59 |
| landing_gear | 26532 / 32 | 93% | 10.9 | 129.2 | 11.0 | 93% | 93% | 19% | 93% | 0.72 | 0.44 |
| tires | 8514 / 31 | 0% | 12.8 | 12.8 | 62.3 | 81% | 83% | 83% | 26% | 0.84 | 0.56 |

### Held-out stress regime (hot, heavy, dusty, high utilisation, higher stress)
| kind | rows / units | at cap | MAE (all) | MAE (RUL<cap) | rule MAE (all) | p10-p90 raw | conformal (CQR) | CQR, RUL<cap rows | rule cov. | anomaly AUC | rule AUC |
|---|---|---|---|---|---|---|---|---|---|---|---|
| apu | 10910 / 32 | 10% | 59.9 | 66.6 | 49.3 | 61% | 64% | 60% | 12% | 0.60 | 0.61 |
| avionics | 19638 / 32 | 66% | 45.2 | 78.0 | 49.5 | 82% | 82% | 62% | 65% | 0.80 | 0.40 |
| brakes | 10002 / 32 | 56% | 29.0 | 58.7 | 54.5 | 84% | 84% | 66% | 57% | 0.97 | 0.73 |
| ecs | 24736 / 32 | 85% | 20.6 | 133.5 | 30.0 | 89% | 89% | 34% | 86% | 0.90 | 0.57 |
| electrical | 11654 / 32 | 1% | 67.0 | 67.4 | 68.0 | 68% | 68% | 68% | 15% | 0.68 | 0.74 |
| hydraulics | 11803 / 32 | 12% | 63.2 | 70.8 | 61.7 | 39% | 39% | 32% | 12% | 0.69 | 0.65 |
| landing_gear | 26101 / 32 | 88% | 10.4 | 56.5 | 19.4 | 90% | 90% | 64% | 90% | 0.71 | 0.40 |
| tires | 11590 / 32 | 0% | 19.6 | 19.6 | 69.3 | 55% | 57% | 57% | 11% | 0.73 | 0.63 |

Matched-row comparisons (sim-trained scored on exactly the rows the rule model was scored on) are in
`generic_meta.json` under `rule_model.simtrained_same_rows`.

## Honest reading
* In distribution the sim-trained models have a lower RUL MAE than the rule model on 7 of 8 kinds (clearly for apu, avionics,
  brakes, electrical, hydraulics, tires; by 4 cycles for ecs, where 93% of rows sit at the cap) and tie on landing_gear (10.9 vs
  11.0, 93% at the cap). The rule model's fixed limits and linear wear do not match the v2 degradation, so this says as much about
  the demo rule as about the learned model.
* Headline coverage is flattered by rows at the cap (easy to cover). On rows with RUL below the cap, CQR coverage in
  distribution is 64-83% for 7 kinds and **19% for landing_gear** (a model that says "cap" for a part that is in fact
  failing within 400 cycles is confidently wrong, and the interval does not widen enough; MAE on its below-cap rows is 129 cycles).
* Under the stress shift the picture is worse: coverage on below-cap rows falls to 34% for ecs and 32% for hydraulics (57-68%
  for the other kinds), overall coverage is 39% for hydraulics and 57% for tires, and for apu and hydraulics the rule model has the
  lower MAE. Conformal calibration on in-distribution units does not repair covariate shift. Do not trust the 80% label outside
  the training regime.
* Anomaly detection is a supervised fault-present classifier; AUC 0.63-0.99 in distribution, 0.60-0.97 under shift
  (apu, electrical and tires are the weakest). Thresholds (max F1 on calibration seeds) give false-positive rates of 16-32% for
  avionics, electrical, hydraulics and landing_gear in distribution, and 20-65% for apu, avionics, brakes, ecs and landing_gear
  under shift. The rule model's anomaly score is near or below chance on these labels in distribution (AUC 0.44-0.59; 0.40-0.74
  under shift; it was not designed to detect them), so that AUC comparison is not a fair test of the rule model.
* Health regression error is small (MAE <= 0.07 in distribution, <= 0.04 shifted) because health is a smooth function of the
  wear signal.
* About 31-32 hold-out units per kind from 4 seeds; row counts overstate the effective sample size.

## Sim versus real
The only comparison of the simulator with real data available in the repo is `docs/SIM-V2-CALIBRATION.md`
(hydraulics and battery). Nothing in this document tests these models against real component histories, and no
real-world accuracy should be inferred from it.

## Reproduce
The committed artifacts were retrained on 2026-10-14 against the v2 simulator after the phase-1 changes (training took about 2 minutes);
any later change to the simulator changes the data, so regenerate with the command below and re-read the tables.
`python -m app.ml.train_generic` (about 2-3 minutes, writes `app/ml/artifacts/generic_<kind>.joblib` and
`generic_meta.json`; `--tiny` for the small test configuration). Committed artifacts total 1.89 MB (lzma-compressed joblib, 8 files + meta).
Tests: `tests/test_ml_generic.py` (tiny configuration, trains in about 20 s), `tests/test_conformal.py`.

## Serving `age` verification (live path)
Training `age` = cycles since the unit was last restored (cycles since the start of the run for a unit never restored).
Serving gets it from the row `cycle` the scorer receives, and that is not the raw source counter: `app/twin/state.py`
rebases each component's window to `row.cycle - cycle0` (`cycle0` = first row seen, cleared by `apply_maintenance`).
Findings (`tests/test_ml_generic_live.py`, both `SIM_ENGINE=v1` and `v2`, driving `Simulator` -> `twin.ingest_telemetry`):
* Fresh / aged unit: age counts 1, 2, 3 ... from the first row the twin saw. **Agrees** with training in both engines.
* After a work-order restoration (`twin.apply_maintenance` then `source.restore`, as `PlatformSimulator.step` does; both
  engines reset the source counter to 0): age restarts at 1. **Agrees**; RUL p50 and health recover for a worn unit.
* Fixed: if the source restarted a series but the twin was not told, the cycle went backwards and the sorted window put the
  stale pre-restoration rows last (age from the old counter). `ingest_telemetry` now starts a new window on a cycle regression.
* Missing channel -> rule model with a `sim-trained model not used (...)` reason; short window -> sim model, `degraded`
  with "window has N of 30 rows". Both exercised through the live path.
* Residual mismatch: units that start the live run already worn (v2 start wear, v1 `wear_profile`) report age from the
  start of observation, as in training (training worlds start new), so a pre-worn unit has a low age with worn channels, a
  combination the models saw only via stress variation. Also the first rows after a restoration use windows of <30 rows
  (training required cycle >= 3).
