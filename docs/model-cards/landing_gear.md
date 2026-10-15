# Model card: landing_gear

Code: `app/sim/v2/models/landing_gear.py`. Tests: `tests/test_sim_v2_landing_gear.py`. Status: SIMULATED, assumed priors only.

## Intended use
Produce main landing-gear strut telemetry (servicing pressure, touchdown load, extension, hard-landing flag) with
slow seal leakage, fatigue accumulation and a hidden crack that is only found by inspection with a probability of detection.

## Not for
Gear life-limit or fatigue-life decisions, hard-landing inspection criteria, crack-growth or damage-tolerance analysis,
retraction or door behaviour. One lumped strut state per tail stands for the gear; legs, nose gear and actuators are not
separated. (Life-limited-part tracking for gear parts lives separately in `app/ops/llp.py`, `docs/LLP.md`, and does not
read this model's state.)

## State and degradation mechanism
`leak_kpa` (seal pressure loss, gamma process with cold-weather and virtual-age growth, plus a step on hard landings),
`fatigue` (Miner damage from a sink-rate load spectrum with an S-N power law), `crack_mm` (latent Paris-law growth from a
sampled initial flaw), `hard_count`, `inspection_due`, `crack_detected`, `age_virtual`. Effective sink rate gets
lognormal noise; above a threshold the landing is "hard" and an inspection becomes due. `hi` is the minimum of leak,
fatigue and crack health, kept monotone until maintenance. Failure: pressure at limit, fatigue >= 1 or crack at critical length.
Values: see PARAMS in `app/sim/v2/models/landing_gear.py`.

## Telemetry channels
`strut_pressure_kpa` (kPa), `shock_load_g` (g, touchdown load factor), `strut_extension_mm` (mm), `hard_landing_flag`
(0 or 1). Crack length is not telemetry; it is only exposed through inspection outcomes.

## Operating-condition drivers
`sink_rate_fps`, `landing_weight_frac`, `oat_c` (cold seal stiffening), `stress_ext`.

## Coupling
In: `hydraulics>landing_gear` (hydraulic damage becomes extra stress). Out: none.

## Fault modes
| id | ATA chapter / section | effect |
|---|---|---|
| `strut_seal_leak` | 32 / 32-11 | degradation-rate boost on leak (rate exponent applied to effect multiplier) |
| `hard_landing_damage` | 32 / 32-11 | additive fatigue shift, very short ramp, hazard scaled by sink rate |

Hazards and ramps: see `LIBRARY` in `app/sim/v2/faults.py`.

## Maintenance semantics
`inspect`: draws crack detection from a log-logistic POD vs crack size (`POD_A50_MM`, `POD_SLOPE`), sets
`crack_detected`, clears `inspection_due`; no repair. `repair`: scales leak by `q`, fatigue by `0.5 + 0.5q`, blends crack
toward the initial flaw by `q`. `overhaul`: new seal pack (leak zero), fatigue and virtual age scaled by `OVERHAUL_FRAC * q`,
crack reset to initial flaw. `replace`: resample all unit samples. The maintenance-layer inspection POD
(`pod_curve` in `maintenance.py`) is a second, separate detection model used by scheduled checks.

`World._do` clears ALL active hidden faults of the unit on any non-`inspect` action (`repair`, `overhaul`, `replace`; `FaultInjector.clear`), regardless of `q`: a `repair` that keeps part of the wear state still removes every injected fault. `q` scales wear only. A no-fault-found return leaves faults untouched. Maintenance-induced faults are drawn after the clear (`docs/RELIABILITY.md`).

## Data used
None. No gear load, seal, or crack-growth dataset is in the repository.

## Parameter provenance
| assumed | sensor-shape-checked | calibrated |
|---|---|---|
| all of PARAMS: nominal and limit pressure, leak gamma shape/scale, cold and age coefficients, hard-landing threshold, load-factor law, S-N exponent and `N_REF`, Paris `C` and `m`, critical crack, POD curve, sensor noise, `OVERHAUL_FRAC` | none | none |

## Known limitations
* Load factor is a linear function of sink rate and weight; no gust, crosswind, drift or bounce.
* The fatigue and crack paths are independent damage channels with no interaction.
* Rate priors (leak, fatigue, crack) are intended to land in an industry-plausible-looking band, not fitted.
* In ML tests, landing_gear has very many cap rows (93% in distribution), so headline coverage looks good while
  coverage on below-cap rows is poor, 19% (`docs/ML-GENERIC.md`).
* Hard-landing threshold is scaled to the profile generator's sink-rate range (2-4 ft/s), not to a certification limit.

## Validation performed and its circularity
Property tests (monotone health, hard-landing inspection flag, POD behaviour, maintenance) and the removal-band test
for planned/unscheduled counts (prior band, one pinned seed). ML results are circular. No external validation.

## Reproduce
```
~/.claude/bin/qrun pytest -q tests/test_sim_v2_landing_gear.py tests/test_sim_v2_removal_bands.py
```
