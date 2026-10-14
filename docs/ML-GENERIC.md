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
  Note `stress_ext` has little or no effect on some kinds in the v2 models (brakes, landing gear, ECS), so those are
  mostly shifted by the operating profile.
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
Rule model figures are on up to 300 evenly spaced windows per set (its estimate is clipped at the cap); the
sim-trained figures are on the full hold-out. "At cap" is the share of rows whose true RUL is the cap (>= 400),
where predicting a large number is easy; the RUL<cap columns are the harder rows.

### In-distribution hold-out units (unseen seeds, same regime)
| kind | rows / units | at cap | MAE (all) | MAE (RUL<cap) | rule MAE (all) | p10-p90 raw | conformal (CQR) | CQR, RUL<cap rows | rule cov. | anomaly AUC | rule AUC |
|---|---|---|---|---|---|---|---|---|---|---|---|
| apu | 7794 / 31 | 53% | 21.6 | 41.2 | 54.8 | 86% | 86% | 72% | 52% | 0.68 | 0.44 |
| avionics | 17108 / 32 | 63% | 55.4 | 103.2 | 67.3 | 89% | 89% | 76% | 62% | 0.93 | 0.45 |
| brakes | 9306 / 30 | 65% | 25.2 | 65.3 | 47.5 | 87% | 87% | 68% | 67% | 0.99 | 0.52 |
| ecs | 27163 / 32 | 93% | 8.8 | 71.3 | 14.3 | 95% | 95% | 72% | 93% | 0.91 | 0.58 |
| electrical | 7709 / 29 | 55% | 16.9 | 33.5 | 49.4 | 84% | 84% | 70% | 69% | 0.74 | 0.55 |
| hydraulics | 9163 / 30 | 65% | 11.5 | 26.2 | 42.1 | 86% | 86% | 72% | 66% | 0.77 | 0.78 |
| landing_gear | 28427 / 32 | 96% | 3.8 | 68.7 | 6.1 | 97% | 97% | 31% | 96% | 0.76 | 0.32 |
| tires | 3139 / 32 | 0% | 36.9 | 36.9 | 124.3 | 65% | 78% | 78% | 10% | 0.74 | 0.52 |

### Held-out stress regime (hot, heavy, dusty, high utilisation, higher stress)
| kind | rows / units | at cap | MAE (all) | MAE (RUL<cap) | rule MAE (all) | p10-p90 raw | conformal (CQR) | CQR, RUL<cap rows | rule cov. | anomaly AUC | rule AUC |
|---|---|---|---|---|---|---|---|---|---|---|---|
| apu | 8246 / 32 | 7% | 69.4 | 74.3 | 60.5 | 76% | 76% | 74% | 7% | 0.66 | 0.62 |
| avionics | 20642 / 32 | 53% | 92.1 | 95.3 | 87.8 | 86% | 86% | 80% | 54% | 0.77 | 0.44 |
| brakes | 5510 / 29 | 45% | 33.6 | 56.8 | 66.9 | 80% | 80% | 67% | 46% | 0.95 | 0.93 |
| ecs | 21616 / 32 | 87% | 19.4 | 141.7 | 30.5 | 92% | 92% | 44% | 86% | 0.88 | 0.61 |
| electrical | 9357 / 32 | 0% | 95.5 | 95.5 | 67.2 | 53% | 53% | 53% | 13% | 0.84 | 0.79 |
| hydraulics | 5931 / 31 | 2% | 73.8 | 75.2 | 69.9 | 47% | 47% | 46% | 2% | 0.64 | 0.63 |
| landing_gear | 23750 / 32 | 87% | 10.9 | 55.4 | 18.8 | 92% | 92% | 56% | 89% | 0.75 | 0.52 |
| tires | 2939 / 32 | 0% | 26.0 | 26.0 | 115.2 | 63% | 81% | 81% | 7% | 0.64 | 0.57 |

Matched-row comparisons (sim-trained scored on exactly the rows the rule model was scored on) are in
`generic_meta.json` under `rule_model.simtrained_same_rows`.

## Honest reading
* In distribution the sim-trained models have a lower RUL MAE than the rule model on all 8 kinds (clearly for apu, brakes,
  electrical, hydraulics, tires; narrowly for avionics, ecs, landing_gear, where most rows sit at the cap). The rule model's
  fixed limits and linear wear do not match the v2 degradation, so this says as much about the demo rule as about the learned model.
* Headline coverage is flattered by rows at the cap (easy to cover). On rows with RUL below the cap, CQR coverage in
  distribution is 68-78% for 7 kinds and **31% for landing_gear** (a model that says "cap" for a part that is in fact
  failing within 400 cycles is confidently wrong, and the interval does not widen enough).
* Under the stress shift the picture is worse: coverage on below-cap rows falls to 44-56% for ecs, electrical, hydraulics
  and landing_gear, and for apu, avionics, electrical and hydraulics the rule model has the lower MAE. Conformal calibration
  on in-distribution units does not repair covariate shift. Do not trust the 80% label outside the training regime.
* Anomaly detection is a supervised fault-present classifier; AUC 0.68-0.99 in distribution, 0.64-0.95 under shift.
  Thresholds (max F1 on calibration seeds) give high false-positive rates for landing_gear and ecs in distribution (11-43%)
  and for brakes, ecs, landing_gear and apu under shift (34-78%). The rule model's anomaly score is near chance on these labels
  (it was not designed to detect them), so that AUC comparison is not a fair test of the rule model.
* Health regression error is tiny (MAE < 0.04) because health is a smooth function of the wear signal.
* About 30 hold-out units per kind from 4 seeds; row counts overstate the effective sample size.

## Sim versus real
The only comparison of the simulator with real data available in the repo is `docs/SIM-V2-CALIBRATION.md`
(hydraulics and battery). Nothing in this document tests these models against real component histories, and no
real-world accuracy should be inferred from it.

## Reproduce
The committed artifacts were generated against the v2 simulator as it stood on 2026-10-14 (uncommitted edits to `app/sim/v2/*` were in flight); any change to the simulator changes the data, so regenerate with the command below and re-read the tables.
`python -m app.ml.train_generic` (about 2-3 minutes, writes `app/ml/artifacts/generic_<kind>.joblib` and
`generic_meta.json`; `--tiny` for the small test configuration). Committed artifacts total 1.90 MB (lzma-compressed joblib, 8 files + meta).
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
