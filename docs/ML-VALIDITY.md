# ML validity: detection back-test and model audit

Status: SIMULATED data throughout. Code: `app/ml/backtest.py`, `app/ml/validity.py`, runner `scripts/run_ml_validity.py`.
Outputs (gitignored): `out/ml-validity/{backtest,validity}.{json,md}` and `calibration_<kind>_{test,shift}.svg`.

> **Circularity caveat, stated once and repeated in every output file.** The same simulator (`app.sim.v2`) generated the
> training data, the injected faults and the test streams. Nothing here is field performance. These checks show whether the
> sim-trained models are *internally* sound (no leakage, calibrated where they should be, better than the rule model on the
> same simulated rows) and how the scorers behave on *simulated* faults. They cannot show that real aircraft behave like the simulator.

## How to run

```
python scripts/run_ml_validity.py --quick   # tiny training, 2 fault modes, about 1 minute total (this is what the tests run)
python scripts/run_ml_validity.py           # full: default training config (the shipped artifacts' config) + all 19 fault modes, 2 seeds
python scripts/run_ml_validity.py --only backtest | --only validity
```

Full run on an 8 core laptop: roughly 20 minutes each for the back-test and the validity pack (single threaded; set
`OMP_NUM_THREADS=1` when running them side by side). The validity pack retrains with `train_generic.default_config()`,
so its "full" row reproduces the shipped `generic_meta.json` numbers (checked for tires: MAE 12.8, CQR coverage 0.83).

## 1. Detection back-test (`app/ml/backtest.py`)

Method.

* One fault-free *control* world per seed and one *faulted* world per (seed, mode); same seed, tails and flight profiles. Every
  unit of the mode's kind receives the fault at `onset = 150 + (7 * unit) mod 41` (known per unit, never read by a scorer).
  Severity then follows the mode's own ramp from `app.sim.v2.faults.LIBRARY`.
* Functional failure: first cycle at/after onset where the unit is `failed` or its health index is at/below
  `train_generic.EOL_HI` (0.15), the end-of-life definition the sim-trained model was trained on. A unit counts for lead
  time only if its control twin was not already at end of life (the failure is then attributable to the fault).
* Scorers: the real `health.score_component`, fed the last 30 rows of `TELEMETRY_KEYS[kind]` every 5th cycle. `rule` is the
  default demo model, `simtrained` is `HEALTH_MODEL=simtrained` with the shipped artifacts, `engine_model` is the CMAPSS-trained
  engine scorer (one scorer for engines, no flag). The **alert** is the scorer's own `HealthScore.is_anomaly`.
* Metrics: detection delay (first alert at/after onset minus onset), lead time (failure minus first alert, in cycles and flight
  hours; distribution p10/p50/p90 over units), window precision/recall (positive = injected severity >= 0.1; negatives =
  pre-fault windows plus all control windows), false alarms per 1000 FH (alert episodes = rising edges of the alert stream on the
  matched fault-free units, until their own end of life).
* Lead time and delay are quantised to the 5 cycle scoring stride. Units: 12 per mode (2 seeds x 6 tails; engines 24).

### Headline results (full run, 2 seeds, 6 tails)

Per kind, pooled over that kind's fault modes. Lead time is defined only for units that actually reach functional failure
inside the horizon; "-" means none did (then there is no lead time, only delay).

| kind | scorer | units failed / total | recall before failure | lead p10 / p50 / p90 (cycles) | lead p50 (FH) | delay p50 (cycles) | window precision | window recall | false alarms / 1000 FH |
|---|---|---|---|---|---|---|---|---|---|
| apu | simtrained | 12 / 24 | 1.00 | 130 / 133 / 135 | 266 | 40 | 1.00 | 0.18 | 0.00 |
| avionics | simtrained | 5 / 24 | 1.00 | 60 / 172 / 275 | 344 | 28 | 0.63 | 0.63 | 4.70 |
| brakes | simtrained | 19 / 24 | 1.00 | 60 / 102 / 354 | 204 | 12 | 1.00 | 0.97 | 0.00 |
| ecs | simtrained | 0 / 24 | - | - | - | 134 | 0.88 | 0.78 | 2.88 |
| electrical | simtrained | 4 / 24 | 1.00 | 214 / 296 / 359 | 591 | 181 | 0.98 | 0.13 | 0.15 |
| hydraulics | simtrained | 0 / 24 | - | - | - | 96 | 0.49 | 0.93 | 1.69 |
| landing_gear | simtrained | 0 / 24 | - | - | - | 278 | 0.72 | 0.31 | 4.39 |
| tires | simtrained | 19 / 24 | 0.89 | 3 / 13 / 41 | 26 | 32 | 0.38 | 0.37 | 2.86 |
| engine | engine_model | 0 / 48 | - | - | - | 813 | 1.00 | 0.01 | 0.00 |
| all 7 non-engine kinds | **rule** | 59 failed | **0.00** | - | - | - | - | 0.00 | 0.00 |

