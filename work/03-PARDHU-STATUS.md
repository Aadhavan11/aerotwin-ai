# Pardhu: status, message for the team, what to present, what is left

Branch: `claude/great-albattani-bv8i0b` (not merged to `main`, no pull request opened). Written 2026-10-11.
Scope of this branch: **only my package** (3.3 scheduler, 3.5 what-if, task cards, their tests). Nothing of Shamit's, Aashirth's or Aadhavan's was built.

## 0000. Decisions confirmed by Pardhu (2026-10-11)

* YES: a part delay holds the kits until the event time plus the delay (units are not deleted).
* YES: keep the 0.05 per hour "fix sooner" preference (tie-breaker only; deadlines stay hard).
* Event rules: Pardhu shared the Hacklanta 2026 Hacker Guide (Notion link in `docs/AI-DISCLOSURE-DRAFT.md`); I could not open it from the sandbox, so the AI-disclosure wording is still a draft until its AI-policy section is pasted.

## 000. Update 4: baseline contrast (reply to Shamit's review)

* Shamit's integration branch is `integration/full`; it already contains my branch and the stretch goals. **Merge that one, not mine.**
* On the real fleet baseline and optimized tie on cost ($0, 0 h, 0 flights). I kept the baseline definition and added a **risk view** instead (hours to fix, flights flown with the fault open, margin before the hard deadline): baseline 92 h / 16 flights / 10 h margin vs optimized 20 h / 4 flights / 82 h margin. Details in `docs/INTEGRATION-FINDINGS.md`. Present THIS contrast, not dollars, if the real run also ties on cost.
* I withdrew my "Force service at ATL needs a tail" claim: on the real fleet it is infeasible either way.
* Totals: **299 tests pass, 30/30 mutations caught.** The two choices (kit delay held until event time plus delay; 0.05 per hour fix-sooner preference) stay as the defaults; Shamit's review recommends yes to both; they are still waiting for your yes.

## 00. Update 3: integrated with everyone's branches (read `docs/INTEGRATION-FINDINGS.md`)

* This branch now contains `main`, `aadhavan/simulator-integration` and `aashirth/ml-core`, so it can be merged as the **integration base**. 265 tests pass, 2 fail (Shamit's order-dependent `test_db_mode`).
* Ran Shamit's `scripts.golden` on a live `app.main`: 15/16 as the repo stands; **13/16 with all six module groups real** after two small Aadhavan fixes (missing `app/sim/router.py`, a telemetry-id clash with Shamit's table). Patches are tested and written out in `docs/INTEGRATION-FINDINGS.md`.
* Fixed on my side: shared request models, work orders go through the copilot's audited store, `MOCK=1` no longer hides the live twin, least-bad suggestion for impossible deadlines.

## 0. Update (Sun 2026-10-11, second pass)

