# SIM-V2 engine model (C-MAPSS-calibrated surrogate)

Code: `app/sim/v2/models/engine.py` (`@register("engine")`), fit: `python -m app.calib.engine`
(writes `app/sim/v2/calibration/engine_fit.json`), validation: `python -m app.calib.engine --validate`
(`app/calib/engine_validate.py`). Data: NASA C-MAPSS FD001-FD004 training files (public NASA PCoE data,
kept under `data/external/nasa_cmapss/extracted/`, git-ignored; override with `AEROTWIN_CMAPSS_DIR`).

## Method
* Latent wear `progress` p in [0,1] advances `accel / life` per cycle. `accel` is a product of context accelerators
  (hot OAT, density altitude, low derate, dust index, `stress_ext`) with a per-cycle lognormal jitter; `life` is
  lognormal fitted to the training cycles-to-failure of the chosen variant (FD001 mean 206 / sd 46).
* Sensors: per operating condition c (FD002/FD004: 6 clusters of op1-3, KMeans; FD001/FD003: one),
  `raw = mu_c + sd_c * (poly(p) @ coef_i + sigma * noise)`, rounded to each sensor's recorded resolution.
  `mu_c` is the healthy-phase (first 15% of life) mean, `sd_c` the within-condition sd. `poly` is `[1, p, p^2, p^4]`.
  Per-engine coefficients come from per-engine OLS on life fraction; the mean, a 4-factor PCA covariance (with
  OLS estimation noise subtracted) and a diagonal remainder are stored. `sigma` is the pooled residual sd
  minus quantisation noise.
* Emitted channels: the 14 sensors in `SENSOR_NAMES` order, then `egt_margin_c`, `llp_cycles_remaining`, `hpc_eff_pct`.
* Module deficits (`def_fan/lpc/hpc/hpt/lpt`) are bookkeeping coordinates: shares of `p` per variant plus fault
  contributions. Only the fitted sensor curves are data-driven.
* Faults: `state['fault_hpt_blade_erosion']`, `state['fault_compressor_fouling']` (severity 0..1) are tracked as
  running peaks (health never recovers without maintenance), consume life, shift sensors along prior loading
  directions (fd001 end-of-life shift times a physical sensor mask) and cut EGT margin. `stress_ext` multiplies wear.
* LLP: cycle counter against a limit (20000 prior); exhaustion fails the unit. Overhaul does not reset it, replace does.
* `maintain`: inspect (flag only), repair (keeps 0.5+0.5q of progress), overhaul (keeps 0.6q, faults x0.2),
  replace (resample unit, zero everything). `EngineModel.set_variant` selects fd001..fd004 fits (default fd001).

## Time scale: model cycles vs real flights
C-MAPSS engines fail in ~206 cycles; those are not flights (a real narrow-body engine stays on wing 15-25k flights).
`PARAMS["flights_per_model_cycle"]` (default 87) maps one unit of the fitted life axis to 87 real flights, so mean
on-wing life is 206.3 x 87 = ~18,000 flights (lognormal, sd ~22%). Only the wear RATE is scaled
(`dp = accel / accel_ref / (life_scale * K)` per flight); the sensor map is a function of progress `p` alone, so the
sensor-trajectory shape against life fraction is identical to the fit. The validation below is run with K = 1
(`app/calib/engine_validate.py` sets it for the run) so its numbers stay comparable to the data and did not change.
Knock-on priors retuned for flight units: LLP limit 25,000 flights (initial use <= 30% of limit) and engine fault
hazards per flight (`hpt_blade_erosion` 2e-5, `compressor_fouling` 4e-5, roughly 10-15% of engines per year).
Shop-visit semantics in `World`: `overhaul`/`replace` when health < 0.15 (progress ~0.85, ~15k flights), engine
borescope findings only act on an active fault (otherwise on-wing repairs would keep resetting wear).
Fleet-year numbers (24 tails, 48 engines, 6 legs/day = 2,190 flights/tail-year, staggered start): see
docs/SIM-V2-SERVICE.md. A tail-year is ~12% of an engine life, so steady state is ~48 x 0.12 / 0.85 = ~7 shop visits
per fleet-year. Measured (seeds 7, 8, 6 years, fleet.py staggered start, `World` policy): cumulative engine shop visits
[0, 0, 7, 13, 26, 34] and [0, 0, 5, 12, 20, 35] i.e. 0 in years 1-2 (the v1 fleet starts at <= 45% of life) and 5-13
(mean ~7) per fleet-year after that, plus ~8 on-wing repairs per year. A 1-3 per fleet-year prior would imply ~40k-flight
engines; at 18k flights and 2,190 flights/tail-year the arithmetic above is what the model must give.

## Validation (100 simulated FD001-variant engines, seed 7, v1 flight profiles; real = FD001 train)
| check | result |
|---|---|
| Cycles to failure, sim vs real | 199.0 / 43.0 vs 206.3 / 46.3 (mean / sd) |
| FD002 / FD003 / FD004 variants (n=40) | 201 / 238 / 238 mean vs 207 / 247 / 246 real; sd 50 / 83 / 77 vs 47 / 86 / 73 |
| Two-sample classifier AUC (RF, per-row, unit-grouped 5-fold CV; 0.5 = indistinguishable) | 0.64 all rows, 0.64 for life fraction > 0.7 |
| Marginals by life-fraction bin, mean KS over 14 sensors | 0.04 / 0.03 / 0.03 / 0.04 (bins 0-.25, .25-.5, .5-.75, .75-1) |
| Same, Wasserstein / real sd | 0.06 / 0.05 / 0.07 / 0.05 |
| Worst sensor marginal (late bin) | Nc: KS 0.14, W/sd 0.15 |
| Repo RUL scorer on sim windows vs sim latent RUL (cap 125) | MAE 13.2, bias -0.4, RMSE 21.2, 722 windows |
| Scorer predicted RUL vs age, mean Spearman per engine | -0.95 (monotone decreasing) |

Reference: the scorer's held-out FD001 test MAE is 10.8 (`app/ml/artifacts/eval.json`); on FD001 train engines it
scores 1.5, which is in-sample and optimistic. Sim MAE 13.2 is therefore somewhat worse than real held-out data.

## Honest limits
* The surrogate reproduces marginal and per-engine statistics, not the C-MAPSS generative physics. An AUC of
  0.64 means a classifier can still tell sim from real; the residual is likely cross-sensor and temporal
  correlation not captured by 4 factors, and non-Gaussian noise.
* The classifier and marginal checks are FD001 only. FD002-FD004 variants are checked on cycles-to-failure only.
* Sensors are a function of progress only. Operating context does not shift raw sensors in the single-condition
  variants (real physics would move temperatures with OAT); it only drives wear. Multi-condition variants draw a
  condition per cycle from the fitted frequencies, not from flight phase.
* Module-level physics (fan/LPC/HPT/LPT deficits, fault loadings, EGT margin gain, LLP limit, accelerator gains,
  maintenance effectiveness) are priors, not identified from C-MAPSS (all training degradation is HPC/fan).
  Mean accelerator under v1 profiles (1.16) is baked in as `accel_ref` so mean life matches the fit; other
  context distributions shift life accordingly.
* The scorer is FD001-trained; running it on FD002-4 variants is not meaningful.
* Polynomial curves are only valid on p in [0,1]; progress is clipped there.