Per fault mode rows are in `out/ml-validity/backtest.md`. Ones worth knowing:

* `stuck_brake`: lead p50 347 cycles (694 FH), no false alarms; `rejected_takeoff_overheat`: 80 cycles, delay 8.
* `egt_overtemp` (apu): lead 130-135 cycles, but only 0.73 window recall; `starter_failure` is never alerted (0 recall).
* `slow_leak` (tires): lead p50 18 cycles; `fod_cut`: p50 7 cycles, 18% of failures not alerted before failure, window precision 0.32-0.42.
* `idg_bearing` (electrical): window recall 0.00; `cell_short`: lead p50 296 cycles but the first alert comes 184 cycles after onset.

### What the numbers say, including the unflattering ones

* **The rule model never alerted** on any injected fault in any kind (0 of 59 attributable failures; also 0 false alarms).
  Its `is_anomaly` needs anomaly >= 0.75 on an IsolationForest of (level, slope) built for slow monotone wear; injected faults
  move the channels in ways that never push that score over the line before end of life. As an alerting system on these
  faults the rule model is, in effect, off. It still produces health and RUL numbers.
* **Sim-trained lead time is large for some kinds (brakes, apu, avionics, electrical) and small for others.** Tires give a
  median of only 13 cycles (26 FH) with 0.38 window precision. Where it is large the sim-trained alert tracks a
  slowly drifting wear channel that the fault accelerates, so part of "lead" is simply early flagging of any worn unit.
* **False alarms are not negligible**: avionics 4.7, landing gear 4.4, tires 2.9, ECS 2.9 per 1000 FH on fault-free twins.
  (Illustration only: a 24 tail fleet at 6 legs/day of about 2 FH each flies about 300 FH per day, so 3 per 1000 FH is
  roughly one false alarm per day fleet-wide, per kind.)
* **Hydraulics and landing gear: no functional failures in the horizon**, so no lead time can be computed. They alert
  readily (recall 0.93 / 0.31) but at precision 0.49 / 0.72.
* **Engines**: 0 of 48 faulted engines reached end of life inside 2200 cycles, so lead time is undefined; the
  CMAPSS-trained scorer alerted on essentially nothing (window recall 0.00 to 0.05), first alert after ~800-1200 cycles. This
  is a coverage gap of the back-test (engine life in v2 is far longer than the horizon) and of the engine scorer.

## 2. Validity pack (`app/ml/validity.py`)

All tables below come from one full run (`default_config`, 8 kinds). `cov80` is the CQR 80% interval coverage; "below cap" means
rows whose true RUL is under the 400 cycle cap.

### 2a. Leakage audit

Retrain the p10/p50/p90 + CQR stack with features removed and compare test MAE (cycles). Variants: `no_age` drops the
age feature; `no_wear_channel` drops the six statistics of the kind's wear-signal channel; `extras_only` keeps only the
extras channels; `wear_channel_only` and `age_only` are the converse. dMAE is vs the full model, in-distribution test.

| kind | full MAE | no_age dMAE | no_wear_channel dMAE | extras_only dMAE | wear_channel_only dMAE | age_only dMAE |
|---|---|---|---|---|---|---|
| brakes | 20.1 | +2.1 | +32.5 | +38.6 | +2.6 | +35.1 |
| landing_gear | 10.9 | -0.3 | +2.4 | +2.7 | -0.4 | +2.0 |
| hydraulics | 10.5 | +0.3 | +53.6 | +67.1 | -0.5 | +46.8 |
| avionics | 21.5 | -1.4 | +41.5 | +42.0 | -4.3 | +40.3 |
| apu | 32.4 | 0.0 | +2.4 | +8.9 | -0.6 | +83.7 |
| tires | 12.8 | +1.8 | +14.2 | +43.4 | +1.9 | +14.1 |
| ecs | 7.8 | -1.3 | +4.6 | +3.0 | +3.9 | +4.2 |
| electrical | 15.3 | +8.6 | +30.9 | +32.0 | +10.7 | +74.4 |

