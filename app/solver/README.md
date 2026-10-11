# Scheduler (3.3), what-if (3.5) and agent tools (3.4 scripted): `app/solver/`, `app/whatif/`, `app/agents/`

Owner: Pardhu. Everything here is **SIMULATED** and uses synthetic task cards; it is advisory only (a human approves every action).

## Run it

```
pip install ortools simpy pyyaml pydantic fastapi httpx pytest      # tested: Python 3.13, ortools 9.15, simpy 4.1, pydantic 2.13
python -m pytest tests -q                                           # 299 tests in the merged repo, about 2 min (mine alone: about 20 s)
python scripts/agent_demo.py [--export] [--replan]                   # runs the 5 scripted agents with the 17 tools; --export writes fixtures/agent_run_golden.json
python scripts/plan_demo.py                                          # prints the baseline-vs-optimized table, replan, compare, simulation
python scripts/plan_demo.py --export                                 # also rewrites fixtures/plan_current.json, plan_baseline.json, replan_result.json
python scripts/mutation_check.py                                     # breaks 30 rules one at a time in a scratch copy; every one must be CAUGHT (about 1 min)
```

Environment: `SOLVER_WORKERS` (CP-SAT threads, default **1** so results repeat exactly; use 4 for big fleets), `SEED` (default 1234),
`MOCK_SOLVER=1` (use the greedy stub instead of CP-SAT, and the golden stand-in state). `MOCK=1` is Shamit's flag: it mounts his mock routers instead of ours.

## Files

