# TASK BOARD: who builds what, when, and who is waiting on whom

Clock times are ET, Sat 2026-10-10 evening to Sun morning. They assume the event ends Sun ~7 AM (**unverified: confirm the real deadline**).
Tick boxes by editing this file in a small commit (`[docs] tick ...`), so everyone sees progress in the repo.

## 1. Who owns which feature

> **Owners changed Sat night (Shamit and Aadhavan swapped jobs). `work/OWNERSHIP.md` is the source of truth for who builds what; where this file names a different owner, follow OWNERSHIP.md. The specs themselves are unchanged.**

| Feature | Owner | Support |
|---|---|---|
| Foundation: contracts, database, API skeleton, mock mode, fixtures, `make` targets | **Shamit** | |
| Fleet generator (aircraft, rotations, stations, technicians, parts, golden constraints), `SimClock` | **Pardhu** | Shamit supplies the DB tables |
| Telemetry source (C-MAPSS replay), `/sim/step`, scenario injection | **Aashirth** | Shamit's Lab screen calls it |
| **3.1** Health intelligence (anomaly + RUL, data quality) | **Aashirth** | |
| **3.2** Digital twin state engine (backend) | **Aashirth** | |
| **3.2** Aircraft Twin screen | **Aadhavan** | Aashirth explains `TwinView` |
| **3.3** Scheduling (CP-SAT, greedy, static baseline, derive tasks) | **Pardhu** | Aashirth supplies RUL |
| **3.4** Agents (tool registry, runner, scripted + LLM) | **Aadhavan** | Pardhu/Aashirth supply tool functions |
| **3.5** What-if (apply event, simulate, replan, compare) | **Pardhu** | |
| **3.6** Copilot backend, KB, work orders, audit | **Aadhavan** | Pardhu authors the KB task cards |
| Web shell, Fleet Overview, Planning board, Simulation Lab, Copilot drawer + Agent Activity + approvals UI | **Shamit** | Aadhavan supplies the endpoints' shapes |
| Demo story, video, Devpost; concept owner | **Aadhavan** (lead), everyone reviews | |
| Stretch goals from the doc (after all six reach L2) | **Pardhu** | Aashirth |

Pairs for sleep shifts: **Shamit + Aadhavan**, **Aashirth + Pardhu**. At least one person from each pair is awake until 3:15 AM.

## 2. Timeline by person

> The checklists below were written before the ownership swap. **Keep the time boxes, but take the items from `work/OWNERSHIP.md`** (e.g. the agent and copilot items are Aadhavan's, the screens except the Aircraft Twin are Shamit's, the telemetry/scenario items are Aashirth's, the fleet generator is Pardhu's).