Reading. The prediction is carried by the **wear-signal channel**, not by the age feature: removing age changes MAE by
a few cycles at most (largest: electrical +8.6), removing the wear channel costs 2 to 54 cycles. The wear channel alone is
within 3 cycles of the full feature set for brakes (+2.6), tires (+1.9), hydraulics, apu and landing gear, and beats it on
avionics (-4.3), i.e. the other channels and age add little or add noise there.
Removing the wear channel does not leave a useless model for ECS and landing gear (dMAE 4.6 / 2.4): their RUL is mostly
at the cap (93% of rows), so a near-constant prediction is already good.

Label determinism (train rows; binned R2 is the R2 of the best piecewise constant function of `<wear>.last`):

| kind | wear channel | corr(hi, wear) | R2 hi given wear | R2 RUL given wear | R2 RUL given age |
|---|---|---|---|---|---|
| brakes | pad_wear_mm | -1.00 | 1.00 | 0.84 | 0.40 |
| landing_gear | strut_pressure_kpa | 0.92 | 0.85 | 0.59 | 0.25 |
| hydraulics | pump_pressure_kpa | 0.99 | 1.00 | 0.83 | 0.17 |
| avionics | error_rate_per_khr | -1.00 | 1.00 | 0.56 | 0.07 |
| apu | apu_egt_margin_c | 1.00 | 1.00 | 0.74 | 0.19 |
| tires | tread_depth_mm | 0.97 | 0.95 | 0.83 | 0.67 |
| ecs | pack_outlet_temp_c | -0.95 | 0.92 | 0.33 | 0.17 |
| electrical | battery_capacity_pct | 1.00 | 0.99 | 0.70 | 0.17 |

**Finding (unflattering): for 5 of 8 kinds (brakes, hydraulics, avionics, apu, electrical) the health-index target is
(nearly) a deterministic function of one input feature** (R2 >= 0.99), because the simulator defines the health index from
the same latent wear that drives the wear channel. The health regressor there mostly re-reads its input; its low `health
MAE` (0.002 to 0.011) is not evidence of prognostic skill. RUL is not deterministic given the wear channel (R2 0.33 to 0.84),
so the RUL models do have real work left to do.

### 2b. Split audit

Asserted programmatically (`assert_disjoint`) on the samples the trainer actually produced: train, calibration, test and
shifted partitions share no unit key and no seed, for every kind. Four schemes are compared with the same model class
(MAE of p50 RUL, in cycles):

| kind | seed-grouped (trainer) | unit-grouped (worlds shared) | temporal (early train, late test) | row-random (LEAKY) |
|---|---|---|---|---|
| brakes | 20.1 | 11.4 | 63.9 | 12.6 |
| landing_gear | 10.9 | 7.9 | 25.9 | 6.5 |
| hydraulics | 10.5 | 10.8 | 32.9 | 7.9 |
| avionics | 21.5 | 19.9 | 52.9 | 16.4 |
| apu | 32.4 | 31.0 | 78.2 | 21.5 |
| tires | 12.8 | 15.0 | 13.4 | 13.2 |
| ecs | 7.8 | 6.2 | 27.1 | 6.6 |
| electrical | 15.3 | 17.9 | 36.3 | 17.8 |

The leaky row split is more optimistic than the trainer's split for 6 of 8 kinds (tires and electrical: no gain) (apu 21.5 vs 32.4), the audit flags it
(`units disjoint = False`). Seed-grouped is the stricter and safer choice, as shipped. The temporal split is much worse
(2.4 to 3.5x) for 6 of 8 kinds (tires: about equal): the test rows are late-life windows of held-out units and the training rows only the early part,
so it mixes extrapolation in time with a different RUL distribution; read it as a pessimistic bound, not a like-for-like score.
The "unit-grouped" scheme is sometimes better than the seed split, which says that whole-world effects (a world's weather and
stress draws) are a real part of the seed-to-seed error, not that unit grouping is enough.

### 2c. Calibration

Observed frequency of `y <= q` for the raw quantile regressors and the CQR band (nominal 0.10 / 0.50 / 0.90), in-distribution test:

