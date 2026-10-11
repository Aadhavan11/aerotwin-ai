# Data sources

## NASA C-MAPSS, subset FD001

Turbofan engine degradation simulation, run-to-failure sensor data (Saxena and Goebel, 2008). One operating condition, one fault mode, 100 train engines and 100 test engines.

| File | Rows | Content |
|---|---|---|
| `train_FD001.txt` | 20,631 | 100 engines, each run to failure |
| `test_FD001.txt` | 13,096 | 100 engines, truncated before failure |
| `RUL_FD001.txt` | 100 | True remaining cycles at the last test cycle of each engine |

- **Original publisher:** NASA Prognostics Center of Excellence, Prognostics Data Repository.
- **Mirror we downloaded from:** `https://huggingface.co/datasets/SoyVitou/NASA-C-MAPSS-Turbofan-Engine` (`data/` folder), fetched 2026-10-10. The mirror's licence field reads "other"; we have not verified redistribution terms, so the files are **not committed** (`data/` is git-ignored). Everyone downloads them with `python -m app.ml.cmapss --download`.
- **Not real airline data.** C-MAPSS is simulated engine data. Nothing in AeroTwin is validated on airline operations.

### Checks done on the file

- No missing values. 26 columns: unit, cycle, 3 operating settings, 21 sensors.
- Single-valued: s1, s5, s10, s16, s18, s19. Two values only: s6. These are dropped (`app/ml/sensors.py`).
- Train life: 128 to 362 cycles, median 199. Test last cycle: 31 to 303. Final true RUL in test: 7 to 145.

### How it is used

| Use | Engines |
|---|---|
| Fit models | 85 of the 100 train engines (split by engine, seed 1234) |
| Calibrate RUL uncertainty | the other 15 train engines |
| Report MAE / RMSE and anomaly precision / recall / FPR | the 100 test engines |
| **Simulator replay (the demo)** | **the 100 test engines**, which no model was fitted or calibrated on |

RUL is capped at 125 cycles for training targets only. `true_rul` in replayed rows is the real, uncapped remaining life.

## Synthetic fallback

If the NASA files are unavailable, `app.ml.cmapss.synthetic_trajectories` generates C-MAPSS-shaped run-to-failure data: per-sensor healthy mean, engine-to-engine spread, end-of-life shift and noise measured on FD001, with an exponential drift to failure. `data_source()` returns `synthetic-cmapss-shaped` and the README and any slide must say so.
