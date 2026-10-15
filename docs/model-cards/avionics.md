# Model card: avionics

Code: `app/sim/v2/models/avionics.py`. Tests: `tests/test_sim_v2_avionics.py`. Status: SIMULATED, assumed priors only.

## Intended use
Produce telemetry for a thermally stressed electronic box with a Weibull life, rising built-in-test (BITE) fault
counts, and no-fault-found (NFF) removals, so that reliability and NFF analytics have something to measure.

## Not for
LRU reliability prediction, MTBUR/MTBF budgeting, NFF-rate benchmarking against an operator, or any line-replaceable
unit selection. **One per-tail box stands for the whole avionics suite** (many LRUs in reality); box-level
statistics are not LRU-level statistics.

## State and degradation mechanism
`eff_age` (effective age accumulated per flight), `life_eff` (per-unit Weibull life from lognormal scale and Weibull
shape), `bite_fault_count` (Poisson), `box_offset_c`, `fan_factor`. Per flight: `increment = Arrhenius acceleration at
box temperature * (1 + thermal_weight * thermal-cycling term) * stress_ext`. `hi = 1 - eff_age / life_eff`. Box
temperature = per-unit offset plus ground heat soak (OAT x taxi time) plus flight heating plus `overheating` fault shift.
BITE rate rises with damage and temperature and with the `intermittent_connector` fault. Hard limits use noise-free
values; measurement noise cannot create failures.
Values: see PARAMS in `app/sim/v2/models/avionics.py`.

## Telemetry channels
`error_rate_per_khr` (errors per 1000 h, rises with damage), `box_temp_c` (degC), `bite_fault_count` (count, cumulative),
`cooling_fan_rpm` (rpm).

## Operating-condition drivers
`oat_c`, `taxi_out_min`, `taxi_in_min`, `flight_hours`, `humidity_pct` (fault hazard), `stress_ext`.

## Coupling
In: `electrical>avionics` (bus-voltage shortfall becomes extra stress; cap on total stress `max_total`). Out: none.

## Fault modes
| id | ATA chapter / section | effect |
|---|---|---|
| `intermittent_connector` | 34 / 34-00 | BITE rate multiplier (hazard scaled by humidity) |
| `overheating` | 34 / 34-00 | additive box temperature shift (hazard scaled by OAT) |

Hazards: see `LIBRARY` in `app/sim/v2/faults.py`.

## Maintenance semantics
`inspect`: no state change (NFF shop visits reinstall the unit with bit-identical state). `repair`: effective and virtual
age times `q`; `overhaul`: ages zeroed and BITE count cleared; `replace`: resample unit. NFF removal probability
(`nff_base`, `nff_fault_scale`) and the removal-reason sampling live in `nff_prob` / `removal_reason` here and in
`maintenance.py` (`NFF_HEALTH`) and `world.py` (`NFF_WINDOW`, `NFF_BITE_DELTA`); these NFF values are under revision,
so read the code. World policy defers some findings under MEL category C.

`World._do` clears ALL active hidden faults of the unit on any non-`inspect` action (`repair`, `overhaul`, `replace`; `FaultInjector.clear`), regardless of `q`: a `repair` that keeps part of the wear state still removes every injected fault. `q` scales wear only. A no-fault-found return leaves faults untouched. Maintenance-induced faults are drawn after the clear (`docs/RELIABILITY.md`).

## Data used
None. No public avionics run-to-failure or field BITE data exists in the repository.

## Parameter provenance
| assumed | sensor-shape-checked | calibrated |
|---|---|---|
| all of PARAMS: Weibull `beta` and `eta_cycles`, activation energy, thermal terms, error-rate endpoints, BITE terms, NFF terms, fan terms, noise | none | none |

## Known limitations
* A near-exponential Weibull (`beta` close to 1) means ageing carries little information by construction; infant-mortality or wear-out structure of real electronics is not represented unless the parameter is changed.
* The unscheduled removal count (including NFF) sits at the upper edge of its assumed band in `docs/RELIABILITY.md` and other seeds exceed it.
* NFF fractions are a design parameter, not an estimate of real NFF rates.
* Single-box aggregation ignores LRU redundancy, software, and common-cause effects.
* In shifted-regime ML tests avionics has a mid-to-high MAE (45 cycles; the rule model's is 50) and 82% overall coverage, 62% on below-cap rows (`docs/ML-GENERIC.md`).

## Validation performed and its circularity
Property tests (determinism, monotone damage, NFF semantics, hard-limit logic) and the removal-band test (prior band,
one pinned seed). ML results are circular. No external validation.

## Reproduce
```
~/.claude/bin/qrun pytest -q tests/test_sim_v2_avionics.py tests/test_sim_v2_removal_bands.py tests/test_reliability_pack.py
```
