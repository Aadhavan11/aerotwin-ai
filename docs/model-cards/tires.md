# Model card: tires

Code: `app/sim/v2/models/tires.py`. Tests: `tests/test_sim_v2_tires.py`. Status: SIMULATED, assumed priors only.

## Intended use
Produce main-gear tire tread, pressure and temperature telemetry with tread wear, permeation leakage, FOD cuts and
retread cycles, so a high-volume consumable exists for planning and reliability demos.

## Not for
Tire change intervals, pressure-servicing rules, speed/energy limit decisions, or retread economics. One unit per tail
represents the main-gear tire set as a single lumped state; per-wheel scatter, nose-gear tires, load sharing between
axles and tire construction are not represented (the code carries no per-tire count).

## State and degradation mechanism
`tread_loss_mm`, `p_cold` (cold inflation pressure), `cut_dmg`, `cut_leak`, `retreads`, `cuts`, `n2_topups`, `condemned`,
`age_virtual`. Tread loss per landing scales with landing weight and sink rate (power laws), a wet-runway factor and taxi
distance, amplified by under-inflation and `stress_ext`, with lognormal scatter. Pressure leaks per calendar day with
temperature dependence, growth with retreads and virtual age. FOD/cut events are a Poisson process (rate rising with
dust index) that remove tread, step pressure down, and add leak. `hi` is the minimum of tread, pressure and cut health.
Failure: tread at limit, pressure below the low fraction (flat), cut damage >= 1, or condemned carcass.
Values: see PARAMS in `app/sim/v2/models/tires.py`.

## Telemetry channels
`tread_depth_mm` (mm), `tire_pressure_psi` (psi, hot pressure from the gas law at tire temperature), `tire_temp_c`
(degC, OAT plus a fixed rise plus a brake-energy term).

## Operating-condition drivers
`landing_weight_frac`, `sink_rate_fps`, `wet_runway`, `taxi_distance_km`, `dust_index`, `oat_c`, `brake_energy_mj`,
`calendar_days`, `stress_ext`.

## Coupling
In: `brakes>tires` (brake peak temperature above a threshold becomes extra stress). Out: none.

## Fault modes
| id | ATA chapter / section | effect |
|---|---|---|
| `slow_leak` | 32 / 32-41 | extra exponential pressure loss rate (log-loss implied by effect multiplier) |
| `fod_cut` | 32 / 32-41 | tread-loss multiplier, short exponential ramp, hazard scaled by dust index |

Hazards and ramps: see `LIBRARY` in `app/sim/v2/faults.py`. Note the model also has its own internal cut process
(`FOD_RATE`) independent of the fault library.

## Maintenance semantics
Cold and wet (`wet_winter`): tread loss per cycle rises a few percent (wet factor on the landing term only); pressure loss falls because permeation follows `exp(LEAK_TEMP_COEF * (OAT - 15))` (about 0.5x at -10 C), so the *unscheduled* tire count can fall while wear-driven removals rise. `p_cold` is a cold-referenced pressure, so there is no extra cold under-inflation wear term (assumption, `docs/SCENARIOS.md`).

`inspect`: nitrogen top-up of cold pressure to nominal (a state change) and finer tread reading. `repair`: partial tread
and cut recovery scaled by `REPAIR_TREAD_FRAC` and `q`. `overhaul`: retread, resets tread, cuts, pressure, increments
`retreads`; beyond `RETREAD_MAX` the carcass is condemned and only `replace` helps. `replace`: resample, zero everything.
World policy treats tread-limit removals as `wear_limit` (planned) and cuts, leaks and flats as findings/failures.

`World._do` clears ALL active hidden faults of the unit on any non-`inspect` action (`repair`, `overhaul`, `replace`; `FaultInjector.clear`), regardless of `q`: a `repair` that keeps part of the wear state still removes every injected fault. `q` scales wear only. A no-fault-found return leaves faults untouched. Maintenance-induced faults are drawn after the clear (`docs/RELIABILITY.md`).

## Data used
None. No tire wear or retread dataset is in the repository.

## Parameter provenance
| assumed | sensor-shape-checked | calibrated |
|---|---|---|
| all of PARAMS: tread new/limit, `K_LAND_MM`, weight/sink exponents, `WET_FACTOR`, `K_TAXI_MM_KM`, `P_SET_PSI`, `P_LOW_FRAC`, leak terms, `FOD_*`, cut terms, `RETREAD_*`, noise, `REPAIR_TREAD_FRAC` | none | none |

## Known limitations
* Tread-loss exponents and coefficients are not tied to any tire data; tire life in landings is whatever the priors give.
* Hot-pressure calculation uses one lumped temperature; no heat soak, no thermal fuse plugs, no pressure monitoring unit behaviour.
* ML results for tires in `docs/ML-GENERIC.md` have no at-cap rows and the raw p10-p90 band under-covers before conformal correction; treat them as a sim-only property.
* Counts of tire removals (hundreds per fleet-year in `docs/RELIABILITY.md`) are a consequence of a short assumed life.

## Validation performed and its circularity
Property tests and removal-band test only. Rule-vs-sim-trained comparisons in `docs/ML-GENERIC.md` are sim-vs-sim.
No external validation.

## Reproduce
```
~/.claude/bin/qrun pytest -q tests/test_sim_v2_tires.py tests/test_sim_v2_removal_bands.py tests/test_sim_v2_calendar_wear.py
```
