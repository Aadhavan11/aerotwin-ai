# Work package 2: Aashirth. Health intelligence (3.1) and the digital twin state engine (3.2 backend)

> **New: Aashirth also owns the telemetry source (`sim/telemetry.py`), `POST /sim/step` and scenario injection (`sim/scenarios.py`, `/scenario/inject`, `/scenario/reset`): spec in `work/01` sections B.4 to B.7. Do them after the L1 stubs above; they sit next to your C-MAPSS loader and twin ingest.**
> Details: [`OWNERSHIP.md`](OWNERSHIP.md).

You build the **brain of the twin**: the models that say how healthy each component is and how long it has left, and the state engine that
keeps every aircraft's components up to date. Everyone downstream (scheduler, agents, UI) reads what you produce.

## What you own

```
app/ml/       cmapss.py  sensors.py  train.py  health.py  artifacts/ (committed, small)  eval.json
app/twin/     state.py  router.py
tests/test_ml.py   docs/HEALTH.md (formulas)   docs/DATA-SOURCES.md (C-MAPSS source + licence)
```

Read: `FEATURES-3.1-3.6.md` (3.1, 3.2), `CONTRACTS.md` sections 1, 2 (health + twin models), 5 (ml + twin interfaces), `INTEGRATION.md` section 4.1.

## Step-by-step (with time boxes)

### 0. First 30 minutes (before 9:00 PM): unblock the others
1. **Write and push `app/ml/sensors.py` first.** Shamit's telemetry source needs it. Map C-MAPSS column -> sensor name, units and a short label:

   | C-MAPSS | name | meaning |
   |---|---|---|
   | s2 | `T24` | LPC outlet temperature |
   | s3 | `T30` | HPC outlet temperature |
   | s4 | `T50` | LPT outlet temperature (the "engine temperature trend" in the demo) |
   | s7 | `P30` | HPC outlet pressure |
   | s8 | `Nf` | fan speed |
   | s9 | `Nc` | core speed |
   | s11 | `Ps30` | HPC static pressure |
   | s12 | `phi` | fuel flow / Ps30 ratio |
   | s13 | `NRf` | corrected fan speed |
   | s14 | `NRc` | corrected core speed |
   | s15 | `BPR` | bypass ratio |
   | s17 | `htBleed` | bleed enthalpy |
   | s20 | `W31` | HPT coolant bleed |
   | s21 | `W32` | LPT coolant bleed |

   (Names follow the NASA readme. FD001 has some constant sensors, commonly s1, s5, s10, s16, s18, s19 and a near-constant s6: **check the data and
   keep only informative ones**. Operating settings op1 to op3 are ignored for FD001.)
2. **Get the data (20 min cap).** Files: `train_FD001.txt`, `test_FD001.txt`, `RUL_FD001.txt` (space-separated, 26 columns: unit, cycle, op1-3, s1-s21).
   Possible sources (from search results, unverified): the NASA prognostics data repository, IEEE DataPort "NASA Turbofan Jet Engine Data Set", and
   Kaggle/Hugging Face mirrors (e.g. a Hugging Face dataset named `SoyVitou/NASA-C-MAPSS-Turbofan-Engine` appeared in a search). Put the files in `data/cmapss/`
   (gitignored). Record the exact source URL and licence in `docs/DATA-SOURCES.md`. **If you cannot get them in 20 minutes, switch to the fallback:**
   `synthetic_trajectories(seed)` generating C-MAPSS-shaped run-to-failure data (100 engines, 150 to 350 cycles; sensors drift exponentially toward failure
   with noise, e.g. `x(t) = base + a * exp(b * t / T_fail) + noise`). Say so in the README: "synthetic degradation data, C-MAPSS-shaped".

### 1. `ml/cmapss.py` (9:00 to 9:30 PM)
- `load_train()` / `load_test()` -> pandas DataFrames with named columns; `rul_train = max_cycle(unit) - cycle`, **clipped at 125** (standard practice).
- `trajectories() -> dict[int, list[dict]]`: for each unit, ordered rows `{cycle, values: {T24:..., ...}, true_rul}`. **Shamit's simulator replays these**,
  so keep keys exactly the sensor names above. Deterministic ordering.
