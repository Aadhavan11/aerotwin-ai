# Pardhu: status, message for the team, what to present, what is left

Branch: `claude/great-albattani-bv8i0b` (not merged to `main`, no pull request opened). Written 2026-10-11.
Scope of this branch: **only my package** (3.3 scheduler, 3.5 what-if, task cards, their tests). Nothing of Shamit's, Aashirth's or Aadhavan's was built.

## 1. Where things stand

* **Done and tested:** task cards, ground windows, `derive_tasks`, static baseline, greedy, CP-SAT, `plan_metrics` + a final validity guard, `apply_event_to_state`, `replan` with a diff, `compare` (scenarios), `simulate` (analytic + SimPy), the four endpoints I own, plan fixtures for the UI. **113 tests pass** (`python -m pytest tests -q`).
* **Built on a stand-in, not on the real fleet.** Shamit's models/fleet and Aashirth's twin snapshot do not exist in the repo yet, so I used `app/contracts/models.py` (a verbatim copy of `CONTRACTS.md` section 2, in its own commit) and `app/solver/golden.py` (a deterministic 24-aircraft, 8-station golden fleet). Every number below comes from that stand-in. **They must be re-run on the real snapshot at C1 before any number goes on a slide.**
* **Not started:** the doc's stretch goals (technician skill levels, spare-part repositioning, fleet-wide multi-fault tuning). Not checked: Python 3.11 (tested on 3.13), OR-Tools older than 9.10, anything LLM or UI related.

## 2. Message to paste in the group chat

> **Pardhu: scheduler + what-if is pushed** on branch `claude/great-albattani-bv8i0b` (nothing merged to `main`).
> What works, with tests (`python -m pytest tests -q` = 113 passing; `python scripts/plan_demo.py` prints the demo tables from a real run):
> `GET /plan`, `POST /plan/solve`, `POST /plan/replan`, `POST /whatif/compare`, `GET /metrics/plan`; `solve()` with static baseline, greedy and CP-SAT; `derive_tasks`, `ground_windows`, `apply_event_to_state`, `replan`, `compare`, `simulate` (analytic + SimPy); the 10 task cards in `app/copilot/kb/task_cards.yaml`.
> On the golden story: optimized plan fixes SIM-017's engine at DFW on day 1 with a kit on hand; the baseline (home base, last window) ships a kit to ATL and finishes about 46 h later; a 12 h DFW kit delay replans in under 0.1 s and moves the job to the next DFW window, with 0 flights delayed.
> I built on a stand-in fleet because `models.py`/fleet/twin were not in the repo. **Please read the asks below; they are small but they decide whether it works on the real data.**
>
> **Shamit:** (1) keep YOUR `models.py` when merging; mine is a verbatim copy. (2) `requirements.txt` needs `ortools`, `simpy`, `pyyaml` (not in the list in INTEGRATION.md), and `httpx` for the tests. (3) mount `app.solver.router.router` and `app.whatif.router.router`. (4) In your fleet generator please set `ComponentState.last_maintenance` (else no routine tasks appear), chain legs so each arrival airport equals the next departure airport, and keep the golden facts: SIM-017 has a ground window of 6 h or more at DFW that STARTS within a few hours of the sim start (otherwise a 12 h kit delay changes nothing and the replan demo shows an empty diff), ATL has 0 BORESCOPE-KIT, DFW has 2. (5) If `/scenario/inject` writes `part_delay`/`facility_closed`/`tech_unavailable` into the DB, call `replan(..., already_applied=True)`, otherwise the event is applied twice. (6) After `solve()` in your tool wrappers call `STORE.save(plan)` (from `app.solver.store`) so `GET /plan` shows it; call `STORE.reset()` on `/scenario/reset`. Details: `app/solver/README.md`. (7) If your `fixtures/plan_*.json` collide with mine in the merge, take mine (they come from the real solver).
> **Aashirth:** the scheduler only needs `rul_cycles.p10` (never p50) and `last_maintenance` on each `ComponentState`, and `twin.snapshot()` returning `FleetState` with `now` = the sim clock. If an anomaly lowers the RUL, new deadlines appear automatically; no extra hook needed.
> **Aadhavan:** real plan shapes are in `fixtures/plan_current.json`, `plan_baseline.json`, `replan_result.json`. Request bodies: `POST /plan/solve {mode, weights?, time_limit_s?}`, `POST /plan/replan {event}` (422 `{"error","detail"}` for a bad event), `POST /whatif/compare {scenarios: []}` (empty list returns the 5 default what-ifs; rows carry `name, feasible, metrics, infeasible_reasons, solve_seconds`). Show `Assignment.reason` in the side panel, `PlanDiff.moved[].why` in the Lab, a red banner from `infeasible_reasons`, and the SIMULATED badge.
> **All:** two choices I made that you may want to change (section 4 of `app/solver/README.md`): kit delay = units held until event time + delay (not deleted), and a small "finish sooner" preference of 0.05 per hour in the objective (deadlines stay hard).

## 3. What I present (about 3 minutes, every number from `python scripts/plan_demo.py`)

Numbers below are from the stand-in golden fleet on 2026-10-11 and **must be refreshed on the real snapshot**.

