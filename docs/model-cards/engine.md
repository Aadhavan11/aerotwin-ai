# Model card: engine (C-MAPSS-calibrated surrogate)

Code: `app/sim/v2/models/engine.py` (`@register("engine")`). Fit: `app/sim/v2/calibration/engine_fit.json`
(`python -m app.calib.engine`). Longer method text: `docs/SIM-V2-ENGINE.md`. Status: SIMULATED, research surrogate.

## Intended use
Generate engine telemetry whose sensor trajectories against life fraction have the shape of the NASA C-MAPSS turbofan
data, for exercising the twin, scorer, reliability and planning pipelines on a run-to-failure-like signal.

## Not for
Predicting when a real engine will need a shop visit; any airworthiness, life-limit or maintenance-interval decision;
representing a specific engine type or manufacturer; module-level (fan/LPC/HPC/HPT/LPT) diagnosis. C-MAPSS is itself a
simulation, so this is a surrogate of a simulator, not of fielded engines.

## State and degradation mechanism
Latent wear `progress` p in [0,1] (1 = end of life) advances per flight by `accel / life` with
`life` lognormal (fitted to C-MAPSS cycles-to-failure of the chosen variant) and `accel` a product of context
accelerators (hot OAT, density altitude, low derate, dust, `stress_ext`) with per-cycle lognormal jitter.
Sensors are `mu_c + sd_c * (poly(p) @ coef + sigma * noise)` per operating condition `c`, so sensor values are a function
of p alone. Other state: module deficits `def_fan/lpc/hpc/hpt/lpt` (bookkeeping shares of p plus fault terms), fault
severities, an LLP cycle counter, variant (`set_variant`, fd001 default). Wear-rate time scale is stretched by
`flights_per_model_cycle` so mean life is about 18k flights instead of about 206 model cycles.
Values: see PARAMS in `app/sim/v2/models/engine.py`.

## Telemetry channels
14 C-MAPSS sensors (`SENSOR_NAMES`, units in `app/ml/sensors.py`): T24, T30, T50 (degR), P30, Ps30 (psia),
Nf, Nc, NRf, NRc (rpm), phi (pps/psi), BPR, htBleed (dimensionless), W31, W32 (lbm/s). Plus `egt_margin_c` (degC),
`llp_cycles_remaining` (flights), `hpc_eff_pct` (%). Sensors are rounded to the recorded C-MAPSS resolution.

## Operating-condition drivers
`oat_c`, `density_alt_ft`, `thrust_derate`, `dust_index`, `humidity_pct` (fault hazard only), `stress_ext`.
In single-condition variants (FD001, FD003) raw sensors do not move with OAT; context only changes the wear rate.
In multi-condition variants (FD002, FD004) one of 6 fitted conditions is drawn per cycle from fitted frequencies, not
from flight phase.

## Coupling
In: none. Out: `engine>ecs` (extra ECS stress from `1 - hi`; the edge maps a tail to its first engine unit, engine_1).

## Fault modes
| id | ATA chapter / section | effect |
|---|---|---|
| `hpt_blade_erosion` | 72 / 72-50 | consumes life, shifts sensors along a prior loading mask, cuts EGT margin |
| `compressor_fouling` | 72 / 72-30 | consumes life, shifts HPC-related sensors, cuts `hpc_eff_pct` |

Hazards, ramp shape and driver gains: see `LIBRARY` in `app/sim/v2/faults.py`. Severity is tracked as a running peak.

## Maintenance semantics
`inspect` flags only (no state change). `repair` keeps `repair_keep + (1 - repair_keep) * q` of progress.
`overhaul` keeps `overhaul_keep * q` of progress and scales faults down. `replace` resamples the unit and zeroes
everything including the LLP counter; `overhaul` does not reset LLP. World policy (`POLICY`, `world.py`): shop visit
(overhaul) below a health threshold; borescope findings act only on an active fault. Values: see PARAMS.

## Data used
| item | status |
|---|---|
| mean sensor trajectory curves, per-engine coefficient spread (4-factor PCA), noise, operating-condition baselines, sensor resolutions, life distribution per variant | calibrated on C-MAPSS FD001-FD004 training files |
| check of marginals, cycles-to-failure, RUL scorer behaviour | sensor-shape-checked / distributional, FD001 mainly |
| time-scale factor to flights, accelerator gains, module shares, fault loadings, EGT margin gain, LLP limit, maintenance effectiveness, fault hazards | assumed |

No real engine data is in the repository. C-MAPSS training degradation is HPC/fan only; every other module effect is assumed.

## Parameter provenance
| assumed | sensor-shape-checked | calibrated |
|---|---|---|
| `flights_per_model_cycle`, accelerator gains (`hot_per_c`, `da_per_kft`, `derate_gain`, `dust_gain`), `accel_ref`, EGT margin and fault terms, LLP limit and initial fraction, fault progress and efficiency terms, `repair_keep`, `overhaul_keep`, fault loadings and masks, module shares | marginal distributions by life-fraction bin and classifier AUC (FD001) | life lognormal, sensor means/sds per condition, polynomial coefficients and covariance, noise sigma, resolution (all in `engine_fit.json`) |

## Known limitations
* Sensors depend on progress only; real temperatures would move with OAT and phase.
* A classifier still separates sim from real (AUC about 0.64 in `SIM-V2-ENGINE.md`); cross-sensor/temporal correlation is only partly captured.
* The 87 flights-per-model-cycle factor, and hence mean life and shop-visit rate, is a stated prior (narrow-body interval of 15-25k flights), not a result.
* The scorer is FD001-trained; do not apply it to FD002-FD004 variants.
* Two engines on a tail are independent apart from shared context; engine-to-engine interaction is not modelled.

## Validation performed and its circularity
Distributional checks against FD001 train (100 simulated engines, seed 7). The reference data is the same data the
curves were fitted on, so the checks are in-sample. The RUL scorer check is sim-vs-sim (scorer trained on C-MAPSS,
scored on simulator output): MAE 13.2 on sim windows vs 10.8 on held-out real FD001 test, per `SIM-V2-ENGINE.md`.
Time-scale-dependent outputs (shop visits per fleet-year) follow arithmetically from the assumed factor.

## Reproduce
```
python -m app.calib.engine              # refit (needs data/external/nasa_cmapss, AEROTWIN_CMAPSS_DIR)
python -m app.calib.engine --validate   # table in docs/SIM-V2-ENGINE.md
~/.claude/bin/qrun pytest -q tests/test_sim_v2_engine.py tests/test_calib_engine.py
```