- `make data` entry point: `python -m app.ml.cmapss --download` (or print where to put the files).

### 2. L1 stubs (by 10:00 PM) so the others can integrate
- `ml/health.py: score_engine(...)` returns a valid `HealthScore` from a simple rule: z-score of `T50`, `Nc` and `Ps30` against first-30-cycle means;
  `rul_cycles` from a linear map of health; `data_quality` ok. `score_generic(...)` same idea with one wear signal.
- `twin/state.py` L1: in-memory dict of `ComponentState`; `ingest_telemetry`, `get_twin`, `snapshot` working on fixtures; `status_from()` implemented
  exactly as `INTEGRATION.md` section 4.1. Merge.

### 3. RUL model (9:45 to 11:00 PM)
- **Features** per (unit, cycle): for the last 30 cycles of each kept sensor: mean, slope (least squares), last value; plus the cycle number. Windows shorter
  than 30 use what exists (and set `data_quality: degraded`). Do not leak: fit any scaler on train only.
- **Model:** `RandomForestRegressor(n_estimators=200, min_samples_leaf=3, n_jobs=-1, random_state=SEED)` on the clipped RUL. (XGBoost is an alternative;
  try only if time.)
- **Quantiles:** predict with every tree (`[t.predict(X) for t in rf.estimators_]`) and take the 10th/50th/90th percentile -> `RulEstimate`. State in
  `HEALTH.md` that this is a spread of tree predictions (an approximation of uncertainty), not a calibrated interval.
- **Evaluation:** for each test unit take the **last window** and compare with `RUL_FD001.txt`. Report **MAE and RMSE** (and optionally the NASA score).
  Write `app/ml/artifacts/eval.json`. Report whatever you get; do not tune on the test set.
- Save with `joblib.dump` to `app/ml/artifacts/rul_rf.joblib` (small) and commit it.