| File | What |
|---|---|
| `app/copilot/kb/task_cards.yaml` | 10 synthetic task cards (read by `derive_tasks` and by the copilot's `search_kb`) |
| `solver/windows.py` | `ground_windows(legs, now, aircraft=None)`; rotation breaks are reported, not guessed |
| `solver/tasks.py` | `derive_tasks(state)` and `derive_tasks_ex(state)` (also returns `notes` for components it could not turn into a task) |
| `solver/candidates.py` | the one place the hard constraints live: every feasible (task, window, technician, part source) and its start range |
| `solver/greedy.py`, `baseline.py`, `cpsat.py` | the three solvers behind one `solve()` (`engine.py`) |
| `solver/plan_metrics.py` | metrics, `objective_value`, and `validate_plan` (final guard on every plan) |
| `solver/exposure.py` | risk view: hours to fix, flights flown with the fault open, margin before the hard deadline (additive `exposure` in `GET /plan` and compare rows) |
| `solver/reasons.py` | the plain-language `Assignment.reason` (all numbers computed, none invented) |
| `solver/store.py`, `service.py`, `router.py` | in-memory plan store, state source, `GET /plan`, `POST /plan/solve` |
| `whatif/events.py` | `apply_event_to_state` (pure), `apply_event` (also returns the effects as text) |
| `whatif/replan.py`, `compare.py`, `simulate.py`, `router.py`, `metrics.py` | replan + diff, scenarios, analytic + SimPy simulation, `POST /plan/replan`, `POST /whatif/compare`, `GET /metrics/plan` |
| `solver/golden.py` | deterministic **stand-in** golden `FleetState` until the real simulator/twin exist |

## Agent tools and scripted runner (`app/agents/`, piece 11 in `work/OWNERSHIP.md`)

| File | What |
|---|---|
| `tools.py` | `TOOLS`: the 16 tools of CONTRACTS.md section 6 plus `suggest_repositioning` (`name, agent, description, JSON input_schema, fn`); each returns `{data, evidence, missing_info}`; `call_tool(name, args)` turns expected failures into `{"data": None, "error": ...}` |
| `scripted.py` | Health -> Scheduling -> Logistics -> Operations -> Coordinator; a `thought`, `tool_call`, `tool_result` step per call plus a `summary` per agent; explanation is a template filled only with tool outputs |
| `runner.py` | `run_workflow(goal, scope, mode, background=False)`; `mode="llm"` runs the scripted workflow and records `mode="scripted"` (LLM mode is piece 12) |
| `runs.py` | in-memory run store (`RUNS`), `RUNS.add_listener(fn)` for the database/audit layer |
| `workorders.py`, `kb.py` | work orders (always `pending_approval`, with plan/kb/health/tool_call evidence); keyword search over the task cards |
| `router.py` | `POST /agents/run` (returns at once with `running`), `GET /agents/runs/{id}` |

The tools read the same state as the planner (`service.get_state()`): the twin snapshot when it exists, otherwise the golden stand-in, plus disruptions already applied.
`get_sensor_trend` needs the digital twin and says so (in `missing_info`) instead of inventing a trend. A scripted run on the golden fleet takes about 0.6 s.

## Stretch goals (all built, advisory where they suggest)

* **Technician skill levels:** skills stay plain strings: `"engine"` = level 1, `"engine:2"` = level 2 (a technician with a higher level covers a lower requirement).
  A card may carry `min_level: 2`; `derive_tasks` then sets `required_skill: "engine:2"`. No model change. Code: `app/solver/skills.py`.
* **Spare-part repositioning:** `suggest_repositioning(horizon_cycles=120)` (also the 17th agent tool): forecasts demand per home station, compares with stock on hand, and suggests moving
  surplus from the donor named in the stock record (time and cost copied from it). Never moves or books anything. Code: `app/solver/reposition.py`.
* **Several simultaneous faults, fleet-wide:** the model always handled them; tests now prove 6 and 10 simultaneous faults and a chain of four disruptions stay valid, CP-SAT never loses to greedy, and the 5 s limit holds.

## How a plan is made

1. `derive_tasks`: a component with RUL **p10 < 60 cycles** gets a task from its card. **Deadline = scheduled arrival of its `floor(p10)`-th future flight** (p10, never p50);
   beyond the known rotation it extrapolates with the aircraft's own legs/day. p10 < 1 cycle means "before the next departure". Routine checks come from
   `last_maintenance + interval_days` (at most 3, due within 5 days; overdue ones are only reported).
2. `SolveContext.candidates`: for each task, each ground window of that aircraft at an open station, each eligible technician (skill, available, on shift) and each part source
   (on hand, arriving, or shipped at a cost). Deadline, shift, part-ready time and the extension cap are baked into the start range, so no solver can break them.
3. Solvers: **static baseline** (home base, last window that fits), **greedy** (earliest completion), **CP-SAT** (optional intervals; technician and aircraft NoOverlap; hangar Cumulative;
   part pools; an `unscheduled` literal so you always get a plan plus reasons). Extension beyond a ground window is allowed up to 3 h, costed, and counted as an affected flight.
4. `validate_plan` re-checks every hard rule on the finished plan. If CP-SAT ever returned something invalid, the engine falls back to greedy **and says so** in `Plan.evidence`.
5. `replan`: apply event (pure) -> `derive_tasks` -> `solve(previous=prior)` -> diff with a `why` per moved task. The previous plan adds a stability penalty, so only what must change changes.

Objective (same formula for every mode, reported as `Plan.objective_value`):
`downtime*alpha(10) + flights_affected*beta(25) + logistics_usd*gamma(0.01) + schedule_changes*delta(2) + 0.05 per hour until each job ends + 10 000 per unscheduled task`.
The `0.05/h` earliness term is my addition (a soft preference for fixing sooner; deadlines stay hard). Without it the optimizer would wait four days to save a shipment.

## Decisions I had to make where the docs were silent or contradictory (please confirm)

| # | Topic | What the code does |
|---|---|---|
| 1 | `part_delay` on stock that is on hand | work/03 said "set qty to 0"; that deletes the stock. Instead the units are **held until event time + delay** (`eta_hours` = hours to the event + delay). Stock already in transit: `eta_hours += delay` |
| 2 | `PartStock` meaning | `qty >= need` -> local stock, usable after `eta_hours`. Otherwise, or if faster, ship from `ship_from` (needs stock there; ready at `source.eta + ship_hours`; costs `ship_cost_usd`; consumes the SOURCE station's stock). `qty = 0` with `eta_hours > 0` is read conservatively (the later time) |
| 3 | Deadline | arrival of the `floor(p10)`-th future flight. A job normally finishes before that flight departs |
| 4 | Task-card fields | `component_kind` uses `ComponentKind` (`brakes`, `landing_gear`), `skill` uses technician skills (`gear`); added `trigger: rul|scheduled`, `interval_days`, `ata` |
| 5 | Scheduled tasks | need `ComponentState.last_maintenance`; components without it get no routine task |
| 6 | Technician shift | one contiguous `[shift_start, shift_end]`; a job must lie inside it. A technician belongs to the station that lists him |
| 7 | Metrics | `flights_affected` = assignments with an extension (cascades ignored); SimPy `simulate(runs>=2)` counts cascaded delays too |
| 8 | Extension cap | 3 h hard cap (`max_extension_h`); an extended job delays the next departure |
| 9 | Events written to the DB | `replan` applies the event itself. If the platform already wrote it into the snapshot, call `replan(..., already_applied=True)` or it is applied twice |
| 10 | Optional additions to contract signatures | `ground_windows(..., aircraft=None)`, `solve(..., force_station=None, force_tails=None)`, `replan(..., weights, time_limit_s, already_applied, mode)`, `simulate(..., tasks=None)`, `Scenario.constraints["tail"]` |

## Known limits (say them out loud)

* Task cards, durations, intervals and the golden fleet are **made up**; the numbers are simulated, relative to the stated baseline, and are not airline savings.
* Delay cascades are ignored by the plan metrics; the SimPy run shows them. Utilization is over a 120 h horizon with a handful of tasks, so it is small.
* At 20+ simultaneous faults the 5 s limit is reached: the plan is still valid and no worse than greedy, and it says "not proven optimal" (try `SOLVER_WORKERS=4`).
* Tested on Python 3.13 and OR-Tools 9.15 only. The code uses the snake_case OR-Tools API (9.10+).
* The golden state in `golden.py` is a stand-in, not Shamit's fleet. Re-run `plan_demo.py` on the real snapshot before any number goes on a slide.