1. **The problem in one sentence (15 s).** "ML says SIM-017's engine has RUL p10 = 18 cycles. That is a hard deadline: Oct 17 04:40Z, the arrival of its 18th scheduled flight. My job: where and when to fix it, around flights, technicians, parts and hangars."
2. **Three planners, same snapshot (60 s).** Show the table from the script:

   | | static baseline | greedy | CP-SAT optimized |
   |---|---|---|---|
   | tasks scheduled | 5/5 | 5/5 | 5/5 |
   | late or unscheduled | 0 | 0 | 0 |
   | logistics cost | $2,200 | $1,400 | $600 |
   | objective (lower is better) | 41.36 | 18.27 | 14.28 |
   | SIM-017 engine fixed | ATL Oct 14 12:25Z, kit shipped from DFW ($800) | | DFW Oct 12 14:30Z, kit on hand, **45.9 h sooner** |

   Say the baseline out loud: "**home base, last ground window that fits before the deadline.**" Say it is simulated and relative to that baseline.
3. **Why this slot (20 s).** Read the `reason` of the SIM-017 assignment: kit on hand at DFW, engine technician DFW-T1 on shift, 14.5 h window for a 6 h job, 0 h extension, 104 h before the deadline, next best alternative.
4. **Disruption (40 s).** "DFW kit delayed 12 h." Replan takes **0.09 s**; the old plan would now break 1 hard rule (kit arrives after the job starts); SIM-017 moves to the next DFW window (Oct 13 14:40Z); 0 flights delayed; the diff says why. Then the honesty case: "if nothing works it says so, with the reason per window, and never moves a deadline". Example from the tests: an anomaly of severity 0.9 on SIM-012's engine leaves a deadline under a day away, and the plan reports "3 windows too short for the 6 h job, 1 where the kit cannot arrive in time".
5. **What-if lab (20 s).** 6 scenarios from `POST /whatif/compare` (later, another airport, kit delay, technician gone, hangar closed).
6. **Trust (20 s).** 113 tests; an independent checker re-verifies deadlines, overlaps, hangar slots, stock; 21 deliberate bugs (`python scripts/mutation_check.py` breaks one rule at a time in a scratch copy) were all caught; every plan is re-validated before it is returned.

Honest limits to say (SCOPE.md section 10): synthetic cards and fleet; savings are simulated and relative to the stated baseline; delay cascades are ignored by the plan metrics (the SimPy run shows them: baseline mean downtime 0.003 h, optimized 0, 20 runs, +-15 % durations); utilization looks tiny because 5 tasks run on a 120 h horizon.

Likely questions: *Why not let the LLM schedule?* Numbers and safety rules must come from a solver. *What if CP-SAT times out?* Best feasible plan, marked "not proven optimal"; invalid output falls back to greedy and says so. *Why did SIM-012 wait days?* Its deadline is 13 days away and waiting avoids an $800 shipment; the 0.05/h preference is a knob. *Is the 3 h extension safe?* It is a cap, it delays a flight, and it is costed.

## 4. What is left for me to complete my part (in order)

1. **Merge** into the team flow (Shamit merges to `main` from C1): `models.py` -> take Shamit's; `fixtures/plan_*.json` -> take mine; mount the two routers in `main.py`.
2. **C1 check on the real data:** run `python scripts/plan_demo.py` against the real `twin.snapshot()` (it uses it automatically once `app/twin/state.py` exists) and re-run `--export` to refresh the fixtures. Check the golden facts hold in Shamit's fleet; if not, tell him or adjust the golden scenario.
3. **Confirm the three choices** with the team: kit-delay semantics, the 0.05/h earliness term, the 3 h extension cap.
4. **Replace the numbers** in section 3 with the real run before they appear on a slide or in the video.
5. **Disclose AI assistance** in the README/Devpost if the event requires it (this module was built with Claude), and check the event rules on pre-written code.
6. **Only when all six features are at L2 and Shamit says go:** the stretch goals, in this order: technician skill levels, spare-part repositioning, fleet-wide multi-fault (the solver already handles several simultaneous faults; it is not tuned for 20+).
7. **Tick** the remaining boxes in `work/03-...` and `TASK-BOARD.md` after C1.

## 5. Quick reference for the others

```python
from app.solver import derive_tasks, ground_windows, solve                 # solve(state, tasks, weights, mode, time_limit_s, previous)
from app.solver.tasks import derive_tasks_ex                               # also returns notes: components it could not plan, and why
from app.solver.store import STORE                                         # STORE.save(plan), STORE.reset(), STORE.add_listener(fn)
from app.whatif.replan import replan                                       # replan(state, prior, event, already_applied=False)
from app.whatif.events import apply_event_to_state, apply_event            # apply_event also returns the effects as text
from app.whatif.compare import compare, build_scenario_plans, default_scenarios
from app.whatif.simulate import simulate, simulate_detailed, affected_legs # runs=1 analytic; runs>=2 SimPy; affected_legs -> Operations agent
from app.whatif.metrics import plan_block                                  # the plan block of GET /metrics
```

`POST /whatif/compare` row: `{"name", "feasible", "mode", "plan_id", "metrics": PlanMetrics, "objective_value", "solve_seconds", "as_of", "infeasible_reasons": []}`.
Agent-tool mapping: `solve_schedule` = `solve` + `STORE.save`; `compare_plans` = `compare`; `simulate_plan` = `simulate(plan, state)`; `flights_affected` = `affected_legs`; `replan_with_event` = `replan`; `shortage_report` = `derive_tasks_ex(...).notes` + `plan.infeasible_reasons`.
