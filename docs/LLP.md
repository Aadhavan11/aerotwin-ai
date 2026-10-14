# LLP tracking and shop-visit forecast (SIMULATED)

Code: `app/ops/llp.py` (model), `app/ops/llp_api.py` (routes, auto-mounted by `app/insights/router.py`).
All data is SIMULATED; part numbers/serials are fictional; limits are documented priors. Advisory only.
Every payload carries `"simulated": true`, `"provenance": "SIMULATED"`, `disclaimer`, `as_of` (sim clock, ISO UTC) and
`basis` ("twin" or "seeded simulated fleet"). Routes are GET only, open to every signed-in role (no `GUARDS` rule).

## Parts per aircraft (12)

Per engine (`engine_1`, `engine_2`; position `"engine_1:<key>"`): `fan_disk` (30,000 cyc), `hpc_rotor` (25,000),
`hpt_disk` (20,000), `lpt_shaft` (30,000). Per airframe (position = key): `mlg_leg_l`, `mlg_leg_r` (25,000),
`nlg_leg` (30,000), `mlg_retract_actuator` (20,000). Calendar limits 15-25 years.

* Cycles since new (`csn`) = deterministic pre-owned stagger (hash of seed, tail, position) + live counter
  (engine component cycles for engine parts, `Aircraft.cycles` for airframe parts). Seeding wraps at 97% of the limit.
* `remaining_cycles = cycle_limit - csn`; `remaining_days` at the tail's observed flights/day (legs / span of the leg
  window; 4/day default); `limit_date` = earliest of the cycle and calendar date; `limit_driver` = `cycles` | `calendar`.
* `status` on effective remaining cycles (min of cycles and calendar-days x flights/day):
  `ok` >= 2000, `watch` < 2000, `alert` < 500, `expired` <= 0.
* Replacement: `llp.record_replacement(tail, position, at_cycles, when=None, serial=None)` resets a part;
  `llp.apply_install_registry(registry, counters)` bridges `InstallRegistry` (tolerates absence).

## GET /ops/llp?tail=SIM-001  and  GET /ops/llp/{tail}

```
{ simulated, provenance, disclaimer, as_of, basis,
  tail: str|null, count: int, summary: {ok, watch, alert, expired},
  aircraft: {cycles, cycles_per_day, location},          // only on /ops/llp/{tail}
  parts: [ { tail, position, part_key, label, ata, scope ("engine_1"|"engine_2"|"airframe"),
             part_no, serial, cycle_limit, calendar_limit_years, csn, remaining_cycles, remaining_days,
             cycles_per_day, limit_date, limit_driver, installed_date, replaced, status,
             simulated, provenance } ] }                 // sorted by remaining_days ascending
```
Unknown tail: 404 `{error:"not_found", detail}`.

## GET /ops/shop-visits?tail=SIM-001

Drivers per engine (flights = cycles), earliest p50 wins:
* `egt_margin`: `egt_margin_c` telemetry Theil-Sen trend over the last 30 cycles when >= 6 points (loss rate clipped to
  0.5x-3x of the 0.007 C/cycle prior), else a latent prior (basis `latent_prior`, capped by 15 + 55 x health). Floor 15 C.
* `llp`: worst effective remaining cycles of the engine's four LLPs (+/-8% planning spread).
* `rul`: health RUL p10/p50/p90. The model is capped at 125 cycles; a capped RUL is censored (`rul_censored: true`) and
  is not used as a driver.
Window = (min p10, min p90) over drivers, dated at the tail's flights/day.

```
{ simulated, provenance, disclaimer, as_of, basis, tail, horizon_months: 12, count,
  drivers: {egt_margin: n, llp: n, rul: n},
  engines: [ { rank, tail, engine, component_id, driver ("egt_margin"|"llp"|"rul"), driver_llp_part: str|null,
               overdue, p10_flights, p50_flights, p90_flights, p10_date, p50_date, p90_date, cycles_per_day,
               drivers: {<driver>: {p10_flights, p50_flights, p90_flights}},
               egt_margin: {margin_c, loss_c_per_cycle, basis, floor_c}, rul_censored, simulated, provenance } ],
  pipeline: [ { month 1..12, days_from, days_to, expected, cumulative_expected, p50_count, cumulative_p50_count } ] }
```
`expected` is the smoothed expected number of shop visits in the month (sum of per-engine probability mass from a
piecewise-linear CDF through p10/p50/p90); overdue engines land in month 1. `p50_count` is the hard count at p50.
Both cumulative series are monotone non-decreasing.
