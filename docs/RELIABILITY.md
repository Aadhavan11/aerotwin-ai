# Reliability pack and ATA taxonomy

All data is SIMULATED (`provenance: "SIMULATED"`, `simulated: true`). ATA-style, simplified, fictional part numbers; advisory
only, not a certified reliability analysis.

## Code
* `app/taxonomy/ata.py`: chapters (05, 21, 22, 24, 29, 31, 32, 34, 36, 49, 72), sections (e.g. 32-41 wheels/tires, 32-42 brakes),
  `KIND_SECTION` (component kind -> section), `MODE_SECTION` (v2 fault mode -> section), `ata_for(kind, mode=None)`,
  `chapter_for`, `table()`. `app/sim/v2/faults.py` is not edited; `tests/test_ata.py` checks its inline chapters agree.
* `app/insights/reliability_pack.py`: `from_world(world)`, `from_run_dir(path)`, `cached_backfill(seed?, years, fleet)`,
  `compute(dataset)`, `pack()`, `weibull_fits()` (wraps the unmodified `app/insights/reliability.py` report).

## Definitions
Removal = action `replace` or `overhaul` (on-wing repairs/inspections excluded). Exposure = sum of unit flight hours/cycles of the
kind (per ATA chapter: kinds summed, i.e. unit-hours).
* Removal reasons: `failure`, `inspection_finding`, `nff` are unscheduled. `scheduled`, `opportunistic`, `deferred_defect_rectification` and `wear_limit` are planned. `wear_limit` = tire/brake change (retread or replace) because the wear / retread limit was reached with no active fault, cut or pressure loss; it is a consumable change, never an unscheduled removal. Cuts/FOD, leaks, faults and hard failures stay `failure` / `inspection_finding`.
* MTBUR = exposure / unscheduled removals (FH and FC); MTBF = exposure / confirmed failures (reason `failure`, not NFF).
* NFF rate = NFF removals / unscheduled removals. Premature-removal rate = removals with `health_before >= 0.5` / removals.
* Removal rate per 1000 FH with exact (Garwood) 95 % Poisson interval.
* AMOS-style alert: 30-day periods, rate per 1000 FH (FH spread uniformly over the calendar span); latest complete period vs
  mean + 3 sigma of up to 12 earlier periods (`warning` at +2 sigma; needs >= 3 earlier periods and >= 2 events to alert).
* Repeat defect: >= 3 unscheduled removals of the same tail + position within 90 days.

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
 metrics:{per_kind:[{kind, ata_chapter, ata_section, units, flight_hours, flight_cycles, removals, unscheduled_removals, failures,
                     nff_removals, mtbur_fh, mtbur_fc, mtbf_fh, mtbf_fc, nff_rate, premature_removal_rate,
                     removal_rate_per_1000fh, rate_ci_low, rate_ci_high, ci_level, alert_status}],
          per_ata:[{ata_chapter, title, kinds, units, ...same metric keys}]},
 pareto:[{ata_chapter, title, unscheduled_removals, share, cumulative_share}],
 alerts:[{kind, ata_chapter, status:"normal|warning|alert|insufficient_data", period_days, latest_rate_per_1000fh, latest_count,
          threshold_per_1000fh, warning_per_1000fh, window_mean_per_1000fh, window_sigma_per_1000fh, window_periods, rule}],
 repeat_defects:[{tail, kind, position, ata_chapter, count, window_days, first, last, reasons, nff_count}],
 top_contributors:{kinds:[{kind, ata_chapter, unscheduled_removals, share, cumulative_share, removal_rate_per_1000fh, nff_removals}],
                   tails:[{tail, unscheduled_removals}]},
 weibull:[{kind, components, samples, beta, eta_cycles, mtbur_cycles, b10_cycles, basis}]}
```
Metric values are `null` when undefined (no events).

## Simulator removal-rate bands (priors, not calibration)
One simulated year (2190 cycles = 365 days) gives unscheduled removals per kind inside industry-plausible prior
bands (typical MTBUR orders of magnitude; bands are priors, NOT validated against any operator's data).
`tests/test_sim_v2_removal_bands.py` pins them for seed 1234. Construction for every number below:
`build_world({"seed": S, "fleet": 24})` (initial_wear=True, as the reliability pack builds it), `run(2190)`,
`replace`/`overhaul` actions only. "Planned" is `not removal.unscheduled` (scheduled, wear_limit, opportunistic,
deferred_defect_rectification).

| kind | unscheduled band | unscheduled seed 1234 | unscheduled min-max, seeds 1/7/99/1234 | planned seed 1234 | planned min-max |
|---|---|---|---|---|---|
| hydraulics | 8-30 | 27 | 21-27 | 11 | 10-13 |
| electrical | 6-25 | 17 | 17-27 | 19 | 7-19 |
| landing_gear | 2-20 (lead-chosen prior; 18 per ~105k FH is acceptable) | 18 | 12-18 | 0 | 0-1 |
| apu | 6-25 | 24 | 16-24 | 9 | 9-24 |
| ecs | 5-20 | 5 | 4-11 | 8 | 3-8 |
| avionics (LRU, incl. NFF) | 25-80 | 80 | 76-86 | 5 | 5-7 |
| engine (on-wing unscheduled) | 0-4 | 1 | 0-1 | 0 | 0 |
| brakes | planned share > 0.7, wear_limit removals exist | 3 | 3-8 | 33 | 21-34 |
| tires | planned share > 0.7, wear_limit removals exist | 25 | 25-37 | 229 | 208-229 |

Results vary by seed; only seed 1234 is pinned by the test. Other seeds can fall slightly outside a band (seeds 1 and 7
give avionics 86 and 82 against 25-80, seed 7 electrical 27 against 6-25), which is expected for priors of this width.

How rates were set: the 6-legs/day calendar fix exposed that inspection findings were actioned at `hi < 0.6`, and a
`repair` only partly resets damage, so ordinary ageing of slow components was repaired or removed again and again
(hydraulics ~113, electrical ~81, gear ~26 per year). The model degradation rates are unchanged (their nominal-life
priors are pinned by per-model tests). Instead the policy thresholds were tuned to plausible bands: `FINDING_BELOW`
0.6 -> 0.4, and per-kind `KindPolicy.finding_below` 0.3 for hydraulics, electrical and landing gear and 0.2 for tires
(cuts / low pressure; wear itself is `wear_limit`). Calibration JSONs (`app/sim/v2/calibration/*`) and the fitted engine mapping are untouched.
