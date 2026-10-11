# Work package 3: Pardhu. Fleet-aware scheduler (3.3) and what-if simulation (3.5)

You build the **decision engine**: given the fleet state and the maintenance that is needed, find where and when to do it, and recompute when
reality changes. This is the heart of the "self-adjusting plan" story.

## What you own

```
app/solver/   windows.py  tasks.py  greedy.py  baseline.py  cpsat.py  plan_metrics.py  router.py
app/whatif/   events.py  simulate.py  replan.py  compare.py  router.py
app/copilot/kb/task_cards.yaml      (you author it; Shamit's copilot and your derive_tasks both read it)
tests/test_solver.py   tests/test_whatif.py
```

Read: `FEATURES-3.1-3.6.md` (3.3, 3.5), `CONTRACTS.md` sections 1, 2 (scheduling + what-if models), 5, `INTEGRATION.md` sections 4.3, 4.4.

## Step-by-step (with time boxes)

### 0. Task cards (before 9:15 PM, 30 min): `app/copilot/kb/task_cards.yaml`
10 synthetic cards, each marked `synthetic: true`, so the whole team can use them. Fields: `id, title, component_kind, duration_h, skill, part, part_qty, note`.
Suggested: engine borescope inspection (6 h, `engine`, `BORESCOPE-KIT`), engine oil/chip check (2 h, `engine`), brake assembly replacement (3 h, `gear`, `BRAKE-ASSY`),
landing gear actuator check (4 h, `gear`), hydraulic pump replacement (5 h, `hydraulics`, `HYD-PUMP`), hydraulic leak check (2 h, `hydraulics`), avionics LRU swap
(2 h, `avionics`, `AVIONICS-LRU`), avionics software load (1.5 h, `avionics`), tire/wheel change (1.5 h, `gear`), generic scheduled inspection (3 h, `engine`).
These are **made-up demo values, not real maintenance data**; say so in the file header.

### 1. L1 (by 10:00 PM): windows, tasks, greedy, baseline, metrics
- `windows.py: ground_windows(legs, now)`: for each aircraft, sort its legs; a window = from arrival at station X to the next departure from X. (If the next
  departure is from a different airport, there is no window; flag the data.)
- `tasks.py: derive_tasks(state)`: for every component with `rul_cycles.p10 < 60` (tunable) make a `Task` from the matching card; **deadline = the time the
  aircraft will have flown `p10` more cycles**: walk its scheduled legs; beyond the known rotation use average legs/day. `earliest = state.now`. Add a few
  `scheduled` tasks (e.g. brake check due in 3 days) so the fleet has background work. `basis` explains it in words ("engine_2 RUL p10 = 18 cycles = about 4.5 days").
- `greedy.py`: for each task by deadline: first window (any station with the skill + part + hangar) that fits `duration` before the deadline; assign a technician;
  record `reason`. This is also your **fallback solver**.
- `baseline.py` (`static_baseline`): do each task at the aircraft's **home base in the last window before the deadline** (a fixed-schedule policy). Write down
  this definition in the docstring; the demo compares against it.
- `plan_metrics.py`: `downtime_h` (hours beyond planned ground time: sum of `extension_h`), `flights_affected` (1 per assignment with extension > 0; note the simplification:
  cascades onto later legs are ignored), `logistics_cost_usd` (sum of shipping costs of parts not on hand), `tech_utilization` and `hangar_utilization`
  (busy hours / available hours over the horizon), `schedule_changes` (vs `previous`).
- `router.py`: `GET /plan` (`{current, baseline, compare}`), `POST /plan/solve`. Merge. **Gate: L1 merged by 10:00 PM.**

### 2. CP-SAT model (10:00 PM to 12:15 AM; the core)
OR-Tools CP-SAT, `from ortools.sat.python import cp_model`. Method names are CamelCase in older releases (`NewBoolVar`, `AddExactlyOne`, `NewOptionalIntervalVar`,
`AddNoOverlap`, `AddCumulative`, `Minimize`) and snake_case in newer ones (`new_bool_var`, `add_exactly_one`, ...). Check `help(cp_model.CpModel)` for the installed version.

