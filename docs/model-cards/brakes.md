# Model card: brakes

Code: `app/sim/v2/models/brakes.py`. Tests: `tests/test_sim_v2_brakes.py`. Status: SIMULATED, assumed priors only.

## Intended use
Produce plausible brake wear, temperature and pressure telemetry that responds to landing energy, application count,
wet runways and rejected takeoffs, so that planning, reliability and ML pipelines have a consumable that wears out.

## Not for
Brake life prediction, brake-change interval setting, energy-limit or turnaround-time decisions, or comparing carbon vs
steel brakes. One state per tail lumps all braked wheels (`N_BRAKES` shares the energy); individual wheel or
anti-skid/brake-control behaviour is not represented.

## State and degradation mechanism
`wear_mm` (pad/heat-sink wear), `ox_mm` (oxidation part), `temp_c` (lumped disc peak temperature), `rto_count`, `age_virtual`
(Kijima-style virtual age), per-unit samples (wear coefficient, wear limit, oxidation sensitivity, cooling constant).
Per flight: wear = per-application wear scaled by energy per application (exponent below 1), wet factor, virtual-age
factor, `stress_ext` and lognormal scatter; plus Arrhenius oxidation above a reference temperature (capped per cycle);
plus a Poisson rejected-takeoff shock that adds large wear and a very high peak temperature. Temperature cools from the
previous peak toward OAT with a unit-specific time constant, then heats by the landing-roll share of cycle energy.
`hi = 1 - wear / limit`, kept monotone until maintenance; `failed` when wear reaches the unit limit.
Values: see PARAMS in `app/sim/v2/models/brakes.py`.

## Telemetry channels
`pad_wear_mm` (mm, noisier between inspections), `disc_temp_c` (degC, peak of the cycle), `brake_pressure_kpa` (kPa,
derived from energy per application), `brake_apps_cycle` (count).

## Operating-condition drivers
`brake_applications`, `brake_energy_mj`, `wet_runway`, `oat_c`, `flight_hours`, `stress_ext`. Application count and
energy come from `drivers._derive` and `app/sim/profile.py` (landing weight, runway length, wetness, taxi time).

## Coupling
In: `hydraulics>brakes` (hydraulic damage becomes extra wear stress). Out: `brakes>tires` (disc temperature above a
threshold raises tire stress). Edge gains and clamps: `default_edges()` in `app/sim/v2/coupling.py`.

## Fault modes
| id | ATA chapter / section | effect |
|---|---|---|
| `stuck_brake` | 32 / 32-42 | additive peak-temperature shift ramping with severity (drag), raises oxidation |
| `rejected_takeoff_overheat` | 32 / 32-42 | additive peak-temperature shift, short ramp, hazard scaled by brake energy |

Hazards and ramps: see `LIBRARY` in `app/sim/v2/faults.py`. These faults heat the brake; they do not change wear
directly except through oxidation.

## Maintenance semantics
`inspect`: next emit reads the wear pin at inspection resolution; no state change. `repair`: wear, oxidation and virtual
age multiplied by `q` (default 0.6). `overhaul`: same, multiplied by `OVERHAUL_FRAC * q`. `replace`: resample unit
parameters, zero wear, RTO count and ages. This is an abstraction (real heat-sinks are replaced, not partly repaired).
`failed` and `hi` are recomputed from the new wear. In the world policy, brake wear removals are labelled `wear_limit`
(planned) when no fault is active.

`World._do` clears ALL active hidden faults of the unit on any non-`inspect` action (`repair`, `overhaul`, `replace`; `FaultInjector.clear`), regardless of `q`: a `repair` that keeps part of the wear state still removes every injected fault. `q` scales wear only. A no-fault-found return leaves faults untouched. Maintenance-induced faults are drawn after the clear (`docs/RELIABILITY.md`).

## Data used
None. No public run-to-failure or fleet brake-wear dataset exists in the repository, and none was used
(`docs/SIM-V2-CALIBRATION.md` covers hydraulics and battery only).

## Parameter provenance
| assumed | sensor-shape-checked | calibrated |
|---|---|---|
| all of PARAMS: `K_APP_MM`, `E_REF_MJ`, `BETA_ENERGY`, `WET_FACTOR`, `LIMIT_MM`, `HEAT_CAP_MJ_K`, `N_BRAKES`, `TAU_COOL_MIN`, oxidation terms, RTO terms, `AGE_COEF`, sensor noise, `OVERHAUL_FRAC` | none | none |

`LIMIT_MM` and the generic limit in `app/ml/sensors.py` GENERIC are demo values, not a manual limit.

## Known limitations
* One lumped heat-sink for all braked wheels; no wheel-to-wheel scatter, no brake-temperature-monitor dynamics beyond one peak per cycle.
* Cold + wet (stress scenario `wet_winter`, -10 C, 60% wet): wear per cycle rises about 15-20% (wet energy and `WET_FACTOR` outweigh the halved Arrhenius oxidation term at a ~25 C lower peak). Checked over 3 seeds (`tests/test_sim_v2_fault_effects.py`); no term was rebalanced.
* Wear is near-linear in applications; real wear regimes (carbon wear vs temperature, cold-brake wear) are not resolved.
* Maintenance by scaling wear with `q` is not how brakes are actually serviced.
* Hydraulic coupling is a scalar stress multiplier, not pressure-dependent braking.
* Planned-removal share and counts in `docs/RELIABILITY.md` are prior bands, not data.
* `docs/ML-GENERIC.md` says `stress_ext` has little or no effect on brakes; in this code (and `test_stress_ext_scales_wear_and_oxidation_needs_heat`) it multiplies mechanical wear.

## Validation performed and its circularity
Only property tests (monotone wear, determinism, shocks, maintenance semantics) and the removal-band test
(`tests/test_sim_v2_removal_bands.py`, seed 1234, prior bands). Sim-trained ML results for brakes in
`docs/ML-GENERIC.md` are circular (trained and tested on this model's output). There is no external validation.

## Reproduce
```
~/.claude/bin/qrun pytest -q tests/test_sim_v2_brakes.py tests/test_sim_v2_removal_bands.py
```
