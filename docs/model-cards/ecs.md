# Model card: ecs

Code: `app/sim/v2/models/ecs.py`. Tests: `tests/test_sim_v2_ecs.py`. Status: SIMULATED, assumed priors only.

## Intended use
Produce air-conditioning pack telemetry with heat-exchanger fouling, air-cycle-machine (ACM) bearing wear and
stuck-valve degradation, so ECS planning, MEL-style deferral and reliability pages have a plausible signal.

## Not for
Pack or heat-exchanger cleaning intervals, ACM life prediction, cabin-temperature or ventilation analysis, or
dispatch decisions. One per-tail unit stands for the packs jointly; there is no pack 1 vs pack 2, no bleed
management, and no cabin thermal model.

## State and degradation mechanism
`fouling` (saturating exponential, environment-scaled), `bearing_hours` with per-unit Weibull `bearing_life_h`
giving `bearing_wear = (hours / life)^exponent`, `valve_degradation` (Poisson events, each a fixed step),
`thermal_load` (OAT-driven outlet offset), `fouling_factor` (per-unit lognormal). Exposure hours = flight hours times
`stress_ext`; fouling growth multiplies a dust-and-humidity environment term and the `hx_fouling` fault rate.
`hi = 1 - max(fouling fraction, vibration/bearing index, valve degradation)`; failure at `hi <= 0` or outlet
temperature at its limit (noise-free).
Values: see PARAMS in `app/sim/v2/models/ecs.py`.

## Telemetry channels
`pack_outlet_temp_c` (degC), `pack_flow_kgs` (kg/s), `acm_speed_rpm` (rpm), `compressor_outlet_temp_c` (degC),
`ram_door_pos_pct` (%).

## Operating-condition drivers
`flight_hours`, `dust_index`, `humidity_pct`, `oat_c`, `stress_ext`.

## Coupling
In: `engine>ecs` (engine health loss, engine_1 of the tail) and `apu>ecs` (ground APU hours). Out: none.
Edge gains and clamps: `default_edges()` in `app/sim/v2/coupling.py`.

## Fault modes
| id | ATA chapter / section | effect |
|---|---|---|
| `valve_stuck` | 21 / 21-51 | level shift in valve degradation (reversible when the fault clears) |
| `hx_fouling` | 21 / 21-50 | fouling-rate boost (hazard scaled by dust index) |

Hazards: see `LIBRARY` in `app/sim/v2/faults.py`.

## Maintenance semantics
`inspect`: no state change. `repair`: fouling, fouling hours, valve degradation and virtual age times `q`; bearing hours
scaled by `q^(1/bearing_exponent)` so bearing wear scales by `q`. `overhaul`: those zeroed, valve count and thermal load
cleared, bearing hours zeroed. `replace`: resample unit. Cached health arrays are recomputed for the selected units only.
World policy can defer some findings under MEL category C.

## Data used
None. No public ECS or pack run-to-failure data exists in the repository.

## Parameter provenance
| assumed | sensor-shape-checked | calibrated |
|---|---|---|
| all of PARAMS: fouling asymptote and time constant, environment terms, bearing scale and Weibull shape, valve rate and step, flow and outlet gains, outlet limit, ACM and compressor terms, noise | none | none |

## Known limitations
* ECS is mostly at the cap in ML tests (93% in distribution), so headline ML metrics are dominated by easy rows;
  below-cap coverage is 72% in distribution and 44% under shift (`docs/ML-GENERIC.md`).
* Unscheduled removals in the 1-year backfill sit at the low end of their assumed band (`docs/RELIABILITY.md`).
* Fouling is a closed-form saturating curve; contamination events, cleaning effects and pack-specific differences are not modelled.
* Coupling to engine and APU is a scalar stress factor, not bleed temperature or flow.

## Validation performed and its circularity
Property tests (driver sensitivity and exact constant-environment fouling, life distribution, temperature failure,
maintenance) and the removal-band test. ML results are circular. No external validation.

## Reproduce
```
~/.claude/bin/qrun pytest -q tests/test_sim_v2_ecs.py tests/test_sim_v2_removal_bands.py
```