* Merged `origin/main` (the Shamit/Aadhavan job swap, `work/OWNERSHIP.md`): clean merge. I also read Aashirth's `aashirth/ml-core` branch; nothing there conflicts, and I did not merge it.
* **New owner of the agent tool registry + scripted runner (piece 11) is me** (marked "proposed" in OWNERSHIP.md; Shamit confirms). **Built and tested:** `app/agents/` (16 tools, five-agent scripted workflow, `POST /agents/run`, `GET /agents/runs/{id}`), `scripts/agent_demo.py`, `fixtures/agent_run_golden.json`, 22 tests. A scripted run takes about 0.6 s.
* Totals now: **299 tests pass** across the whole merged repo (2 fail: Shamit's order-dependent `test_db_mode`, one of them also on plain `main`); `scripts/mutation_check.py` breaks 30 rules one at a time and all 30 are caught.
* The event rules could not be verified (no AI policy found online; `hacklanta.dev` unreachable from here). Draft disclosure text is in `docs/AI-DISCLOSURE-DRAFT.md`; someone must read the real rules.
* Still impossible without teammates: running on the real snapshot (Aashirth's twin, Aadhavan's fleet), the team decisions (section 4, item 3), the real numbers for slides.

## 1. Where things stand

* **Done and tested:** task cards, ground windows, `derive_tasks`, static baseline, greedy, CP-SAT, `plan_metrics` + a final validity guard, `apply_event_to_state`, `replan` with a diff, `compare` (scenarios), `simulate` (analytic + SimPy), the four endpoints I own, plan fixtures for the UI. **135 tests pass** (`python -m pytest tests -q`).
* **Built on a stand-in, not on the real fleet.** Shamit's models/fleet and Aashirth's twin snapshot do not exist in the repo yet, so I used `app/contracts/models.py` (a verbatim copy of `CONTRACTS.md` section 2, in its own commit) and `app/solver/golden.py` (a deterministic 24-aircraft, 8-station golden fleet). Every number below comes from that stand-in. **They must be re-run on the real snapshot at C1 before any number goes on a slide.**
* **Stretch goals: done** (technician skill levels, spare-part repositioning, multi-fault tests). Not checked: Python 3.11 (tested on 3.13), OR-Tools older than 9.10, anything LLM or UI related.

## 2. Message to paste in the group chat

> **Pardhu: scheduler + what-if + agent tools are pushed** on branch `claude/great-albattani-bv8i0b` (merged with the latest `main`; nothing merged into `main`, no PR).
> `python -m pytest tests -q` = 299 passing (the 2 `test_db_mode` failures are not mine). `python scripts/plan_demo.py` prints the planner demo tables from a real run; `python scripts/agent_demo.py` runs the 5 scripted agents.
> What works: `GET /plan`, `POST /plan/solve`, `POST /plan/replan`, `POST /whatif/compare`, `GET /metrics/plan`, `POST /agents/run`, `GET /agents/runs/{id}`; `solve()` with static baseline, greedy and CP-SAT; the 16 agent tools (`app/agents/tools.py`); the 10 task cards in `app/copilot/kb/task_cards.yaml`.
> Golden story: the optimized plan fixes SIM-017's engine at DFW on day 1 with a kit on hand; the baseline (home base, last window) ships a kit to ATL and finishes about 46 h later; a 12 h DFW kit delay replans in under 0.1 s and moves the job to the next DFW window with 0 flights delayed.
> I built on a stand-in fleet because the real models/fleet/twin were not in the repo. **Small asks that decide whether it works on the real data:**
>
> **Shamit:** (1) keep YOUR `models.py` when merging; mine is a verbatim copy. (2) `requirements.txt` needs `ortools`, `simpy`, `pyyaml` (not in the INTEGRATION.md list) and `httpx` for the tests. (3) mount `app.solver.router.router`, `app.whatif.router.router`, `app.agents.router.router` in `main.py`. (4) Please confirm piece 11 (tool registry + scripted runner) is mine, as OWNERSHIP.md proposes. (5) If your fixtures `plan_*.json` / `agent_run_golden.json` collide with mine in the merge, take mine (they come from the real code). (6) Your screens can use: `GET /plan` -> `{current, baseline, compare}`; `POST /plan/replan {event}` (422 `{"error","detail"}` for a bad event); `POST /whatif/compare {scenarios: []}` (empty = 5 default what-ifs); show `Assignment.reason`, `PlanDiff.moved[].why`, `infeasible_reasons` in a red banner, the SIMULATED badge.
> **Aadhavan (fleet generator + `/scenario/inject`):** please set `ComponentState.last_maintenance` (else no routine tasks appear); chain legs so each arrival airport equals the next departure airport; keep the golden facts: SIM-017 has a ground window of 6 h or more at DFW that STARTS within a few hours of the sim start (otherwise a 12 h kit delay changes nothing), ATL has 0 BORESCOPE-KIT, DFW has 2. If `/scenario/inject` writes `part_delay`/`facility_closed`/`tech_unavailable` into the DB, call `replan(..., already_applied=True)` (or `replan_and_store`), otherwise the event is applied twice. Call `STORE.reset()` and `RUNS.reset()` on `/scenario/reset`.
> **Aashirth (ML + twin, and LLM mode/copilot if piece 12 continues):** the scheduler only needs `rul_cycles.p10` (never p50) and `last_maintenance` on each `ComponentState`, and `twin.snapshot()` returning `FleetState` with `now` = the sim clock. For LLM mode, the registry is ready: `from app.agents.tools import TOOLS, call_tool` (name, description, JSON `input_schema`, `fn`); `run_workflow(goal, scope, mode)` already falls back to scripted and records it. `get_sensor_trend` calls `app.twin.state.get_twin(tail)` when it exists. Persist runs with `RUNS.add_listener(fn)`; work orders are drafted `pending_approval` by `app/agents/workorders.py` (your copilot's `decide()` approves them).
> **All:** two choices I made that you may want to change (section 4 of `app/solver/README.md`): kit delay = units held until event time + delay (not deleted), and a small "finish sooner" preference of 0.05 per hour in the objective (deadlines stay hard). Also: someone please read the real event rules; my draft AI disclosure is in `docs/AI-DISCLOSURE-DRAFT.md`.

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
6. **Trust (20 s).** 299 tests; an independent checker re-verifies deadlines, overlaps, hangar slots, stock; 30 deliberate bugs (`python scripts/mutation_check.py` breaks one rule at a time in a scratch copy) were all caught; every plan is re-validated before it is returned.

Honest limits to say (SCOPE.md section 10): synthetic cards and fleet; savings are simulated and relative to the stated baseline; delay cascades are ignored by the plan metrics (the SimPy run shows them: baseline mean downtime 0.003 h, optimized 0, 20 runs, +-15 % durations); utilization looks tiny because 5 tasks run on a 120 h horizon.

Likely questions: *Why not let the LLM schedule?* Numbers and safety rules must come from a solver. *What if CP-SAT times out?* Best feasible plan, marked "not proven optimal"; invalid output falls back to greedy and says so. *Why did SIM-012 wait days?* Its deadline is 13 days away and waiting avoids an $800 shipment; the 0.05/h preference is a knob. *Is the 3 h extension safe?* It is a cap, it delays a flight, and it is costed.

## 4. What is left for me to complete my part (in order)

1. **Merge** into the team flow (Shamit merges to `main` from C1): `models.py` -> take Shamit's; `fixtures/plan_*.json` and `agent_run_golden.json` -> take mine; mount the three routers in `main.py`.
2. **C1 check on the real data:** run `python scripts/plan_demo.py` against the real `twin.snapshot()` (it uses it automatically once `app/twin/state.py` exists) and re-run `--export` to refresh the fixtures. Check the golden facts hold in Shamit's fleet; if not, tell him or adjust the golden scenario.
3. **Confirm the three choices** with the team: kit-delay semantics, the 0.05/h earliness term, the 3 h extension cap.
4. **Replace the numbers** in section 3 with the real run before they appear on a slide or in the video.
5. **Disclose AI assistance** in the README/Devpost if the event requires it (this module was built with Claude): draft in `docs/AI-DISCLOSURE-DRAFT.md`; the real rules still need to be read by someone.
6. **Stretch goals are built** (`app/solver/skills.py`, `reposition.py`; multi-fault tests in `tests/test_whatif.py`); nothing more to build on my side.
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
