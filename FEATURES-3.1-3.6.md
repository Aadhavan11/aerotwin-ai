# The six core features (AeroTwin doc sections 3.1 to 3.6): what we build, concretely

> **Owners changed Sat night (Shamit and Aadhavan swapped jobs). `work/OWNERSHIP.md` is the source of truth for who builds what; where this file names a different owner, follow OWNERSHIP.md. The specs themselves are unchanged.**

These come **first**. The doc's stretch goals (technician skill matching, spare-part repositioning, multiple simultaneous faults) start only after all six reach Level 2 (`INTEGRATION.md` section 3).
Each feature below says: what the doc asks for, exactly what we build in ~10 hours, who owns it, what it needs from others, what it gives
to others, the acceptance test, and the moment in the demo.

Level key: **L1** stub with the right shape (60 min), **L2** real implementation (by C1), **L3** polish.

---

## 3.1 Predictive Aircraft Health Intelligence

**Doc asks for:** anomaly detection (Isolation Forest/autoencoder), RUL (XGBoost/RF/LSTM), component health scoring and risk ranking,
confidence estimates and data-quality checks.

**We build** (owner: **Aashirth**; module `app/ml/`)
- **Data:** NASA C-MAPSS FD001 (train and test units). Drop constant sensors; keep the informative ones (verify which are flat in the file:
  commonly s2, s3, s4, s7, s8, s9, s11, s12, s13, s14, s15, s17, s20, s21). Fallback: synthetic C-MAPSS-shaped trajectories.
- **Features:** the last 30 cycles as rolling mean, slope and last value per sensor (plus cycle count).
- **RUL model:** `RandomForestRegressor` (or XGBoost) on RUL clipped at 125 cycles; **quantiles from the spread of tree predictions**
  (p10/p50/p90). Report MAE and RMSE on the held-out test units (compare with the true RUL file shipped with C-MAPSS).
- **Anomaly model:** `IsolationForest` fitted on healthy-phase windows (e.g. first 60% of life); `anomaly_score` normalized to 0..1;
  `is_anomaly` by a threshold chosen on the validation set. Report precision, recall and false-positive rate against the label
  "RUL <= 30 cycles".
- **Health score:** `health = clip(rul_p50 / 125, 0, 1)` blended with `1 - anomaly_score` (document the formula in `HEALTH.md`).
- **Data quality:** flags for flat sensors (zero variance over the window), out-of-training-range values, missing values, too-short windows.
- **Other components** (brakes, landing gear, hydraulics, avionics): `score_generic` = z-score/wear-threshold rule + an IsolationForest
  on a few synthetic features. Honest label: "rule-based demo model".
- **Explainability:** `top_signals` = the 3 sensors with the largest contribution (feature importance times deviation).

**Needs:** telemetry shape from Shamit (`TelemetryRow`), nothing else. **Gives:** `ml.health.score_engine/score_generic/evaluate`, `ml.cmapss`
(trajectories for the simulator), `/metrics` ML block.

**Acceptance test** (`tests/test_ml.py`): on the held-out set the script prints RUL MAE and RMSE and anomaly precision/recall/FPR; scorer
returns a valid `HealthScore` for a normal window, a late-life window (anomaly true, RUL p50 < 30) and a broken window (flat sensor ->
`data_quality: degraded`). **Demo moment:** "SIM-017 engine 2: abnormal temperature trend, RUL 18-25 cycles, uncertainty shown."

## 3.2 Interactive Aircraft Digital Twin

**Doc asks for:** a virtual representation of each aircraft and its components; 3D visualization; clickable components; health indicators and
trends; hours/cycles/history; predicted future condition; a timeline; updating as telemetry and events arrive.

**We build** (backend owner: **Aashirth** `app/twin/`; UI owner: **Aadhavan** `web/`)
- **State engine:** per component: cycles, hours, health, anomaly, RUL quantiles, data quality, last maintenance, status, history list,
  `predicted` curve (health falling to ~0 at RUL p50 with a band from p10 to p90), and a per-aircraft timeline of events
  (flights, anomalies, injected faults, maintenance, plan changes).
- **Updates:** `ingest_telemetry` on each new row; `apply_event` for scenario events; `apply_maintenance` resets a component when a work order's
  window completes.
- **UI (L2):** top-view **2D SVG aircraft** with clickable zones (engine 1, engine 2, landing gear, hydraulics, avionics) colored by health;
  side panel with health gauge, RUL band chart, sensor trend chart, history table, predicted curve and the timeline. **3D (react-three-fiber)
  is L3 only** if everything else is done.

