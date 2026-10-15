# Health intelligence (feature 3.1) and the twin state engine (feature 3.2 backend)

Code: `app/ml/` (`sensors.py`, `cmapss.py`, `health.py`, `train.py`, `artifacts/`) and `app/twin/` (`state.py`, `router.py`).
Everything here is trained and evaluated on **NASA C-MAPSS FD001, simulated engine data**, not airline data. See `docs/DATA-SOURCES.md`.

## What it produces

For every component, from its last 30 telemetry rows: `health` (0..1), `anomaly_score` (0..1) and `is_anomaly`, `rul_cycles` (p10 / p50 / p90, in flights), `data_quality` with reasons, and `top_signals`.

## Features (one function for training and serving)

`ml.health.window_features`: for each of the 14 kept sensors, over the last 30 rows (fewer at the start): mean, least-squares slope per cycle, last value; plus the cycle count. A slope needs at least 10 rows; below that it is 0 (a slope from 2 to 9 noisy points is mostly noise, and it made brand-new windows look anomalous).
The same function builds training rows and scores a live window; a test checks the two agree.

## Models

| Part | Method | Notes |
|---|---|---|
| RUL | `RandomForestRegressor`, 100 trees, `min_samples_leaf=5`, target RUL clipped at 125 | Fitted on 85 train engines |
| RUL band | p10 / p50 / p90 of the per-tree predictions, then widened by residual quantiles | Calibrated on 15 other train engines, per band of predicted RUL (below 30, 30 to 60, 60 to 100, above 100). A band is only ever widened. |
| Anomaly | `IsolationForest`, 200 trees, fitted on the first 60% of each fit engine's life | Sensors only (no cycle count). Raw score mapped to 0..1 with the 1st and 99th percentile of the calibration engines; threshold maximises F1 on the calibration engines. |
| Non-engine components | Rule: wear signal against a limit, plus an `IsolationForest` on (level, slope) | **Rule-based demo models** with synthetic limits (`app/ml/sensors.py`, `GENERIC`). Not real limits. |
| Fallback | `rule-v1`: z-scores of T50, Nc, Ps30 against the healthy population | Used only if the artifacts are missing; always `degraded` with the reason. |

### Health score

```
health = clip(0.6 * clip(rul_p50 / 125, 0, 1) + 0.4 * (1 - anomaly_score), 0, 1)
```

### Status (`twin/state.py: status_from`)

`alert` if `is_anomaly` or `rul_p10 < 30`; `watch` if `rul_p10 < 60` or `health < 0.5`; otherwise `ok`. `in_maintenance` and `aog` are set only by plan execution and scenario events.

### Data quality

| Result | When |
|---|---|
| `degraded` | window shorter than 30 rows; a sensor flat over the window; a value more than 10% outside the training range; a missing sensor (imputed); rule fallback in use |
| `bad` | more than half the sensors missing in the latest row. The twin keeps the last good numbers and flags the quality. |

## Results (held-out test engines, from `artifacts/eval.json`)

The 100 test engines were never used to fit or calibrate anything, and they are the engines the simulator replays. Numbers are from the committed artifacts; regenerate with `python -m app.ml.train`.

| Metric | Value | Definition |
|---|---|---|
| RUL MAE / RMSE | 10.84 / 14.39 cycles | Last window of each of 100 engines against NASA's `RUL_FD001.txt` (unclipped) |
| Same, against truth clipped at 125 | 9.77 / 13.47 cycles | The training target |
| RUL MAE, late life | 3.8 cycles | Every full window with true RUL <= 30 (332 windows) |
| Bias | +1.54 cycles | Slight over-estimate of remaining life on average |
| NASA score | 355.1 | Asymmetric score from the C-MAPSS paper; lower is better |
| [p10, p90] covers the truth | 89% last window, 93% all windows | Nominal 80%. Tree spread alone: 75% and 84%. |
| Mean band width | 38.4 cycles | Tree spread alone: 25.9 |
| Anomaly precision / recall | 0.79 / 0.72 | Every full test window (10,196), label "true RUL <= 30" (332 positives) |
| Anomaly false-positive rate | 0.63% | Same windows |
| Anomaly, last window only | precision 0.95, recall 0.80, FPR 1.3% | 100 windows, so noisy |

