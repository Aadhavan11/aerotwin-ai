# Model card: apu

Code: `app/sim/v2/models/apu.py`. Tests: `tests/test_sim_v2_apu.py`. Status: SIMULATED, assumed priors only.

## Intended use
Produce auxiliary-power-unit telemetry whose wear follows starts and running hours: EGT margin loss, starter wear,
oil consumption and rare surge shocks, so APU-related planning and reliability pages have a realistic-shaped signal.

## Not for
APU overhaul or hot-section interval decisions, start-reliability claims, MEL relief decisions, or fuel-burn work.
It is a per-tail lumped state with no model of bleed or electrical load extraction.

## State and degradation mechanism
`loss` (progressive EGT margin loss, degC), `shock_loss` (compound-Poisson surge damage), `shock_count`, `starter_wear`
(extra start time, s), `oil_loss` (%), per-unit lognormal wear, starter and oil factors. Progressive loss per flight =
(start term scaled by hot-day and density-altitude factors + hour term) times `stress_ext` times the unit factor.
Oil use grows with accumulated hours (power law, trapezoidal cycle average). Shocks arrive as Poisson events with
Gamma-summed size. `margin = healthy - loss - shock_loss - egt_overtemp fault shift`; `hi` is margin scaled between limit
and healthy; failure at the margin limit (noise-free).
Values: see PARAMS in `app/sim/v2/models/apu.py`.

## Telemetry channels
`apu_egt_margin_c` (degC), `apu_rpm_pct` (%), `apu_start_time_s` (s), `apu_oil_qty_pct` (%),
`apu_peak_start_egt_c` (degC; equals the EGT limit minus margin plus noise).

## Operating-condition drivers
`apu_starts`, `apu_hours` (from taxi and turnaround times), `oat_c` (hot-day wear, cold-start time), `density_alt_ft`,
`flight_hours` (oil-growth time axis), `stress_ext`.

## Coupling
In: none. Out: `apu>ecs` (ground APU hours this cycle become ECS stress; the edge reads the destination context).

## Fault modes
| id | ATA chapter / section | effect |
|---|---|---|
| `starter_failure` | 49 / 49-40 | starter-wear and start-time multiplier (hazard scaled by APU starts) |
| `egt_overtemp` | 49 / 49-60 | additive margin loss shift |

Hazards: see `LIBRARY` in `app/sim/v2/faults.py`.

## Maintenance semantics
`inspect`: no state change. `repair`: `loss`, `shock_loss`, `starter_wear`, `oil_loss`, virtual age times `q`.
`overhaul`: those zeroed, shock count cleared (unit factors kept). `replace`: resample unit. Health and failure are
recomputed from the margin after the action. World policy can defer some findings under MEL category C
(`defer_ok` in `POLICY`).

## Data used
None. No public APU run-to-failure data exists in the repository, and none was used.

## Parameter provenance
| assumed | sensor-shape-checked | calibrated |
|---|---|---|
| all of PARAMS: healthy and limit margin, start and hour loss, hot and altitude coefficients, starter and oil rates, shock rate and scale, EGT limit, RPM droop, noise terms | none | none |

## Known limitations
* Starts and hours per flight come from the synthetic schedule (taxi and turnaround minutes), not APU usage logs.
* No separate load-compressor, generator or bleed model; only a margin-loss abstraction.
* Margin limit and span (demo values) set the failure point; shocks and starter wear do not feed one another.
* Under the shifted regime ML coverage for APU is 76% and the rule model has the lower MAE (`docs/ML-GENERIC.md`).
* The `rpm_pct` channel is a deterministic function of health plus noise; it adds no independent information.

## Validation performed and its circularity
Property tests (monotone health, start/hour scaling, oil growth and shock failure, maintenance) and the removal-band test (prior band,
one pinned seed). ML results are circular. No external validation.

## Reproduce
```
~/.claude/bin/qrun pytest -q tests/test_sim_v2_apu.py tests/test_sim_v2_removal_bands.py
```
