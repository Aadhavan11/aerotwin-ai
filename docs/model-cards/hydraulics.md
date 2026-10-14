# Model card: hydraulics

Code: `app/sim/v2/models/hydraulics.py`. Calibration study: `app/calib/hydraulic.py`, `docs/SIM-V2-CALIBRATION.md`,
`app/sim/v2/calibration/hydraulics_fit.json`. Tests: `tests/test_sim_v2_hydraulics.py`, `tests/test_calib_hydraulic.py`.
Status: SIMULATED. Priors are assumed; the lab study changed no value.

## Intended use
Produce system-level hydraulic power telemetry (pump pressure, fluid temperature, case drain, fluid quantity, filter
differential pressure) with monotone pump-efficiency loss, leaks, gas precharge loss and fluid/filter contamination.

## Not for
Pump or filter life prediction, fluid-sampling intervals, leak-rate acceptance limits, or system-architecture work.
One per-tail aggregate stands for all hydraulic systems/pumps. Cooler faults and valve degradation seen in the lab rig
are not modelled (see below).

## State and degradation mechanism
Latents: `eta_loss` (efficiency, clipped Wiener increments so monotone), `leak` (gamma increments), `precharge_loss`,
`acid_number` (contamination proxy), `filter_dp_kpa`. Per-unit rate parameters are lognormal around PARAMS. The driver
multiplies Arrhenius temperature acceleration, a payload/derate pressure-cycle factor, `stress_ext`, an acid-number term
and the `pump_cavitation` fault rate. `damage = eta_loss + leak_weight*leak + precharge_weight*precharge_loss`,
clipped to [0,1]; `hi = 1 - damage`; failure at `hi <= 0`. Pressure falls linearly with damage between healthy and limit.
Values: see PARAMS in `app/sim/v2/models/hydraulics.py` (`PRIOR_PARAMS` keeps the unfitted values).

## Telemetry channels
`pump_pressure_kpa` (kPa), `fluid_temp_c` (degC), `case_drain_flow_lpm` (L/min), `fluid_qty_pct` (%),
`filter_dp_kpa` (kPa, excess over a fresh filter).

## Operating-condition drivers
`oat_c`, `flight_hours` (fluid heating), `payload_frac`, `thrust_derate`, `dust_index`, `stress_ext`.

## Coupling
In: none. Out: `hydraulics>brakes`, `hydraulics>landing_gear` (damage sum `eta_loss + leak + precharge_loss` as extra stress).

## Fault modes
| id | ATA chapter / section | effect |
|---|---|---|
| `pump_cavitation` | 29 / 29-10 | efficiency-loss rate boost, exponential ramp |
| `line_leak` | 29 / 29-10 | leak-rate boost (affects case drain, fluid quantity) |

Hazards: see `LIBRARY` in `app/sim/v2/faults.py`.

## Maintenance semantics
`inspect`: increments an inspection counter only. `repair`: all latents and virtual age multiplied by `q`; filter dp
set to zero. `overhaul`: latents zeroed (retained fraction 0), unit rate parameters kept. `replace`: also resamples
rate parameters and zeroes ages. Note `overhaul` and `replace` differ only in resampling and age reset.

## Data used
UCI hydraulic condition-monitoring rig (ZeMA, 2205 cycles, 144 label cells, 1449 stable cycles; 160 bar lab bench with
a designed, stepped fault grid; data under `data/external/uci_hydraulic/`, git-ignored). Used for: sensor sensitivities,
noise floors, detectability of conditions. Results (`docs/SIM-V2-CALIBRATION.md`): cooler fault strongly visible (no
counterpart in the model); valve degradation not detectable from cycle means; pressure rises with leak and low precharge
on the rig, opposite to the model; model sensors 12x (pressure) to 70x (temperature) noisier than the lab rig.
**Of 36 hydraulics PARAMS, 0 were changed** (the two override keys equal the priors).

## Parameter provenance
| assumed | sensor-shape-checked | calibrated |
|---|---|---|
| all rates (`eta_drift`, `leak_scale`, `acid_rate`, `precharge_rate`, `filter_rate`), `life_cycles`, spreads, weights, `activation_k`, `heat_eta`, thermal terms, failure Weibull terms, sensor gains | `pressure_noise`, `temp_noise` (rig noise below prior floor; prior kept), `heat_eta` and `leak_weight`/`precharge_weight` (rig sign conflict or non-significant; prior kept) | none |

## Known limitations
* No fleet or flight hydraulics data; rates are literature-style priors with a nominal life of about 2000 flights.
* Rig pressure moves opposite to the model for leak and precharge; unresolved (probably bench load control), so pump
  pressure sensitivity is a prior.
* `acid_wear` (acid-number-to-pump coupling) is marked weakest in the code.
* Lab rig is a stepped fault design; degradation rates and failure timing are not estimable from it.
* Sensor noise in the model is a stated floor, not a measured fleet value.
* Module docstring still says calibration "comes later"; the committed fit exists but did not change values.

## Validation performed and its circularity
Before/after distributional comparison with rig data (KS and Wasserstein; fitted equals prior, so before = after) and
detectability analysis; these show what the rig can and cannot support, not model accuracy. ML results in
`docs/ML-GENERIC.md` are circular. Under a shifted stress regime hydraulics coverage is 47% against an 80% target.

## Reproduce
```
python -m app.calib.hydraulic [--validate]     # needs data/external/uci_hydraulic or AEROTWIN_HYDRAULIC_DIR
~/.claude/bin/qrun pytest -q tests/test_sim_v2_hydraulics.py tests/test_calib_hydraulic.py
```
