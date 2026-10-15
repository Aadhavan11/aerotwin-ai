# Model card: electrical

Code: `app/sim/v2/models/electrical.py`. Calibration: `app/calib/battery.py`, `docs/SIM-V2-CALIBRATION.md`,
`app/sim/v2/calibration/electrical_fit.json`. Tests: `tests/test_sim_v2_electrical.py`, `tests/test_calib_battery.py`.
Status: SIMULATED. Three values fitted from lab battery cells; everything else assumed.

## Intended use
Produce DC battery and AC generator (IDG) telemetry: battery capacity fade and resistance growth, loaded bus voltage,
bus frequency droop and IDG oil temperature, so downstream consumers see a slow-wear electrical system.

## Not for
Battery state-of-health, generator overhaul intervals, load-shedding or power-budget analysis, ETOPS decisions.
One per-tail aggregate lumps battery and generator(s). Fitted battery values come from small 2 Ah 18650 cells at one
temperature, not aircraft batteries.

## State and degradation mechanism
`fade_pct` (capacity fade, percentage points), `resistance_growth` (extra mohm), `generator_wear`, calendar watermark
`cal_days_prev`. Fade per flight = (calendar term in sqrt of days + cycle term scaled by APU starts) times Arrhenius
acceleration, plus rare thermal-event steps, times `stress_ext` and the `cell_short` fault rate. Resistance =
`p_r0 * (1 + resistance_fade * fade) + resistance_growth`. Loaded bus voltage = nominal times a capacity-dependent
fraction minus load current times resistance. `hi = 1 - fade / (capacity_healthy - capacity_limit)`; failure at `hi <= 0`.
Generator wear grows with flight hours and the `idg_bearing` fault level.
Values: see PARAMS in `app/sim/v2/models/electrical.py` (`PRIOR_PARAMS` keeps unfitted values).

## Telemetry channels
`battery_capacity_pct` (%), `battery_resistance_mohm` (mohm), `bus_voltage_v` (V), `bus_frequency_hz` (Hz),
`idg_oil_temp_c` (degC).

## Operating-condition drivers
`oat_c`, `calendar_days`, `apu_starts`, `flight_hours`, `payload_frac` (load current), `stress_ext`.

## Coupling
In: none. Out: `electrical>avionics` (noise-free bus-voltage shortfall below a reference loaded level becomes avionics stress).

## Fault modes
| id | ATA chapter / section | effect |
|---|---|---|
| `cell_short` | 24 / 24-30 | fade-rate boost, exponential ramp |
| `idg_bearing` | 24 / 24-20 | additive IDG oil temperature shift and extra generator wear |

Hazards: see `LIBRARY` in `app/sim/v2/faults.py`.

## Maintenance semantics
`inspect`: counter only. `repair`: latents (fade, resistance growth, generator wear) and virtual age times `q`.
`overhaul`: latents zeroed (unit parameters kept). `replace`: also resamples unit parameters and zeroes ages.
The calendar watermark is preserved so maintenance does not "recharge" elapsed time.

`World._do` clears ALL active hidden faults of the unit on any non-`inspect` action (`repair`, `overhaul`, `replace`; `FaultInjector.clear`), regardless of `q`: a `repair` that keeps part of the wear state still removes every injected fault. `q` scales wear only. A no-fault-found return leaves faults untouched. Maintenance-induced faults are drawn after the clear (`docs/RELIABILITY.md`).

## Data used
NASA PCoE Li-ion battery cells B0005/B0006/B0007/B0018 (24 degC, 168/168/168/132 discharges; lab cycling,
`data/external/nasa_battery_5/`, git-ignored). Findings: fade shape not distinguishable between linear/exponential/knee
out of sample (held-out error about 5.5 pp); resistance-to-fade coupling much smaller than the prior; temperature
acceleration not estimable (single temperature).

## Parameter provenance
| assumed | sensor-shape-checked | calibrated |
|---|---|---|
| `cycle_fade`, `calendar_fade`, `life_cycles`, `activation_k`, `capacity_limit`, `resistance_rate`, generator and IDG terms, thermal-event terms (`lam_thermal` marked weakest), load and voltage model, frequency and oil terms, failure Weibull terms | fade shape (linear vs exponential vs knee) vs cells: model shape consistent, dynamics unchanged | `resistance_fade` (prior 0.03 to about 0.0065), `capacity_noise` (about 0.66), `resistance_noise` (about 0.12) |

Rig cycles are not flights; absolute rates (`cycle_fade`, `life_cycles`) are not transferable and stay priors.

## Known limitations
* Four cells, one temperature, one current profile: calibrated values are small-sample relative couplings, not aircraft battery behaviour.
* `resistance_rate` is still a prior and may double count with the fitted fade coupling (up to about 7% of R0).
* Calibrated noise values are lab-cell scatter, not fleet sensor noise.
* The generator/IDG half has no data at all.
* "Fade matches" in the before/after table is partly scale-matched to the rig window, not independent.

## Validation performed and its circularity
Before/after distributional comparison with the 4 cells (in-sample for the fitted values) and 3-fit/1-test
held-out fade check. `resistance_fade` improvement in R/R0 is in-sample. ML results are circular.

## Reproduce
```
python -m app.calib.battery [--validate]      # needs data/external/nasa_battery_5 or AEROTWIN_BATTERY_DIR
~/.claude/bin/qrun pytest -q tests/test_sim_v2_electrical.py tests/test_calib_battery.py
```
