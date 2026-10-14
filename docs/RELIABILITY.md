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
(~15 s once). Note the 2,190 nominal cycles span ~504 calendar days in the simulated clock, not 365.

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