**Time:** integer **minutes since `state.now`**; horizon 5 days = 7200 min.

**Candidates.** For each task `i`, each ground window `w` of that aircraft at a station `s` with `hangar_slots > 0`, build a candidate if some technician `t` with
the skill exists on shift:
- `part_ready(s)`: 0 if the part is on hand; else the station's `eta_hours` or the cheapest `ship_hours` from `ship_from` (record `ship_cost_usd`); no source -> no candidate.
- start range `[max(w.start, task.earliest, part_ready, shift_start), ...]`; **hard deadline:** `end <= task.deadline` (this is the safety constraint: never relaxed).
- Allow a **ground-time extension** `ext` in `[0, max_ext]` (default 3 h): `end <= w.end + ext`; `ext > 0` costs `downtime` and counts as a `flights_affected` (a bool).

**Variables:** presence literal `x[i,w,t]` and an optional interval `[start, start+dur)` per candidate; `unscheduled[i]` literal.

**Constraints:**
1. `AddExactlyOne(all x[i,*,*] + [unscheduled[i]])` for each task.
2. Technician `t`: `AddNoOverlap` over all optional intervals using `t` (and respect shift windows through the start/end bounds).
3. Hangar capacity at station `s`: `AddCumulative(intervals at s, demands=1, capacity=hangar_slots)` (closed hangars simply have no candidates).
4. Parts: for each (station, part), `sum(presence of tasks using that part there) <= qty_on_hand + shipped_qty_allowed`.
5. Aircraft cannot be maintained in two windows at once: at most one candidate per (task) already; for different tasks of the same aircraft in overlapping windows use `AddNoOverlap` on that aircraft's intervals.

**Objective:** `Minimize( alpha*downtime_min/60 + beta*flights_affected + gamma*logistics_usd + delta*changes + BIG*sum(unscheduled) )`, weights from `ObjectiveWeights`.
`previous`: add a bool `same[i]` (assignment equals the previous one) and charge `delta` when it is false.
**If any `unscheduled[i]` is true** -> `feasible=False` and fill `infeasible_reasons` per task (which constraint blocked it: no technician with the skill, no part, no
hangar, no window before the deadline). **Never silently relax a deadline.**

**Solve:** `solver.parameters.max_time_in_seconds = time_limit_s (5)`, `num_search_workers = 4`, fixed `random_seed = SEED`. Map `OPTIMAL/FEASIBLE/INFEASIBLE/UNKNOWN`
to the `Plan`. Fill each `Assignment.reason` from the numbers (e.g. "Only window before the deadline with an engine technician and a kit on hand; 0 h extension").
Always keep `greedy` as the fallback if CP-SAT returns nothing.

**Test (`tests/test_solver.py`)** exactly as in `FEATURES-3.1-3.6.md` (deadlines, no overlaps, part counts, infeasible case, CP-SAT <= greedy cost).

### 3. What-if (12:00 AM to 1:15 AM, then polish)
- `events.py: apply_event_to_state(state, event) -> FleetState` (pure; use `model_copy(deep=True)`): `part_delay` -> `eta_hours += delay_hours` for that (station, part) (if it was on hand,
  set `qty` to 0 and `eta_hours = delay_hours`); `facility_closed` -> `hangar_slots = 0`; `tech_unavailable` -> that technician `available=False`; `anomaly` /
  `accelerated_degradation` -> shorten the component's `rul_cycles` (e.g. multiply by `1 - severity`) so `derive_tasks` creates earlier deadlines.
- `replan.py: replan(state, prior, event)`: state' = apply; tasks' = derive_tasks(state'); `after = solve(..., previous=prior)`; `diff` = moved/added/dropped (with `why` from the
  assignment reasons); return `ReplanResult` including `solve_seconds`. Persist `after` as the current plan; Shamit's audit logs it.
- `compare.py: compare(state, plans)` -> metrics per plan; the router's `POST /whatif/compare` builds plans for each named scenario (`events`, optional
  `constraints {force_station, earliest}`: implement `force_station` by removing other stations' candidates, `earliest` by raising `task.earliest`) and returns `Comparison`.
