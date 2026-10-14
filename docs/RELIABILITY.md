# Reliability pack and ATA taxonomy

All data is SIMULATED (`provenance: "SIMULATED"`, `simulated: true`). ATA-style, simplified, fictional part numbers; advisory
only, not a certified reliability analysis.

## Code
* `app/taxonomy/ata.py`: chapters (05, 21, 22, 24, 29, 31, 32, 34, 36, 49, 72), sections (e.g. 32-41 wheels/tires, 32-42 brakes),
  `KIND_SECTION` (component kind -> section), `MODE_SECTION` (v2 fault mode -> section), `ata_for(kind, mode=None)`,
  `chapter_for`, `table()`. `app/sim/v2/faults.py` is not edited; `tests/test_ata.py` checks its inline chapters agree.
* `app/insights/reliability_pack.py`: `from_world(world)`, `from_run_dir(path)`, `cached_backfill(seed?, years, fleet)`,
  `compute(dataset)`, `pack()`, `weibull_fits(dataset)` (right-censored Weibull MLE on the dataset's own v2 removal /
  install-time records via `app/insights/reliability.fit_lives`; `weibull_fits()` with no dataset is the legacy v1 fit).
* `app/sim/constants.py` (re-exported by `app/sim/v2/constants.py`): the single source for flights per day (6), the engine
  mean on-wing life (206.3 model cycles x 87 flights = ~17,950 flights) and the LLP cycle limits. See "Constants".

## Definitions
Removal = action `replace`, `overhaul` or `nff_return` (on-wing repairs/inspections excluded). Exposure = sum of unit flight
hours/cycles of the kind (per ATA chapter: kinds summed, i.e. unit-hours).

**Metrics are per-tail system aggregates, not per part / LRU.** Brakes = the 4 main-wheel positions lumped, tires = 6 wheel
positions lumped, avionics = one box standing for many LRUs, hydraulics = pumps + filters + lines, electrical = battery +
generator, ecs = packs + valves, engine = one whole engine (2 per tail). A "removal" is a change of that aggregate; MTBUR/MTBF
are per aggregate unit-hour. Every `per_kind` and `weibull` row carries a `unit_definition` string; the pack carries
`metrics_unit: "per-tail system aggregate"`.
* Removal reasons: `failure`, `inspection_finding`, `nff`, `performance` are unscheduled. `scheduled`, `opportunistic`, `deferred_defect_rectification` and `wear_limit` are planned.
* `performance` = engine shop visit driven by EGT-margin / performance erosion (health index below the shop-visit threshold, used
  as the margin proxy). It is condition-triggered, so it is unscheduled and counts in MTBUR, but it is not a confirmed failure and
  is excluded from MTBF.
* NFF (`nff_return`, reason `nff`, `nff=true`): the unit is removed, goes to the shop, is found serviceable and is RETURNED with
  its state unchanged (same serial, TSN/CSN; no new serial). It is an unscheduled removal (MTBUR, NFF rate) and never a failure
  (MTBF). A repeat BITE report on a box that already had an NFF return is escalated to a swap (`failure`). `wear_limit` = tire/brake change (retread or replace) because the wear / retread limit was reached with no active fault, cut or pressure loss; it is a consumable change, never an unscheduled removal. Cuts/FOD, leaks, faults and hard failures stay `failure` / `inspection_finding`.
* MTBUR = exposure / unscheduled removals (FH and FC); MTBF = exposure / confirmed failures (reason `failure`, not NFF).
* NFF rate = NFF removals / unscheduled removals. Premature-removal rate = removals with `health_before >= 0.5` / removals.
* Removal rate per 1000 FH with exact (Garwood) 95 % Poisson interval.
* AMOS-style alert: 30-day periods, rate per 1000 FH (FH spread uniformly over the calendar span); latest complete period vs
  mean + 3 sigma of up to 12 earlier periods (`warning` at +2 sigma; needs >= 3 earlier periods and >= 2 events to alert).
* Repeat defect: >= 3 unscheduled removals of the same tail + position within 90 days.
* Weibull (per kind, `weibull[]`): right-censored MLE on the v2 removal / install-time records of the same dataset. Life = cycles
  since last restoration (new, or overhaul). Failures = unscheduled non-NFF removals (including `performance`); planned
  removals and units still in service are suspensions censored at their current life; NFF returns produce no record. Caveat:
  lives start at the simulation start while units are pre-aged (`initial_wear`), so first lives are left-truncated and beta/eta are
  biased short (`basis` says so). The older v1 fit (synthetic `wear_profile` lives, 8200-flight engine slot life) is not used here.

## Source selection
`SIM_ENGINE=v2` and the live simulator's world has stepped: that world (`source.basis = "live"`). Otherwise a deterministic
1-year backfill built exactly like the runner (`runner.build_world({seed: settings.SEED, initial_wear: true})`, 2,190 cycles,
no telemetry written), cached in memory and as `<DB dir>/cache/reliability_<sha256(seed,years,fleet,config)>.json`
(~15 s once). The 2,190 cycles span exactly 365 calendar days: the simulated clock advances `1 / (drivers.LEGS_PER_DAY * world.utilisation)` days per cycle (6 legs/day, one constant shared by World, runner and this pack; per-tail utilisation scatter does not stretch the clock).