| kind | raw p10 | raw p50 | raw p90 | cqr p10 | cqr p90 | cov80 cqr | cov80 raw |
|---|---|---|---|---|---|---|---|
| brakes | 0.09 | 0.81 | 0.97 | 0.09 | 0.97 | 0.88 | 0.88 |
| landing_gear | 0.77 | 0.96 | 0.99 | 0.77 | 0.99 | 0.93 | 0.93 |
| hydraulics | 0.08 | 0.81 | 0.96 | 0.08 | 0.96 | 0.89 | 0.89 |
| avionics | 0.07 | 0.73 | 0.98 | 0.07 | 0.98 | 0.92 | 0.92 |
| apu | 0.21 | 0.77 | 0.97 | 0.19 | 0.97 | 0.79 | 0.76 |
| tires | 0.13 | 0.54 | 0.93 | 0.11 | 0.94 | 0.83 | 0.81 |
| ecs | 0.78 | 0.90 | 0.96 | 0.78 | 0.96 | 0.95 | 0.95 |
| electrical | 0.08 | 0.34 | 0.95 | 0.08 | 0.95 | 0.86 | 0.86 |

Caveat on reading this: the target is censored at 400 cycles and 45 to 93% of test rows sit at the cap for seven kinds (tires: none), so
`y <= q` is trivially true for many rows (p50 observed 0.73 to 0.96 for the kinds with many cap rows is a cap artefact, not a bias). The band is wider than
nominal marginally (cov80 0.79 to 0.95) because of the cap mass.

**Conditional coverage is poor.** Marginal coverage hides very uneven coverage by true RUL (CQR 80% band, test):

| kind | RUL 0-25 | 25-50 | 50-100 | 100-200 | 200-400 | at cap |
|---|---|---|---|---|---|---|
| brakes | 0.68 | 0.87 | 0.80 | 0.60 | 0.60 | 0.98 |
| landing_gear | **0.09** | **0.22** | **0.31** | **0.22** | **0.14** | 0.99 |
| hydraulics | 0.83 | 0.86 | 0.79 | 0.67 | 0.61 | 0.98 |
| avionics | 0.72 | 0.79 | 0.73 | 0.76 | 0.79 | 0.98 |
| apu | 0.91 | 0.82 | 0.68 | 0.61 | 0.57 | 0.97 |
| tires | 0.76 | 0.77 | 0.80 | 0.87 | 0.86 | - |
| ecs | **0.18** | 0.66 | 0.68 | 0.74 | 0.85 | 0.96 |
| electrical | 0.66 | 0.74 | 0.80 | 0.77 | 0.76 | 0.96 |

For landing gear the 93% marginal coverage is entirely the at-cap rows; the intervals cover 9 to 31% of units that really
are within 400 cycles of end of life. Treat the interval as meaningful for "is this far from end of life", not for sizing
the remaining life of an ageing unit. Under the held-out stress regime marginal coverage falls further (hydraulics 0.39,
tires 0.57, apu 0.64); see 2d. SVGs: `out/ml-validity/calibration_<kind>_{test,shift}.svg`.

### 2d. Performance by regime

| kind | in-distribution MAE / cov80 | held-out stress regime MAE / cov80 | ageing fleet MAE / cov80 |
|---|---|---|---|
| brakes | 20.1 / 0.88 | 29.0 / 0.84 | 58.9 / 0.73 |
| landing_gear | 10.9 / 0.93 | 10.4 / 0.90 | 53.8 / 0.59 |
| hydraulics | 10.5 / 0.89 | 63.2 / 0.39 | 35.0 / 0.79 |
| avionics | 21.5 / 0.92 | 45.2 / 0.82 | 70.7 / 0.57 |
| apu | 32.4 / 0.79 | 59.9 / 0.64 | 72.6 / 0.65 |
| tires | 12.8 / 0.83 | 19.6 / 0.57 | 17.5 / 0.64 |
| ecs | 7.8 / 0.95 | 20.6 / 0.89 | 58.7 / 0.72 |
| electrical | 15.3 / 0.86 | 67.0 / 0.68 | 90.3 / 0.59 |

"Held-out stress regime" = seeds with hotter, heavier, dustier, higher-utilisation operation and per-unit stress 3.5 to 5.5
(training 1 to 3). "Ageing fleet" = units started 50% worn via `World.pre_age`; their age counter starts at 0, so the
age feature is misleading there by construction. Every kind degrades out of distribution; the median degradation is about
2x MAE under the stress shift and 3x for the ageing fleet, with coverage dropping well below 0.8 (down to 0.39). The
conformal guarantee holds only under exchangeability with the calibration seeds, which these regimes violate.