**Needs:** `HealthScore` from 3.1; telemetry from the simulator. **Gives:** `GET /aircraft/{tail}` (`TwinView`), `snapshot()` for the solver.

**Acceptance test:** `GET /aircraft/SIM-017` validates as `TwinView`; after `POST /scenario/inject` the same call shows `status: alert`, a new
`anomaly` timeline entry and a changed predicted curve; clicking each of the 5 zones in the UI opens the right component. **Demo moment:**
the aircraft goes amber/red when the anomaly is injected and the planner clicks the engine to see why.

## 3.3 Fleet-Aware Predictive Maintenance Scheduling

**Doc asks for:** optimize maintenance using aircraft location and rotations, component health, facilities and ground windows, technicians,
spare parts and deadlines; recommend locations and windows minimizing downtime, disruption and logistics cost; respect approved constraints.

**We build** (owner: **Pardhu**; module `app/solver/`)
- **Inputs:** `FleetState` (aircraft, component states, legs, stations with technicians/parts/hangar slots) and `Task`s derived from it.
- **`derive_tasks`:** for each component with `rul_p10` below a threshold (default 60 cycles) create a task whose **deadline = the time the
  aircraft will have flown `rul_p10` more cycles** (use its scheduled legs, then average legs/day beyond the known rotation). Plus a few
  fixed "scheduled" tasks (e.g. brake check due on a date) so the fleet has background work. Skill, duration and part come from the
  synthetic task-card table (`copilot/kb/`), not from the LLM.
- **`ground_windows`:** time between an arrival and the next departure at that airport, per aircraft.
- **Three solvers behind one `solve()`:**
  1. `static_baseline`: **the baseline** = do each task at the aircraft's home base in the last window before its deadline (a fixed-schedule policy).
  2. `greedy`: earliest feasible window at the nearest station that has skill+part+hangar (L1 and a fallback).
  3. `cpsat`: OR-Tools CP-SAT model (L2): one optional interval per (task, candidate window), technician assignment, hangar capacity,
     part availability (on hand or shipped with delay and cost), hard deadlines, optional ground-time extension with a penalty.
- **Objective:** minimize `alpha*downtime + beta*flights_affected + gamma*logistics + delta*schedule_changes` (weights in `ObjectiveWeights`).
  Hard constraints (deadlines, skills, parts, capacity) are never traded for the objective.
- **Output:** `Plan` with assignments, a plain-language `reason` per assignment, metrics, solve time, and `feasible/infeasible_reasons`.

**Needs:** `FleetState` (Shamit's DB + Aashirth's twin). **Gives:** `solver.solve/derive_tasks/ground_windows`, `GET /plan`, `POST /plan/solve`.

**Acceptance test** (`tests/test_solver.py`): on the golden state every task meets its deadline; no two jobs use the same technician or
exceed hangar slots at once; part quantities are respected; an impossible case (no engine technician anywhere before the deadline) returns
`feasible=false` with that reason; CP-SAT metrics are no worse than greedy on the same state; baseline vs optimized is printed.
**Demo moment:** "best station and window, the constraints that decided it."

## 3.4 Multi-Agent AI Maintenance Orchestration

**Doc asks for:** Health, Scheduling, Logistics, Operations and Coordinator agents using structured tools, producing one explained plan and
proposed work orders for human approval.

**We build** (owner: **Aadhavan**; module `app/agents/`)
- A **tool registry** (`CONTRACTS.md` section 6) wrapping the real module functions, each returning data plus an evidence reference.
- **Five agents** = (system prompt, allowed tools). The runner executes them in order Health -> Scheduling -> Logistics -> Operations ->
  Coordinator; each appends `AgentStep`s (thought, tool_call, tool_result, summary) to the database as it goes.
- **Two modes:** `llm` (Claude tool-calling loop) and `scripted` (deterministic, same tools, same step format).
- **Coordinator** merges findings, flags conflicts (e.g. Scheduling wants DFW, Logistics reports no kit), writes the explanation and drafts
  `WorkOrder`s in `pending_approval`.

**Needs:** 3.1/3.2 (health), 3.3 (solver), 3.5 (simulate/replan), KB. **Gives:** `POST /agents/run`, `GET /agents/runs/{id}`, work orders, audit.

**Acceptance test** (`tests/test_agents.py`): scripted run on golden returns `status: done`, >= 3 distinct agents with real tool calls,
>= 1 work order, and every work order and recommendation has non-empty `evidence`; LLM mode run (when a key exists) produces the same plan
(because the plan comes from the tool, not the LLM) and an explanation whose numbers all appear in tool outputs.
**Demo moment:** the Agent Activity panel fills with steps while the plan appears.

