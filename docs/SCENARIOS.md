# Stress / edge scenario suite (v2 World)

Code: `app/sim/v2/scenarios.py`; tests: `tests/test_sim_v2_scenarios.py`. All data is SIMULATED. The expectations below are
direction-and-rough-size sanity checks of the simulator, NOT validation against real fleets.

## Method

* One `Scenario` = id + description + `perturb(Setup)` + expected-direction checks. `run_suite` runs a baseline and each
  scenario with the SAME seed and tails (common random numbers: all draws are keyed on (seed, stream, cycle)), so deltas
  come from the perturbation. Worlds are `stochastic=False` (sensor layer off; it never feeds maintenance).
* Horizon: 1 calendar year (2190 cycles at utilisation 1; 3066 at x1.4). Default tests use 4 tails; the tables below were
  produced with 8 and 24 tails, seed 1234. Single seed: small counts are noisy (Poisson sigma ~ sqrt(n)).
* Mechanisms: profile transform in a `World` subclass (OAT, wet runway, density
  altitude); `drivers.STATION_ENV` mutated for the run and restored (dust, humidity); `World(utilisation=)`;
  `live.age_world` (pre-ageing); `World.inject_fault` (at cycle 100); shortage = subclass `_do` + `MelBook` category-A items.
* Utilisation x1.4: `LEGS_PER_DAY` stays 6 and one world cycle is always one flight leg. x1.4 means 8.4 legs/day, so a
  year is 3066 cycles and each cycle advances the calendar 1/1.4 days. Per-cycle physics is unchanged (the per-tail
  `utilisation` array is not read by any model), so only calendar-driven terms and day-based intervals differ.
* Pre-ageing +5 y: engines to wear min(10950 flights / mean on-wing life, 0.6) = 0.6; hydraulics, apu, landing gear,
  avionics, ecs, electrical to 0.35 (ASSUMED prior); brakes and tires 0 (consumables reset on wear).
* Part shortage: the sim has no stock. A replace/overhaul falling due when the tail's arrival station is DEN/MIA/SFO is held
  14 days (unit keeps flying, MEL category A with `limit_days`); exposure = unit-cycles flown with work pending.
* "Failed in service" = a `failure` removal executed while the unit's `failed` flag was set (not avionics shop findings).
* Worst-case health = 5th percentile / minimum of `hi` over all units, sampled every 50 cycles.

## Scenarios, expectations and observed deltas (24 tails, 1 yr, seed 1234)

Baseline: 142 unscheduled removals (incl. 38 NFF, share 0.27), MTBUR 740 FH, engine shop visits 0, planned/unplanned
2.33, deferrals 22, failed-in-service 8, hi p5 0.358, hi min 0.101. (Re-run after the fault-effect fixes below; the previous
table had 141 / 746 FH.)

| Scenario | Stresses | Expected | Observed (delta vs baseline) |
|---|---|---|---|
| heat_plus15 | OAT +15 C | hot kinds (avionics, ecs, electrical, tires) >= baseline | unsched 142 -> 159; avionics 28 -> 35, apu 9 -> 18, tires 41 -> 43, electrical 6 -> 3 (noise, see below); MTBUR 661; p5 0.344 |
| ageing_plus5y | pre-aged fleet | engine/hydraulics/apu up | unsched 154; engine 0 -> 8 (+1 shop visit), hydraulics 4 -> 9, apu 9 -> 7 (down); deferrals 22 -> 67; p5 0.289; NFF share 0.19 |
| utilisation_x1.4 | 8.4 legs/day | absolute up, per-1000FH within +/-40% | unsched 142 -> 205 (+44% for +40% cycles); rate per 1000 FH 1.35 -> 1.39 (+3%); MTBUR 718 |
| hot_dusty | dust x2, humidity +20 pp | dust/humidity faults up | unsched 158 (+16), engine 0 -> 2, ecs 7 -> 9, tires 41 -> 50; failed-in-service 8 -> 12 |
| part_shortage | no spares DEN/MIA/SFO, 14 d | deferrals + exposure | 110 replacements held, 6913 unit-cycles exposure, deferrals 22 -> 130, failed-in-service 8 -> 51, unsched 166, planned/unplanned 2.33 -> 1.46 |
| double_engine_fault | 2 x hpt_blade_erosion sev 1 on tail 0 | >= 2 engine events | 2 fault records, 19 detections/actions on the tail's engines, 10 on-wing repairs (each <= +0.15 health) and 2 shop visits through the `performance` path (hi < 0.15) |
| hydraulic_failure | pump_cavitation sev 1 on tail 0 (no hand-raised state any more) | brake/gear stress not below baseline, target-tail mean above | target-tail mean stress brakes 1.162 -> 1.171, gear 1.130 -> 1.137; peaks 1.5 / 1.4 in both (baseline tail 0 saturates the clamps too); unscheduled totals unchanged (the failing pump is removed as planned condition-based work) |
| wet_winter | 60% wet, OAT -10 C | brake/tire wear up | unscheduled 142 -> 113; unscheduled brakes 8 -> 5, tires 41 -> 34, but ALL real brake+tire removals 333 -> 343 (see below); p5 0.372 |
| combined_worst | all of the above (heat, not winter) | worse than any single | unsched 282 (x2.0), engine shop visits 19, MTBUR 522, failed-in-service 105, p5 0.186, hi min 0.0, planned/unplanned 1.68 |

Note on utilisation: 205/49056 FH vs 142/35040 FH gives +3% per flight hour, inside the +/-40% band.

Scaled fleet (8 tails, seed 1234) for the same suite: baseline 39 unscheduled; heat 37; ageing 41; x1.4 53; hot_dusty 45;
shortage 48 (32 held, 2171 exposure cycles, failed-in-service 15); double engine 48 (2 shop visits); hydraulic failure 38;
wet_winter 33; combined 100 (engine shop visits 5, failed-in-service 40, hi min 0.0).

