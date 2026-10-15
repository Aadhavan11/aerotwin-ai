# Rotable spares pool sizing (SIMULATED)

Advisory only. All inputs are simulated removal histories plus assumed priors. **Synthetic != field validity**: nothing here is a provisioning source.

## Method
1. **Demand.** Removals (replace / overhaul / nff_return) are bucketed weekly per (kind, home station); removals carry no station, so a tail's
   home follows the golden convention (SIM-i is based at station `(i-1) mod n`). Each series is classified by ADI and CV2 (cut-offs 1.32 / 0.49):
   smooth and erratic use **Croston**, intermittent uses **SBA** (Croston x (1 - alpha/2), alpha 0.1), lumpy uses the **Willemain-style
   bootstrap** (Markov zero/non-zero chain, jittered sizes). Lead-time demand mean and p50/p90/p95 come from the bootstrap.
   NFF share (kind level) is the repairable-return part of the pipeline; a condemned fraction of non-NFF removals is scrapped and repurchased.
2. **Sizing.** VARI-METRIC two-echelon approximation per part: a central depot (the station that ships the part to nobody in the station
   table, else ATL) repairs all units; bases are resupplied one-for-one. Depot pipeline = units in transit + repair/repurchase (negative
   binomial, variance from the weekly variance-to-mean); depot delay = EBO0/lambda0; base pipeline = demand over (transport + depot delay) with
   Sherbrooke's variance term. Fill rate = P(pipeline <= s-1); EBO = E[(pipeline - s)+]. Greedy marginal analysis adds the unit with the best
   EBO reduction per dollar across all parts, either to a per-station target fill, to a budget, or while saving > holding cost (cost mode).
   AOG risk over the horizon = 1 - exp(-lambda H (1 - fill)). Sensitivities re-evaluate the recommended stock at shop TAT +/-30% and demand +/-20%.
3. **Validation.** `spares.simulate` is a seeded discrete-event model (500 trials x 365 d after a 120 d warm-up, infinite repair capacity,
   FIFO depot queue). `spares.validate` also runs stress cases: gamma demand frailty (CV 0.6) and removal campaigns (clusters of 3).

## API
`GET /ops/spares?target_fill=&budget_usd=&validate=` (default target 0.95), `GET /ops/spares/demand`. Library: `spares.from_simulator(world | run_dir | Dataset)`.

## Assumptions (priors, not data)
Part prices from `app/solver/costs.py`; shop TAT per kind (7-30 d), NFF check 3 d, condemned 8% of non-NFF removals, buy lead 90 d, holding 15%/yr,
AOG cost = `DELAY_COST_USD_H` per hour, transport days from the station table (or distance when the part is not in the golden stock table).
One removal = one rotable (brakes are a lumped per-tail unit in the reliability pack). Engines and landing gear are not modelled.
Current stock exists only for the four golden parts; other kinds show `current: null` and are excluded from shortfalls.

## Limits
* Base case: analytic vs simulated fill agrees within ~0.2 pp on the golden fleet. The approximation breaks under high demand variance
  (up to 5-14 pp on lumpy tire/avionics series) and, severely, under simultaneous campaign removals (20-60 pp): treat recommended stock as a
  floor when campaigns are possible.
* No repair capacity limit, no lateral supply between bases, no cannibalisation, no emergency purchase; two echelons only (main bases and
  outstations differ in label, not in model).
* Forecasts use one simulated year; scarce series (a few events) are dominated by noise.