### 2e. Rule vs sim-trained (paired, unit-clustered bootstrap)

Same rows, MAE of p50 RUL; difference = rule - sim (positive: sim better), 95% CI from 1000 resamples of whole units.

| kind | in-distribution | held-out stress regime | ageing fleet |
|---|---|---|---|
| brakes | +16.6 [9.1, 23.9] sim | +24.2 [16.6, 31.3] sim | +67.8 [39.8, 97.2] sim |
| landing_gear | -0.5 [-3.1, 2.4] tie | +9.5 [3.9, 14.8] sim | -5.2 [-18.5, 10.3] tie |
| hydraulics | +27.0 [20.7, 32.9] sim | -1.1 [-15.8, 13.2] tie | +87.1 [79.9, 96.0] sim |
| avionics | +15.4 [8.9, 21.1] sim | +7.0 [-1.7, 16.4] tie | +18.4 [-10.4, 53.7] tie |
| apu | +29.6 [19.5, 38.7] sim | -7.4 [-13.7, -1.3] **rule** | +53.7 [34.6, 74.0] sim |
| tires | +49.1 [42.3, 55.7] sim | +50.3 [40.4, 60.7] sim | +46.0 [35.2, 56.9] sim |
| ecs | +4.9 [1.0, 9.4] sim | +8.4 [3.8, 14.3] sim | -13.0 [-30.1, 2.6] tie |
| electrical | +39.4 [30.5, 48.0] sim | -3.4 [-9.7, 3.6] tie | +26.3 [0.5, 58.5] sim |

Sim-trained is significantly better in 16 of 24 cells, indistinguishable in 7, and worse in 1 (apu under stress shift).
The rows are at most 300 per cell, evenly subsampled from the set. In-distribution is the favourable comparison (the
sim-trained model was fitted to exactly this generator); the out-of-distribution columns are where the rule model's
simple physics-flavoured extrapolation catches up.

## 3. What this does and does not show

Shows.

* The train/calibration/test partitions are unit- and seed-disjoint, and the audit would catch a leak (the deliberately
  leaky row split is flagged and is visibly more optimistic).
* The sim-trained RUL models are driven by the wear channel, not by the age counter, and they beat the rule model
  in-distribution on 7 of 8 kinds with a CI that excludes zero (landing gear ties).
* The CQR band is roughly calibrated in aggregate in-distribution (0.79 to 0.95 coverage at nominal 0.80).
* On simulated faults the sim-trained alert fires before functional failure for every kind where failures occur
  (recall 0.89 to 1.00), with lead times from about 13 cycles (tires) to about 300 cycles (electrical).

Does not show.

* **Real-world accuracy.** The simulator produced train, calibration and test data and defines the faults the back-test
  injects; the sim-trained model may be recognising the simulator's fault signatures, not a physical phenomenon. Lead
  times and false-alarm rates should be read as properties of this simulator-plus-model pair.
* **That the labels are independent of the features.** For 5 of 8 kinds the health index is a deterministic function of the
  wear channel; health MAE is not a measure of skill there.
* **Conditional calibration.** Marginal 80% coverage hides poor coverage in the low-RUL bins, the regime that matters most
  operationally (landing gear 0.09 to 0.31, ECS 0.18 in the lowest bin).
* **Robustness out of distribution.** Coverage falls to 0.39 to 0.64 for some kinds under the stress shift; the model has
  no way to know it is outside its training envelope except the "outside the simulated training range" data-quality flag.
* **Engines.** No engine reached end of life inside the back-test horizon, so there is no engine lead time; the engine
  scorer's alerting behaviour on injected faults was weak (window recall <= 0.05).
* **Hydraulics, landing gear, ECS lead time.** Same reason: no functional failures in the horizon.
* **Statistical power.** 12 units per mode, 2 seeds. Lead time distributions (p10/p90) for modes with 4 to 8 failed units
  are indicative only; several per-mode cells have a handful of failures. Stride quantisation is 5 cycles.
* **Alerting policy.** The alert is the scorers' own flag at its own threshold. Different thresholds trade lead time against
  false alarms; no ROC sweep is run here.
* **Rule model negative result is about its alert flag**, not its health / RUL outputs, which 2e evaluates separately.

Tests: `tests/test_backtest.py`, `tests/test_ml_validity.py` (pure metric math on hand-built inputs, a quick end-to-end run
on two fault modes and on tires, schema and determinism). Quick mode is deliberately tiny and its numbers are not meaningful.