## Routes (GET, any signed-in role; no GUARDS rule exists for `/insights` GETs)
`GET /insights/ata`: `{simulated, provenance, label, chapters:[{chapter,title,kinds,sections:[{section,title,kinds,fault_modes}]}], kind_to_section, mode_to_section}`.

`GET /insights/reliability/pack`:
```
{as_of, simulated, provenance, disclaimer, taxonomy_label, unscheduled_removals_total,
 source:{basis:"live"|"backfill", cycles, seed, tails, years?, engine, cache_key?, span_days},
 metrics_unit:"per-tail system aggregate",
 metrics:{per_kind:[{kind, ata_chapter, ata_section, units, unit_definition, flight_hours, flight_cycles, removals, unscheduled_removals, failures,
                     nff_removals, mtbur_fh, mtbur_fc, mtbf_fh, mtbf_fc, nff_rate, premature_removal_rate,
                     removal_rate_per_1000fh, rate_ci_low, rate_ci_high, ci_level, alert_status}],
          per_ata:[{ata_chapter, title, kinds, units, ...same metric keys}]},
 pareto:[{ata_chapter, title, unscheduled_removals, share, cumulative_share}],
 alerts:[{kind, ata_chapter, status:"normal|warning|alert|insufficient_data", period_days, latest_rate_per_1000fh, latest_count,
          threshold_per_1000fh, warning_per_1000fh, window_mean_per_1000fh, window_sigma_per_1000fh, window_periods, rule}],
 repeat_defects:[{tail, kind, position, ata_chapter, count, window_days, first, last, reasons, nff_count}],
 top_contributors:{kinds:[{kind, ata_chapter, unscheduled_removals, share, cumulative_share, removal_rate_per_1000fh, nff_removals}],
                   tails:[{tail, unscheduled_removals}]},
 weibull:[{kind, components, samples, failures, censored, beta, eta_cycles, mean_life_cycles, mtbur_cycles, b10_cycles, basis,
           unit_definition}]}
```
Metric values are `null` when undefined (no events).

## Simulator removal-rate bands (assumed priors, not calibration)
Unscheduled removals (`replace` / `overhaul` / `nff_return` with `removal.unscheduled`: failure, inspection_finding, nff,
performance) per 24 tails per year (2190 cycles = 365 days). The bands are ASSUMED priors (no citation): typical MTBUR orders of
magnitude, NOT validated against any operator's data. Construction for every number: `build_world({"seed": S, "fleet": 24})`
(initial_wear=True, as the reliability pack builds it), `run(2190)`. `tests/test_sim_v2_removal_bands.py` asserts, per kind, that
the MEDIAN over seeds 1/7/99/1234/4242 lies inside the band and that at least 4 of the 5 seeds do (no single-seed tuning;
runtime ~80 s for the five 24-tail years).

| kind | band | BEFORE seeds 1/7/99/1234/4242 (min / median / max) | AFTER seeds 1/7/99/1234/4242 (min / median / max) |
|---|---|---|---|
| hydraulics | 5-15 | 26/21/26/27/17 (17 / 26 / 27) | 9/9/13/13/12 (9 / 12 / 13) |
| electrical | 5-15 | 18/27/19/17/22 (17 / 19 / 27) | 6/11/8/8/13 (6 / 8 / 13) |
| landing_gear | 1-5 | 15/12/14/18/14 (12 / 14 / 18) | 5/1/4/0/4 (0 / 4 / 5) |
| apu | 6-20 | 17/17/16/24/17 (16 / 17 / 24) | 11/12/10/12/12 (10 / 12 / 12) |
| ecs | 4-15 | 7/4/11/5/11 (4 / 7 / 11) | 8/5/12/6/14 (5 / 8 / 14) |
| avionics (aggregate, incl. NFF) | 25-80 | 86/82/76/80/84 (76 / 82 / 86) | 66/68/57/60/79 (57 / 66 / 79) |
| engine (on-wing unscheduled incl. performance) | 0-3 | 0/0/0/1/0 (0 / 0 / 1) | 0/0/0/1/0 (0 / 0 / 1) |
| brakes | 0-20 (planned share > 0.7) | 4/4/8/3/6 (3 / 6 / 8) | 7/9/7/5/6 (5 / 7 / 9) |
| tires | 0-60 (planned share > 0.7) | 32/37/34/25/38 (25 / 34 / 38) | 31/39/40/31/47 (31 / 39 / 47) |

NFF share of unscheduled removals (all five seeds pooled, AFTER): avionics ~0.42 (HIGH); hydraulics / electrical / ecs / apu ~0.17
pooled, 0 to 0.5 per kind and seed because counts are small (MODERATE); brakes / landing_gear / tires / engine < 0.05 (LOW). The
test asserts avionics 0.2-0.65, moderate 0.03-0.35, mechanical <= 0.12 and mechanical < moderate < avionics.