### Block 0: Sat 8:30 to 9:15 PM (everyone)
- [ ] Everyone: read your work package (`work/0x-*.md`), `FEATURES-3.1-3.6.md` (your features) and `CONTRACTS.md` sections 1, 2 and 5. 15 min.
- [ ] Everyone: set up the dev environment (`INTEGRATION.md` section 10); make sure you can run `python --version` >= 3.11 and `node --version` >= 20.
- [ ] **Shamit:** repo skeleton, `models.py` (copy from CONTRACTS), `settings.py`, `db.py` + `init_db`, `main.py` with every router returning fixtures when `MOCK=1`, the 9 fixture files, Makefile, `requirements.txt`, `.env.example`, `.gitignore`. Push. **= C0**
- [ ] **Aashirth:** download C-MAPSS (or decide to use the synthetic fallback), write `ml/sensors.py` (sensor-name mapping) and push it **first** (Shamit's telemetry needs it).
- [ ] **Pardhu:** write `copilot/kb/task_cards.yaml` (10 cards) and push (Shamit's copilot and your `derive_tasks` both need it).
- [ ] **Aadhavan:** `npm create vite`, Tailwind, Recharts, router; app shell + SIMULATED badge; load `fixtures/*.json` statically.

### Block 1: 9:15 to 10:00 PM, L1 stubs merged
- [ ] **Shamit:** fleet generator v0 (seeded) writing the DB; `POST /scenario/reset golden`; `SimClock`.
- [ ] **Aashirth:** `ml.health.score_engine` L1 (z-score rule on T50/Nc/Ps30); `twin.state` L1 (in-memory) + `GET /aircraft/{tail}` from fixtures/DB.
- [ ] **Pardhu:** `ground_windows`, `derive_tasks` (L1 from fixtures), `greedy` + `static_baseline`, `plan_metrics`; `GET /plan`.
- [ ] **Aadhavan:** Fleet overview wired to `/fleet` (fixtures first); table + KPI tiles + US map.
- **Gate:** `make demo-check` runs end to end on stubs by **10:00 PM**.

### Block 2: 10:00 PM to 12:45 AM, the real implementations (L2)
- [ ] **Shamit:** telemetry source replaying C-MAPSS (from `ml.cmapss`), `POST /sim/step`, `POST /scenario/inject`; tool registry; **scripted** agent workflow with steps stored; `/agents/run` + polling endpoint.
- [ ] **Aashirth:** RF RUL with quantiles + eval (MAE/RMSE); IsolationForest anomaly + metrics; data-quality flags; twin state with history, predicted curve, timeline; `/metrics` ML block.
- [ ] **Pardhu:** CP-SAT model; objective + hard constraints; infeasibility reasons; `previous` plan stability; `apply_event_to_state`; `replan` + diff; `compare`.
- [ ] **Aadhavan:** Aircraft Twin screen (SVG aircraft, clickable zones, RUL band, sensor trend, timeline); Planning board (Gantt, baseline/optimized toggle, metrics cards).

### Block 3: 12:45 to 1:30 AM, **C1: first end-to-end loop** (all four, together, on one screen share/room)
- [ ] `make demo-check` passes with `MOCK=0`: reset -> step -> inject -> agents (scripted) -> plan -> approve -> replan after part delay.
- [ ] Fix every contract mismatch found (log each in `CONTRACTS-CHANGELOG.md`).

### Block 4: 1:30 to 3:15 AM, finish the six features, then freeze
- [ ] **Shamit:** LLM mode for agents (tool loop + number check + cache); copilot chat; work orders + audit; Operations agent using `simulate`.
- [ ] **Aashirth:** tests + `HEALTH.md`; help Shamit with `search_kb` and the numeric-claims check; polish predicted curves.
- [ ] **Pardhu:** SimPy `simulate` (L2 stretch); scenario comparison; solve-time tuning; then the doc's stretch goals if all six are green.
- [ ] **Aadhavan:** Simulation Lab screen; Copilot drawer + Agent Activity + work-order approval; error/loading states.
- [ ] **3:15 AM: FEATURE FREEZE (C2).** Sleep shift 1: Aashirth + Pardhu (3:15 to 5:00). Shamit + Aadhavan continue.

### Block 5: 3:15 to 5:45 AM, harden and tell the story
- [ ] **Shamit:** `make demo-check` in both modes; cache the golden agent run; offline mode; fix bugs only.
- [ ] **Aadhavan:** demo script (`docs/DEMO-SCRIPT.md`), screen-record the backup video, Devpost text, README top section.
- [ ] **Everyone:** rehearse the 3-minute demo 3 times; record real numbers from the run into the slide (no pre-written claims).
- [ ] Sleep shift 2: Shamit + Aadhavan (5:00 to 6:30 if the submission is already staged).

### Block 6: 5:45 to 7:00 AM
- [ ] Submit (earlier if the real deadline is earlier). Check: repo public link works, no secrets, README explains what is simulated, AI use disclosed per event rules, video uploaded.

## 3. Who is waiting on whom (dependency matrix)

| Needs ... | ... from | By | If it is late |
|---|---|---|---|
| `models.py`, fixtures, mock API | Shamit | 9:15 PM | Others copy models from `CONTRACTS.md` and work on fixtures by hand |
| Sensor-name mapping `ml/sensors.py` | Aashirth | 9:00 PM | Shamit uses the mapping in `FEATURES-3.1-3.6.md` / `work/02` |
| KB task cards `task_cards.yaml` | Pardhu | 9:15 PM | Shamit hard-codes 5 tasks in `derive_tasks` L1 |
| Fleet DB + legs (golden) | Shamit | 9:45 PM | Pardhu works from `fixtures/legs_golden.json` and `stations.json` |
| `HealthScore` with RUL quantiles | Aashirth | 10:00 PM L1, 12:00 AM L2 | Pardhu uses `fixtures/health_scores.json`; deadlines still computed from p10 |
| `FleetState` snapshot (`twin.snapshot`) | Aashirth + Shamit | 10:00 PM | Pardhu builds `FleetState` from fixtures |
| `solve`, `replan`, `compare` | Pardhu | 10:00 PM L1, 12:15 AM L2 | Shamit's tools call the greedy stub; plan shape is identical |
| Tool registry + `/agents/run` | Shamit | 11:30 PM scripted | Aadhavan uses `fixtures/agent_run_golden.json` |
| Endpoints for every screen | owners (see CONTRACTS section 4) | per screen | Aadhavan builds on `MOCK=1` or static fixtures |
| ML numbers (MAE, RMSE, precision, recall) | Aashirth | 1:30 AM | Slide says "ML metrics: in progress", never invents numbers |

## 4. If you finish early (pick up in this order, tell the group first)

- **Aashirth:** `docs/EVAL.md` with the honest ML results; help Pardhu test the solver; then the shared `search_kb` + numeric-claims checker.
- **Pardhu:** the doc's stretch goals: technician skill matching depth, spare-part repositioning (ship a part to a station ahead of time), multiple simultaneous faults and fleet-wide optimization.
- **Aadhavan:** 3D aircraft (react-three-fiber) only if the 2D version is done and everything else is green; otherwise the video and write-up.
- **Shamit:** performance, caching, the Raspberry-free offline run, and the Q&A prep sheet for judges.

## 5. Stuck rule

If you are blocked for 10 minutes, post in the group chat: **what you tried, the error, what you need**. Swap to your fallback (listed in your work
package) rather than waiting. The demo needs a closed loop, not perfect parts.

## 6. Checkpoint checklist (copy into the group chat at each gate)

| Gate | Time | Pass when |
|---|---|---|
| C0 | 9:15 PM | `curl localhost:8000/fleet` works with `MOCK=1` for all four |
| L1 | 10:00 PM | `make demo-check` runs end to end on stubs |
| C1 | 12:45 to 1:30 AM | `make demo-check` passes with `MOCK=0`, scripted agents |
| C2 | 3:15 AM | all six features at L2 or their L1 wired in; freeze |
| C3 | 5:45 AM | rehearsed 3x; backup video; submitted |