- `simulate.py` **L1:** analytic metrics from the plan (reuse `plan_metrics`). **L2 (only if time):** SimPy discrete-event run: one process per aircraft stepping through legs, a `Resource` per
  hangar and technician, durations jittered ±15%, `runs=20`, report mean and p90 downtime. Seed it.
- Routers: `POST /plan/replan`, `POST /whatif/compare`; the plan block of `GET /metrics` (`baseline` vs `optimized` metrics and `replan_seconds`).

### 4. Then the doc's stretch goals (only when all six features are at L2 and Shamit says go)
Pick in this order: **technician skill matching depth** (levels/certifications, not just a skill tag), **spare-part repositioning** (ship a part to a station ahead of time when the plan predicts a shortage; costs
and lead times already exist in `PartStock`), **multiple simultaneous faults and fleet-wide optimization** (several aircraft competing for one technician or hangar slot; this exercises the `NoOverlap`/`Cumulative` constraints).

## What you give to others

| To | What | When |
|---|---|---|
| Shamit | task cards; `derive_tasks`, `ground_windows`, `solve`, `replan`, `compare`, `simulate` (L1 first) | 9:15 PM / 10:00 PM L1 / 12:15 AM L2 |
| Aadhavan | real `GET /plan` and `/plan/replan` shapes (`fixtures/plan_*.json` refined to your output) | 10:00 PM / 12:15 AM |
| Everyone | the baseline-vs-optimized numbers from the golden state | 1:30 AM |

## What you need from others

Aashirth: `HealthScore`/RUL p10 on components (L1 fixture first). Shamit: DB snapshot (`stations.json`, `legs_golden.json` first). Build against fixtures until they land.

## Cut lines and fallbacks

- CP-SAT not working by 12:15 AM -> ship `greedy` as "optimized" **and say so**; the baseline comparison and replan still work. Get CP-SAT done afterwards.
- SimPy: skip entirely if short on time; the analytic `simulate` is enough.
- Technician assignment too slow in the model -> assign technicians in a second greedy pass after choosing windows.

## Pitfalls

- Time zones: everything UTC; never mix naive and aware datetimes.
- Integer minutes only inside CP-SAT (no floats); convert at the edges.
- A maintenance job in a window that is too short **delays the next leg**; that is `extension_h`, not a free pass. Keep deadlines hard.
- Make results deterministic (`random_seed`, stable sorting of tasks and windows) or the demo numbers will change between runs.
- Do not invent maintenance data: the task cards are demo values and labeled so.

## Prompt addendum for your Claude

"My role: scheduler + what-if (features 3.3 and 3.5) per work/03-PARDHU-scheduler-and-whatif.md. First the task cards, then L1 (windows, derive_tasks, greedy, static baseline, metrics), then an OR-Tools
CP-SAT model with optional intervals, technician NoOverlap, hangar cumulative, part constraints, HARD deadlines and an `unscheduled` literal that yields infeasibility reasons. Then pure
apply_event_to_state, replan with a diff, and compare. Deterministic (fixed seeds, stable ordering). Never relax a deadline silently."

## Definition of done

- [x] `task_cards.yaml` pushed (branch `claude/great-albattani-bv8i0b`)
- [x] `greedy` and `static_baseline` plans on the golden state; metrics computed (stand-in golden state)
- [x] CP-SAT plan feasible on golden, all deadlines met, no overlaps; infeasible case reports reasons (`tests/test_solver.py`)
- [x] `replan` after a 12 h DFW kit delay returns a non-empty diff and a feasible (or explained-infeasible) plan in under 5 s (0.09 s on the stand-in golden state)
- [x] `compare` returns >= 4 scenario rows; baseline-vs-optimized table printed from the run (`python scripts/plan_demo.py`)
- [ ] Re-verify all of the above on Shamit's real fleet and Aashirth's `twin.snapshot()` at C1 (everything above was done on the stand-in golden state in `app/solver/golden.py`)
- [ ] Doc's stretch goals (skill levels, spare-part repositioning, fleet-wide multi-fault): not started

Status and hand-off: see [`03-PARDHU-STATUS.md`](03-PARDHU-STATUS.md).