### What changed and why (all through physics / life parameters; policy thresholds untouched in this revision)
* **Landing gear** (was one unscheduled removal per ~6k FH): strut-seal pressure loss and hard-landing leak increments are
  serviced on the aircraft at inspection (`SERVICE_LEAK_KPA`, strut recharge; seal wear itself is not reset), so a leak or a
  routine hard landing is an inspection + servicing, not a removal. A strut is removed only for a severe finding: fatigue,
  crack growth, an overload landing (`hard_landing_damage`, hazard 1e-4 -> 4e-5, effect 0.2 -> 0.8) or a leak so fast it hits the
  limit between inspections.
* **Hydraulics**: all per-flight damage rates divided by 4.5 (nominal life 2000 -> 9000 flights, ~18k FH). The earlier ~27 per
  year was an MTBUR of ~3.9k FH; pumps/systems are of order 10^4 FH. `param_sigma` 0.25 -> 0.45 and the reference Weibull shape
  `failure_shape_accel` 6.0 -> 3.0 (measured 2.9): 6.0 meant a near-deterministic wear-out (CV ~0.2), whereas mixed contamination,
  leak and wear causes give beta of order 2-3.
* **Electrical**: per-flight fade divided by 3.1 (life 2000 -> 6200 flights); calendar fade (sqrt days) unchanged;
  `param_sigma` 0.25 -> 0.40; `failure_shape_accel` 4.5 -> 2.8 (measured 2.8).
* **APU**: `start_loss_c` 0.024 -> 0.015, `hour_loss_c` 0.012 -> 0.0075.
* **Avionics**: `bite_rate` 0.015 -> 0.005 per cycle (the repeat-BITE trigger fired ~150 times per 24 tails per year once NFF boxes
  stayed installed).
* **Engine**: shop visits driven by the health threshold carry reason `performance` (was `scheduled`); in the first simulated
  year almost no engine reaches the threshold (pre-ageing is capped at 0.6 of life), so the engine count stays 0-1. In steady
  state expect ~6 shop visits per 24 tails per year (48 engines, ~18k flights between visits).
* All of these are ASSUMED (no citation). None of the changed rates is data-calibrated (see SIM-V2-CALIBRATION.md).

### No-fault-found priors (assumed, no citation; `app/sim/v2/world.py: FALSE_CALL`)
| class | kinds | mechanism | prior |
|---|---|---|---|
| HIGH | avionics | repeat BITE reports (>= 5 new BITE counts in 60 cycles) remove the box; shop finding drawn from `avionics.nff_prob` (0.35 base, rising with BITE history) | `nff_base` 0.35 |
| MODERATE | hydraulics, electrical | spurious indication on a serviceable unit (hi > 0.6), then removal | 0.10 per 1000 cycles x 0.5 call |
| MODERATE | ecs, apu | same | 0.12 per 1000 cycles x 0.5 call |
| LOW | brakes, landing_gear | same | 0.02 per 1000 cycles x 0.2 call |
| LOW | tires | same | 0.02 per 1000 cycles x 0.1 call |
| LOW | engine | same | 0.01 per 1000 cycles x 0.2 call |

The second number is the false-call probability: the share of spurious indications acted on by pulling the unit rather than
cleared by troubleshooting. The unit then returns unchanged (`nff_return`).

### Early-life hazard and maintenance-induced faults (assumed, no citation)
After a `replace` / `overhaul` the degradation rate of the unit is multiplied by `1 + (M - 1) exp(-age / tau)`
(`world.INFANT`: avionics 1.5 / 200 cycles, electrical and hydraulics 1.3 / 150, apu 1.3 / 100, ecs 1.2 / 100, landing_gear
1.15 / 200, engine 1.15 / 300; consumables have none). Extra wear is (M - 1) x tau cycles of life, a small fraction of a
percent to a few percent of part life. A `repair` or `overhaul` done by the auto-maintenance policy leaves a new low-severity
(0.25) fault with probability 0.03 / 0.04 (`world.MX_INDUCED_P`), recorded in ground truth and as `induced_fault` on the event.

### Constants (one per concept)
`app/sim/constants.py`: `FLIGHTS_PER_DAY = 6.0` (drivers, runner, pack, planner fallback and LLP all use it; the LLP and planner
docs previously assumed 4/day); `ENGINE_MEAN_ON_WING_FLIGHTS = 206.3 x 87` (C-MAPSS fit mean x flights per model cycle);
`LLP_CYCLE_LIMITS` (used by `app/ops/llp.py`; the engine model's single LLP counter uses the HPC-rotor limit, 25,000).
Other numbers you may meet are scales of a different thing, not alternative engine lives: 206 = C-MAPSS model cycles; 1500 =
v1 telemetry run length; 8200 = `LEGACY_V1_ENGINE_COUNTER_CAP`, the v1 fleet's cycles-since-overhaul cap (used only by the v1
`GET /insights/reliability`). `tests/test_sim_v2_constants.py` asserts nobody re-declares them.

### Labels
Parameter comments: "assumed (no citation)" = a prior with no source; "calibrated" is reserved for parameters actually overridden
by data (0 of 36 hydraulics, 3 of 36 electrical per SIM-V2-CALIBRATION.md), and "sensor-shape checks" describes the rest of the
comparison to rig / cell data.
