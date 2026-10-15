# What this simulator cannot tell you

Audience: a reviewer deciding how much weight to put on any number the system shows. Everything here is SIMULATED.
Per-model detail is in `docs/model-cards/`. The sources for the statements below are the repository docs and code
named in each item.

## 1. What this is

A synthetic fleet (24 tails by default, 6 flight legs per day each) driven through per-kind degradation models, with
planning, reliability, risk and ML layers on top. It exists to exercise software and workflows. It is not a digital
twin of any real aircraft, operator or manufacturer, and nothing in it has regulatory meaning.

**Not for airworthiness decisions.** No output of this system may be used for any airworthiness, dispatch, maintenance-planning or life-limit decision. No airline, manufacturer, MRO or regulator data was used, there is no affiliation with or endorsement by any of them, and part numbers, serials, tail names and life limits are fictional or assumed.

## 2. Structural limits

* **Lumped units.** One state per tail per kind (engines: two per tail). Brakes: one state for all braked wheels
  (`N_BRAKES` shares energy). Tires: one state for the main-gear tire set. Hydraulics, electrical, ECS, APU: one
  per-tail aggregate each. Avionics: one box standing for many LRUs. Statistics at part-number or position level
  (per wheel, per pack, per LRU, per pump) do not exist in the models.
* **No affiliation, no real data.** No airline, manufacturer, MRO or regulator data is used, and nothing is endorsed
  by or derived from any of them. Tail names (`SIM-xxx`), part numbers, serials and station codes are fictional or
  generic. The 8 stations use a simplified static climate table (`_CLIMATE` in `app/sim/profile.py`: elevation, a
  fixed arrival OAT with plus/minus 2 degC jitter, runway length, wet probability) with no seasons or weather
  history; `STATION_ENV` in `app/sim/v2/drivers.py` gives fixed humidity and dust values.
* **Synthetic schedule.** Flights come from a synthetic generator; the clock advances `1 / LEGS_PER_DAY` days per
  cycle (6 legs/day). Utilisation scatter between tails does not stretch the clock. Real rotations, seasonality,
  night-stop patterns and flight-length mix are absent.
* **Two LLP notions.** The v2 engine model carries ONE surrogate life-limit counter per engine (about 25,000 flights, HPC-rotor
  value) that can fail the unit; `app/ops/llp.py` tracks FOUR LLPs per engine (20,000-30,000 cycles) plus airframe parts. They do not
  read each other and can disagree (`docs/SIM-V2-ENGINE.md`, `docs/LLP.md`).
* **Maintenance clears every fault.** `World._do` clears ALL active hidden faults of a unit on any non-inspect action regardless of
  the action's `q`; a partial repair still removes every injected fault (`docs/SIM-V2-SERVICE.md`).
* **Engine-to-ECS coupling uses one engine.** `engine>ecs` maps a tail's ECS unit to its first engine unit (engine_1) only.
* **No regulatory meaning.** The ATA-style codes are simplified (`app/taxonomy/ata.py`), the MEL register is a
  fictional catalogue (`docs/MEL.md`), life limits in `docs/LLP.md` are priors. None of it is an MMEL/MEL, MPD, AD,
  SB, manual or life-limit source. It must not support any airworthiness, dispatch or maintenance decision.
* **Assumed priors.** Apart from the engine surrogate and three battery values (below), every model parameter is an
  assumed prior (code comments say "assumed (no citation)"; older text said "literature prior, unverified") with no recorded citation. Rates were additionally
  tuned so one simulated year lands in assumed removal bands (`docs/RELIABILITY.md`); the bands are priors too,
  so the check is partly self-referential.

## 3. Data actually used

| model | dataset | what it supported | what it did not |
|---|---|---|---|
| engine | NASA C-MAPSS FD001-FD004 train files (research simulation, Saxena and Goebel 2008) | sensor curves vs life fraction, noise, operating-condition baselines, life distribution | any real engine behaviour; the 87-flights-per-cycle time scale; module physics; fault loadings; LLP |
| hydraulics | UCI hydraulic rig (ZeMA, lab bench) | sensor sensitivities, noise floors, detectability | any rate or limit; 0 of 36 parameters changed; cooler fault in the rig has no model counterpart; pressure sign conflicts |
| electrical (battery only) | NASA PCoE cells B0005/6/7/18 | fade shape consistency, 3 values: `resistance_fade`, `capacity_noise`, `resistance_noise` | temperature acceleration, aircraft battery life, generator/IDG |
| brakes, tires, landing gear, APU, ECS, avionics | none | nothing | everything: no public run-to-failure data exists for these in the repository |

C-MAPSS is distinguishable from the surrogate: a classifier separates simulator output from real C-MAPSS rows with
AUC about 0.64 (`docs/SIM-V2-ENGINE.md`; 0.5 would be indistinguishable). The reference rows for that check are the
same rows used for fitting, so the check is in-sample.

## 4. Machine learning: circularity and shift

From `docs/ML-GENERIC.md`:

* Sim-trained models for the eight non-engine kinds are trained and tested on output of this simulator, split by seed.
  The score measures recovery of the simulator's own degradation laws. It is not an accuracy claim about real components.
* The rule-based demo model it is compared with uses fixed limits and linear wear that do not match v2 degradation,
  so "learned beats rule" is largely a statement about the rule.