## Limits to state out loud

- Trained and tested on C-MAPSS FD001 (one operating condition, one fault mode). Not validated on airline data, other operating conditions or other fault modes.
- The RUL band is tree spread widened by measured residuals. It covers more than the nominal 80% in aggregate, which is deliberate (the scheduler uses p10 as a hard deadline), but it is not a guaranteed interval for any single engine.
- Individual engines can be off: the last-window RUL error has a typical size of about 11 cycles. The model's error is smaller near end of life (3.8 cycles) than early. All figures are on simulated C-MAPSS engines.
- Anomaly recall of 0.72 means about 3 in 10 late-life windows are not flagged by the anomaly model alone; the `rul_p10 < 30` rule in `status_from` also raises `alert`.
- Brakes, landing gear, hydraulics and avionics use rule-based demo models with synthetic limits.
- `HOURS_PER_CYCLE = 2.0` in `twin/state.py` is an assumption (the simulator flies 2 h legs) that fills component hours from cycles.
- The RUL model's `cycle` feature is engine age since the run began. The twin rebases each engine's lifetime counter to that (`_Comp.cycle0`) before scoring; without it a 5,000-cycle engine looked old to the model.
- The non-engine models flag a level near the limit or a wear rate far above the typical rate for the kind (`GenericSpec.life_cycles`); a part that is simply half worn is not an anomaly. With fewer than 10 rows the slope is blended with that typical rate.

## Process disclosure

- Hyperparameters were first set to the work package's values (200 trees, leaf 3). The file was 15.8 MB, so four configurations were compared and 100 trees with leaf 5 (5.3 MB) was kept. The comparison was looked at on the test metrics, which differed by at most 0.3 cycles of MAE across all four; the choice was made for file size, not for the metric.
- A bug where two-row windows produced a false anomaly was found by tests and fixed in the feature function (slope needs 10 rows), then the models were retrained. The anomaly threshold was chosen on calibration engines only.
- Correction: an early note expected the tree spread to be too narrow; measurement shows it is close to nominal in aggregate, and the widening makes it conservative.

## For the other modules

| Who | What to know |
|---|---|
| Simulator (Aadhavan) | Engine telemetry keys are `ml.sensors.SENSOR_NAMES`. Other components send `ml.sensors.TELEMETRY_KEYS[kind]` (e.g. brakes: `pad_wear_mm`, `disc_temp_c`). `TelemetryRow.cycle` is cumulative cycles of that component. To start a component mid-life, feed its previous 30 rows with `ingest_telemetry(row, backfill=True)` so the window is full and no timeline events fire. After `apply_maintenance`, restart the component's series at cycle 1 on a fresh unit. |
| Scheduler (Pardhu) | `snapshot()` returns `FleetState` with `rul_cycles.p10` and `last_maintenance` on every `ComponentState`. `snapshot().now` is the SimClock if `set_now_provider(clock.now)` is called, else the newest telemetry timestamp. Legs and stations come from `set_ops_provider(fn)`. An empty twin returns an empty state, never another fleet; `twin.state.is_empty()` tells you. See `docs/COPILOT.md` for the one-line change to `app/solver/service.py` that keeps your tests and source labels correct. |
| API (Shamit) | `app.twin.router.router` serves `GET /aircraft/{tail}`, `GET /aircraft/{tail}/telemetry` and `GET /metrics/ml` (the ML block of `/metrics`). Call `app.twin.state.reset()` and `app.copilot.reset_all()` on `POST /scenario/reset`. The copilot endpoints are in `app.copilot.router` (`docs/COPILOT.md`). |