## 3.5 What-If Simulation and Counterfactual Planning

**Doc asks for:** simulate alternatives before implementing: maintenance now vs later, another airport, delayed part, shared technician, closed
facility; compare downtime, cost, delays, utilization, readiness.

**We build** (owner: **Pardhu**; module `app/whatif/`)
- **`apply_event_to_state`** (pure): part delay (`eta_hours += x`), technician unavailable, facility closed (`hangar_slots = 0`), accelerated
  degradation or anomaly (RUL shortened, so the deadline moves earlier).
- **`simulate(plan, state)`**: L1 = analytic metrics from the plan (downtime, flights affected, logistics cost, utilization). L2 = a **discrete-event
  simulation (SimPy)** executing legs and maintenance with random duration jitter, `runs=20`, returning mean and p90 downtime.
- **`replan`**: apply event -> re-derive tasks -> `solve(previous=prior)` -> diff (moved/added/dropped) -> before/after metrics + solve time.
- **`compare`**: one metrics row per named scenario, same snapshot; supports `constraints {force_station, earliest}` for "now vs later" and
  "other airport".

**Needs:** `solver.solve`, `FleetState`. **Gives:** `POST /plan/replan`, `POST /whatif/compare`, `simulate` used by agents and `/metrics`.

**Acceptance test** (`tests/test_whatif.py`): a 12 h kit delay at DFW changes the golden plan (diff non-empty) and the new plan is feasible or
explains why not; a closed hangar is never used; `compare` returns >= 4 scenario rows; `replan` time is printed (target under 5 s).
**Demo moment:** "delay the part -> the plan recomputes -> here is what changed and what it cost."

## 3.6 Explainable AI Maintenance Copilot

**Doc asks for:** a conversational assistant answering the six example questions, explaining with structured data and approved documentation,
identifying missing information, generating auditable maintenance proposals.

**We build** (backend owner: **Aadhavan** `app/copilot/`; UI owner: **Shamit**)
- `POST /copilot/chat`: LLM with the tool registry (read tools + `solve_schedule` + `replan_with_event` + `draft_work_orders` + `search_kb`).
  Response = `answer`, `evidence[]`, `missing_info[]`, `proposed_work_orders[]`.
- **Approved documentation** = a small **synthetic knowledge base** (`copilot/kb/*.yaml`, ~10 task cards: title, ATA-style chapter, duration, skill,
  part, a note, marked SYNTHETIC) searched by keyword. The copilot cites `kb:<doc_id>`.
- **Work-order workflow:** `pending_approval -> approved/rejected` via `POST /workorders/{id}/decision`; every decision writes an audit entry.
- **UI:** chat drawer with the six quick-prompt buttons, evidence chips (click to open the plan/component), a work-order card with Approve/Reject,
  and the audit list.

**Acceptance test:** each of the six example questions returns an answer with >= 1 evidence ref and no number that is absent from tool output;
a question the data cannot answer (e.g. "which technician is on leave next week?") returns `missing_info` instead of a guess;
approve/reject appears in `GET /audit`. **Demo moment:** ask "Why is SIM-017 prioritized over SIM-012?" and approve the work order.

---

## The golden scenario (everything is tested against this)

- Fleet: 24 fictional aircraft, 8 stations (ATL, DFW, ORD, DEN, LAX, JFK, MIA, SEA) with different technicians, kits and hangar slots.
- **Anomaly:** SIM-017, engine 2, abnormal temperature trend; model reports RUL in the tens of cycles (the doc's example is 18 to 25).
- **Task:** engine inspection (needs an engine technician and a borescope kit). ATL has the hangar but no kit on hand; DFW has kit and technician.
  SIM-017 has a long enough ground window at DFW before its deadline (guaranteed by `fixtures/legs_golden.json`).
- **Plan:** the optimizer picks the window and station; the static baseline waits for the last home-base window.
- **Disruption:** the DFW kit is delayed 12 h; replan shifts the job (other station/window or a costed extension).
- **Impact table:** baseline vs optimized: downtime, flights affected, logistics cost, utilization, replan time. **Numbers come from the run.**

## Priority if time runs short

1. Keep **3.1, 3.3 and the closed loop (inject -> detect -> plan -> replan)** alive; they carry the story.
2. Keep **3.4 in scripted mode** with real tool calls; add the LLM loop when stable.
3. **3.5** at L1 (analytic) is enough for the demo; SimPy is L3.
4. **3.2** with the 2D aircraft is enough; 3D only if everything else is done.
5. **3.6** can be 3 working questions plus approve/reject if needed.