* The artifacts were retrained on 2026-10-14 after the simulator changed (removal rates, no-fault-found returns, infant
  mortality); older figures no longer apply.
* Headline coverage is flattered by rows at the RUL cap. On below-cap rows, conformal coverage in distribution is
  64-83% for seven kinds and 19% for landing_gear (target 80%).
* Under the held-out stress regime coverage on below-cap rows falls to 34% for ecs and 32% for hydraulics (57-68% for the
  others), and for apu and hydraulics the rule model has the lower MAE. Conformal calibration on in-distribution units
  does not repair covariate shift; the 80% label does not hold outside training.
* About 31-32 hold-out units per kind from 4 seeds; row counts overstate effective sample size. Anomaly thresholds give high
  false-positive rates for some kinds (16-32% for avionics, electrical, hydraulics, landing_gear; under shift up to 65%).
* The engine RUL scorer (FD001) has held-out MAE 10.8 cycles on real C-MAPSS test engines and 13.2 on simulator
  windows; it is not meaningful on FD002-FD004 variants.
* Serving `age` is verified only against the twin's own cycle accounting; pre-worn units start with low age and worn
  channels, a combination training saw only through stress variation.

## 5. Cost, planning and Monte-Carlo priors

* Costs (`app/solver/costs.py`, `docs/COST-MODEL.md`): parts, labor rate, bay rate, per-job overhead, delay hourly cost,
  per-task deferral allowance, AOG cost per hour and cancellation cost are illustrative priors, not quotes or
  operator figures. Plan-vs-baseline cost differences follow from these constants.
* Monte-Carlo risk (`docs/MONTECARLO.md`): part-delay sigma, no-show probability, overrun distribution and weather
  probability are assumed distributions in `MCConfig`. Failure-before-job uses the RUL p10/p50/p90 of the health model,
  which inherits the ML limits above. Output probabilities (late, AOG, deadline miss) are conditional on those priors
  and are not forecasts. Trials within a plan are not validated against any outcomes.
* Shop-visit forecast (`docs/LLP.md`): trend extrapolation of EGT margin and LLP counters, with assumed limits and
  an assumed engine time scale; dates are planning illustrations.

## 6. What each number can and cannot be used for

| output | acceptable use | not acceptable |
|---|---|---|
| health index, RUL, anomaly flags | demonstrating UI, alerting and workflow logic; testing pipelines | judging real component condition or remaining life |
| removal rates, MTBUR, NFF rate, Weibull fits (reliability pack) | exercising the analytics, comparing code paths | reliability claims, fleet benchmarks, contract or warranty arguments |
| ATA grouping, Pareto, alerts | demonstration of the method | operator reporting, regulator submissions |
| LLP status, shop-visit window | showing how tracking and forecasting would be presented | life-limit tracking, planning real engine removals |
| MEL register | workflow demonstration | any dispatch relief or restriction determination |
| plan cost and Monte-Carlo risk | comparing optimisers on the same assumed prices | budgeting, business cases, risk acceptance |
| cross-kind coupling effects | illustrating cascade logic | quantifying real dependencies (edge gains are assumed) |

## 7. Claim, evidence, strength

Strength key: **none** (assertion only), **weak** (prior-consistency or in-sample), **moderate** (distributional
comparison to an independent public dataset), **strong** (out-of-sample prediction on real fielded data; none exists here).

| claim | evidence | strength |
|---|---|---|
| Engine sensor trajectories look like C-MAPSS FD001 | marginals by life bin (KS about 0.03-0.04), AUC 0.64 vs FD001 train (same data as fit) | weak to moderate (in-sample, research data) |
| Engine life of about 18k flights | assumed scale factor times fitted life | none |
| Engine RUL scorer works on C-MAPSS | held-out FD001 test MAE 10.8 | moderate for C-MAPSS only |
| Hydraulics sensor response matches reality | UCI rig sensitivities; 0 parameters changed; sign conflict on leak and precharge | weak |
| Hydraulics failure timing | nominal-life prior only | none |
| Battery fade shape is plausible | 4 lab cells, held-out error about 5.5 pp, shapes not distinguishable | weak |
| Battery resistance coupling value | fitted to the same 4 cells, wide interval | weak (in-sample) |
| Brakes, tires, gear, APU, ECS, avionics rates and limits | assumed priors and plausible-band tests | none |
| Cross-kind coupling | assumed gains, tested for mechanics only | none |
| Reliability pack formulas (rate, Garwood CI, MTBUR, NFF) are computed correctly | unit tests, code | strong for arithmetic only; says nothing about the inputs |
| Removal rates are plausible | assumed bands; median over 5 seeds inside the band and at least 4 of 5 seeds inside (`tests/test_sim_v2_removal_bands.py`) | weak (bands are priors too) |
| Sim-trained ML beats the rule model in distribution | sim hold-out seeds | weak (circular) |
| ML 80% interval holds | in-distribution at cap-dominated rows; fails under shift | weak; none under shift |
| Plan costs and risk probabilities | assumed prices and distributions | none |
| Deterministic simulation and snapshot/restore | tests for bit-identical resume | strong (software property) |

## 8. How to read a number from this system

State the model, the prior it came from, and that it is simulated. If a figure cannot be traced to a named dataset in
the table in section 3, treat it as an assumption of the authors.