## Multi-seed checks behind findings 4 and 5 (8 tails, 1 yr, seeds 1 / 7 / 99 / 1234 / 4242, baseline -> scenario)

| quantity | per seed | sum |
|---|---|---|
| wet_winter, real brake removals (wear limit + findings + failures) | 24->27, 19->28, 23->28, 26->29, 25->34 | 117 -> 146 (+25%, 5 of 5 up) |
| wet_winter, real tire removals | 82->87, 81->90, 81->84, 83->92, 87->85 | 414 -> 438 (+6%, 4 of 5 up) |
| heat_plus15, electrical real removals | 2->4, 3->3, 2->3, 3->4, 3->5 | 13 -> 19 (4 of 5 up, none down) |
| heat_plus15, electrical unscheduled | 1->3, 2->0, 1->2, 1->1, 1->3 | 6 -> 9 |
| heat_plus15, hot-kind unscheduled sum (avionics, ecs, electrical, tires) | 23->20, 28->33, 24->27, 20->17, 30->34 | 125 -> 131 (within 1 sigma) |

Wear mechanism alone (maintenance off, 300 cycles): brake wear per cycle rises ~18% in wet_winter, tread loss ~5%.

## Findings fixed in this revision

1. `World.inject_fault` was inert early in a run (the back-dated onset went negative and -1 meant "no fault"). `FaultInjector`
   now marks "no fault" with `NO_ONSET` (int64 min) and `active()`; a back-dated onset may lie before cycle 0 and the fault is
   active at the requested severity immediately, then ramps over its documented length. Snapshots / restore / state hash carry
   the explicit marker. `ready_cycle` was removed from the scenario module.
2. Severity-1 `pump_cavitation`: `effect_mult` 0.7 -> 0.35 (drift multiplier 2.9x -> 23x) plus case-drain flow rising with
   severity x efficiency loss. Hydraulics hi falls below the 0.3 finding threshold in about 300-700 cycles, and a removal follows
   within ~1000 cycles (seeds 1/7/99/1234: first removal at cycle 413 / 713 / 343 / > 1000; seed 1234 has a slow per-unit rate).
   Severity 0.3 stays modest: 1.9x drift (but note an injected fault keeps ramping to severity 1; the injected severity is the
   starting point).
3. Severity-1 `hpt_blade_erosion`: progress share 0.5 -> 0.6 and core wear accelerated by (1/0.6)^3 = 4.6x, so the engine reaches the
   hi < 0.15 `performance` shop visit after about 860-1230 cycles (seeds 1/7/99). On-wing repair keeps `repair_keep` 0.85 of all
   damage (was 0.5 progress and 0.5 faults): maximum health gain per repair 0.15 (observed gains 0.04-0.05; the 0.39 -> 0.66 jump is
   gone), and an engine repair clears fouling (wash) but not blade erosion. Engine findings are now repaired rather than replaced
   (`POLICY["engine"].finding_action`), so shop visits come through the performance path.
4. Wet winter (finding 4): not a missing term. Real brake and tire removals rise across seeds. The earlier "fewer removals" read the
   *unscheduled* count: cold lowers tire permeation (`exp(0.03 * (OAT - 15))` ~ 0.5x at -10 C) and heat-driven faults, so
   unscheduled tire / brake events fall while the wear-driven (planned, `wear_limit`) removals rise. Brake wear +18% (wet energy
   and `WET_FACTOR` outweigh the halved oxidation term). No constant was rebalanced. `p_cold` is cold-referenced, so no extra
   under-inflation term was added (an operator inflates at ambient).
5. Heat and electrical (finding 5): small-count noise, not a wiring bug. The Arrhenius factor in `electrical.step` uses OAT through
   `oat_gain` and is applied to calendar + cycle fade and resistance growth; over five seeds electrical removals rise 13 -> 19.

Tests: `tests/test_sim_v2_fault_effects.py`.

## Model behaviour that still looks implausible (known limitations)

1. Baseline target tail saturates the coupling clamps: with the stronger cavitation, a natural onset on tail 0 hydraulics
   (about one per two pumps per year) pushes tail-0 brake / gear stress to 1.5 / 1.4 in the baseline, so the
   `hydraulic_failure` scenario compares the target-tail MEAN stress, and its unscheduled counts equal the baseline (the failing
   pump is removed earlier, as planned work, not as a failure).
2. Part-shortage `failed_in_service` jumps 8 -> 51 because held units run on to their failed flag; real operations would ground
   the aircraft (AOG), which the sim has no concept of. Exposure numbers are an upper bound on flown risk.
3. Heat: only avionics and apu rise clearly at one seed; electrical and tires are within noise at 24 tails. The test asserts
   the hot-kind SUM is within 2 Poisson sigma of >= baseline and electrical real removals summed over seeds not lower.
4. An engine with a persistent erosion fault is repaired at every 45-day borescope (up to ~4 repairs, each +0.04-0.05 health)
   before its shop visit; a real operator would pull it earlier on EGT margin trend. Unscheduled engine counts per 24 tails
   per year are 0-2 in the five-seed band run (band 0-3).
5. A severity-1 injection starts at severity 1 (back-dated); the documented ramp applies from the onset that the injection
   implies, not from 0.

## Caveats

* One seed, small counts: individual kind deltas are not significant; only fleet totals and large effects are.
* Ageing wear fractions other than the engine mapping are assumed priors.
* The environment patch is process-global during a run (not thread-safe). Regenerate with `scenarios.run_suite(1234, 24)`.
* Runtime: default test file about 50 s; fault-effect tests about 90 s; full-fleet variants are `slow` (`RUN_SLOW=1`).