### 4. Anomaly model (11:00 to 11:45 PM)
- `IsolationForest(n_estimators=200, contamination="auto", random_state=SEED)` fitted on windows from the **healthy phase** (first 60% of each unit's life).
- Normalize the raw score to 0..1 (min-max on the validation set); choose the `is_anomaly` threshold on a validation split to balance precision and recall
  against the label "true RUL <= 30 cycles". Report **precision, recall and false-positive rate** on the test windows -> `eval.json`.
- `top_signals`: for the window, the 3 sensors with the largest |z-score| times the RF's feature importance.

### 5. Health score and data quality (11:45 PM to 12:15 AM)
- `health = clip(0.6 * clip(rul_p50/125, 0, 1) + 0.4 * (1 - anomaly_score), 0, 1)` (adjust and **document the formula in `docs/HEALTH.md`**).
- `data_quality`: `degraded` if the window is shorter than 30, a sensor has zero variance over the window ("flat sensor"), or any value is outside the
  training min/max by a margin; `bad` if more than half the sensors are missing/NaN. Always list reasons.
- Other components: `score_generic(kind, ...)` = rule on a wear signal vs. a limit (e.g. brake wear mm) with `IsolationForest` on 2 to 3 synthetic features.
  Mark as "rule-based demo model" in the docstring and in `HEALTH.md`.

### 6. Twin state engine L2 (12:00 to 12:45 AM)
- Persist to the DB (Shamit's `db.py`): `components`, `health_history`, `telemetry`.
- `ingest_telemetry(row)`: store the row, take the last 30 rows of that component, call `ml.health.score_*`, update cycles/hours/health/anomaly/RUL/status,
  append `HistoryPoint`, add a `TwinEvent` when the status changes (e.g. `ok -> alert`: "engine_2 abnormal T50 trend").
- `predicted`: for cycles `c0..c0+min(rul_p90+5, 60)`: `health_p50(c) = h0 * max(0, 1 - (c-c0)/rul_p50)`; band from p10 (lower) and p90 (upper).
- `apply_event(event)`: for `anomaly`/`accelerated_degradation` coordinate with Shamit's `sim.scenarios` (he moves the telemetry position; you re-score and
  append the timeline entry). `apply_maintenance(...)` resets `health` to ~1, `last_maintenance` to the completion time, cycles-since-maintenance to 0, and
  adds a `maintenance` timeline entry.
- `snapshot() -> FleetState` for Pardhu (aircraft, components, legs, stations from the DB).
- Routers: `GET /aircraft/{tail}` (`TwinView`), `GET /aircraft/{tail}/telemetry`, and the ML block of `GET /metrics`.

### 7. Tests and docs (12:45 to 1:30 AM, then fixes)
- `tests/test_ml.py` as specified in `FEATURES-3.1-3.6.md`. Prints MAE/RMSE/precision/recall/FPR so they can go on the slide **from the run**.
- `docs/HEALTH.md`: formulas, model choices, limitations ("trained on C-MAPSS / synthetic data; not validated on airline data; RUL quantiles are an
  approximation").

## What you give to others

| To | What | When |
|---|---|---|
| Shamit | `ml/sensors.py`, then `ml.cmapss.trajectories()` | 9:00 PM / 9:30 PM |
| Pardhu | `HealthScore` L1 + `FleetState` snapshot; RUL p10/p50/p90 on each component | 10:00 PM L1, 12:00 AM L2 |
| Aadhavan | `TwinView` shape in `fixtures/twin_SIM-017.json`; real `GET /aircraft/{tail}` | 10:00 PM fixture, 12:15 AM real |
| Everyone | `eval.json` numbers | 1:30 AM |

## What you need from others

Shamit: `TelemetryRow`/DB tables, the sim feeding rows. Pardhu: nothing blocking. If Shamit's DB is late, keep the in-memory store and persist later.

## Cut lines and fallbacks

- No dataset in 20 min -> synthetic C-MAPSS-shaped data (state it).
- RF not ready by 12:00 AM -> ship the L1 rule with `model_version="rule-v1"`; the loop still closes. Train the RF afterwards and swap it in.
- IsolationForest weak -> use `anomaly_score` from the RUL (e.g. `1 - health`) and say so.
- LSTM / autoencoder: **no**, unless everything else is finished and green.

## Pitfalls

- Leakage (scaling or thresholds fitted on test data), mixing the clipped and unclipped RUL, and float32/float64 JSON errors (`float(...)`).
- Sensor key names must be exactly those in `ml/sensors.py` or the simulator and scorer will not match.
- A flat sensor can crash a z-score (divide by zero): guard with an epsilon and flag `data_quality`.
- Do not claim accuracy numbers you did not compute. If a metric is missing, the slide says "in progress".

## Prompt addendum for your Claude

"My role: ML + digital twin state (features 3.1 and 3.2 backend) per work/02-AASHIRTH-ml-health-and-twin-state.md. First push ml/sensors.py with the exact sensor names, then
the C-MAPSS loader and L1 stubs, then the RF RUL model with tree-spread quantiles, IsolationForest anomaly, data-quality flags and the twin state engine. Report only metrics that
were actually computed. Use the synthetic fallback if the dataset cannot be downloaded in 20 minutes and say so."

## Definition of done

- [ ] `ml/sensors.py` pushed by 9:00 PM; `ml.cmapss.trajectories()` used by the simulator
- [ ] RUL MAE/RMSE and anomaly precision/recall/FPR computed and saved to `eval.json`
- [ ] `score_engine` returns valid `HealthScore` for normal, late-life and broken windows
- [ ] Twin state updates on telemetry and events; `GET /aircraft/SIM-017` returns a valid `TwinView` with history, predicted curve and timeline
- [ ] `docs/HEALTH.md` and `docs/DATA-SOURCES.md` written
