# Work package 1: Shamit. Platform, integration, agents (3.4) and copilot backend (3.6)

You are the **integrator**: you own the spine everyone plugs into, the agent workflow that calls everyone's code, and the final demo build.
Your top rule: **the other three must never wait on you after 9:15 PM.** Publish stubs early; make real things later.

## What you own

```
app/main.py  settings.py  db.py  contracts/models.py
app/sim/        fleet.py clock.py telemetry.py scenarios.py
app/agents/     tools.py runner.py llm.py prompts.py scripted.py
app/copilot/    chat.py workorders.py  (kb/*.yaml is authored by Pardhu)
fixtures/  scripts/golden.py  scripts/reset_db.py  tests/test_contracts.py  tests/test_agents.py  Makefile  requirements.txt  .env.example  .gitignore
```

Features: foundation, **3.4**, **3.6 backend**. Read `FEATURES-3.1-3.6.md` (3.4 and 3.6), `CONTRACTS.md` (all), `INTEGRATION.md` (all).

## Step-by-step (with time boxes)

### A. C0 by 9:15 PM (45 min): the spine and the mock
1. `git clone` the repo; create the folder skeleton from `INTEGRATION.md` section 2 (empty packages with `__init__.py`).
2. Copy the models from `CONTRACTS.md` section 2 into `app/contracts/models.py`. The block already ends with the `model_rebuild()` calls that resolve forward references;
   keep them at the end of the file. Run `python -c "from app.contracts.models import *"`.
3. `app/settings.py`: read `.env` with `python-dotenv`: `MOCK`, `SEED`, `LLM_MODEL`, `ANTHROPIC_API_KEY`, `DB_PATH`, per-module `MOCK_*` flags.
4. `app/db.py`: `init_db()` creating the tables from `CONTRACTS.md` section 3; helpers `put_json(table, key, model)`, `get_json(...)`, `all_json(...)`.
5. `fixtures/`: write the 9 fixture files (use Claude: "generate fixtures that validate against these models; SIM-017 is the golden aircraft"). Keep them small but valid.
6. `app/main.py`: include a router per module. For each endpoint in `CONTRACTS.md` section 4, a mock handler returning the fixture when `MOCK=1`.
   Owners will replace handlers with real ones; you only keep the mounting and the mock switch.
7. `Makefile` targets: `reset`, `api`, `web`, `test`, `demo-check`, `data` (downloads C-MAPSS via Aashirth's `ml/cmapss.py --download`).
8. Push to `main`. **Message the group: "C0 is up, `MOCK=1 make api`".** Then tell Aashirth/Pardhu the exact fixture file names they should extend.

> Prompt for your Claude: "Create the FastAPI skeleton described in INTEGRATION.md section 2 and CONTRACTS.md. Every endpoint returns its fixture when MOCK=1.
> Generate fixtures that validate against app/contracts/models.py. Add a test that parses every fixture into its model."

### B. Simulator (9:15 to 11:00 PM, 105 min)
1. `sim/clock.py`: `SimClock` stored in `kv`; `now()`, `advance(hours)`. Start: `2026-10-12T12:00:00Z`.
2. `sim/fleet.py`: `generate_fleet(seed)`: 24 aircraft `SIM-001..024` (types: 16 `A320-class`, 8 `B738-class`), home hubs spread over the 8 stations; each has
   6 components (`engine_1`, `engine_2`, `brakes`, `landing_gear`, `hydraulics`, `avionics`); cycles/hours plausible; **legs**: 4 per day per aircraft
   for 5 days forming a valid rotation (arrive where the next leg departs; some overnight at hubs); **stations**: 8 with technicians
   (skills engine/gear/hydraulics/avionics, shifts), parts (BORESCOPE-KIT, BRAKE-ASSY, HYD-PUMP, AVIONICS-LRU) and hangar slots.
   Make it seeded and deterministic. Dump `fixtures/fleet.json`, `stations.json`, `legs_golden.json` from it.
3. **Golden constraints** (assert in a test): SIM-017's rotation includes a >= 6 h ground window at DFW before day 5; ATL has a hangar but **0
   BORESCOPE-KIT on hand** (eta 18 h via DFW); DFW has 2 kits and an engine technician on shift during that window.
4. `sim/telemetry.py`: `TelemetrySource`: for engines, use `ml.cmapss.trajectories()` (Aashirth): assign each engine a C-MAPSS unit and a position;
   `advance(tail, component_id, n_cycles) -> list[TelemetryRow]` (values keyed by the names in `ml/sensors.py`). Other components: a small
   generator (linear wear + noise + optional step). Until Aashirth's loader lands, use a stand-in that fabricates a drifting T50.
5. `POST /sim/step {hours}`: advance clock; for each aircraft count completed legs in the interval (= cycles); pull that many rows per component;
   call `twin.state.ingest_telemetry(row)` (Aashirth); return counts.
6. `sim/scenarios.py`: `inject(event)`: `anomaly` -> set the engine's position near end of life (RUL ~ 20 to 25) and push 5 to 10 rows so the twin
   sees the trend; `accelerated_degradation` -> multiply wear rate; `part_delay`, `tech_unavailable`, `facility_closed` -> update station records in the
   DB and then call `whatif.replan` (Pardhu) when asked. Write an audit entry and a `TwinEvent`.
7. `POST /scenario/reset {name:"golden"}` = `reset_db.py`.

### C. Agents: scripted first (11:00 PM to 12:30 AM)
1. `agents/tools.py`: a registry `TOOLS: dict[str, ToolSpec]` (`name, description, input_schema (JSON schema), fn`) implementing the table in
   `CONTRACTS.md` section 6 by calling the owners' service functions. Every tool returns `{"data": ..., "evidence": EvidenceRef}`.
2. `agents/scripted.py`: `run_scripted(goal, scope)` runs, in order: Health (`list_at_risk`, `get_component_health`, `get_sensor_trend`), Scheduling
   (`derive_tasks`, `find_ground_windows`, `solve_schedule(cpsat)`, `solve_schedule(static_baseline)`, `compare_plans`), Logistics (`check_parts`,
   `match_technicians`, `shortage_report`), Operations (`simulate_plan`, `flights_affected`), Coordinator (`draft_work_orders`, `search_kb`).
   After each tool call append an `AgentStep` to the stored `AgentRun` (so the UI sees progress). Explanation text comes from a template filled
   with tool outputs.
3. `POST /agents/run` starts the run in a background thread (`threading.Thread` or FastAPI `BackgroundTasks`), returns the `AgentRun` immediately with
   `status: running`; `GET /agents/runs/{id}` returns the current state.
4. `scripts/golden.py` + `make demo-check`: reset -> step 24 h -> inject anomaly -> run agents (scripted) -> assert at-risk contains
   `SIM-017/engine_2`, plan feasible, >= 1 work order, evidence present -> approve -> replan after `part_delay` -> assert diff non-empty. Prints a table.

### D. LLM mode (1:30 to 2:30 AM)
1. `agents/llm.py`: `run_agent(agent, goal, context, tools)` implementing the tool loop (`INTEGRATION.md` section 5). Check the current `anthropic`
   SDK docs (or use the `claude-api` skill) for exact request/response fields. `max_steps=8`, per-call timeout 20 s, retries 1.
2. `agents/prompts.py`: one system prompt per agent. Skeleton (adapt, keep short):
   - Common: "You are the {role} agent in an aircraft maintenance planning system. This is a simulated prototype. You are advisory only; humans approve every action.
     Use ONLY the provided tools for facts and numbers. Never state a number that a tool did not return. Never propose maintenance later than a task deadline.
     If information is missing, list it in missing_info instead of guessing. Do not invent part numbers, limits or intervals; use search_kb."
   - Health: "Find aircraft needing attention, explain which sensors drive the anomaly, report RUL with uncertainty."
   - Scheduling: "Find feasible windows and solve the schedule; compare with the static baseline."
   - Logistics: "Check parts and technicians for the proposed assignments; report shortages."
   - Operations: "Simulate the plan; report flights affected and downtime."
   - Coordinator: "Merge the findings, flag conflicts, write a short explanation, draft work orders for human approval."
3. **Number check:** after the Coordinator writes the explanation, extract numerals with a regex and require each to occur in some tool output (as text);
   if not, replace the explanation with the template and log a warning. Keep this check; it is your safety story.
4. **Cache:** key = hash of (goal, snapshot hash); store the finished `AgentRun` in `data/agent_cache.json`; `?replay=1` returns it with steps replayed
   at 0.5 s intervals (useful for the live demo).
5. Mode handling: `mode=llm` falls back to `scripted` on any error and records the real mode in `AgentRun.mode`.

### E. Copilot (2:30 to 3:15 AM, stop at the freeze)
1. `copilot/chat.py`: `chat(req)`: same LLM loop with all read tools + `solve_schedule` + `replan_with_event` + `draft_work_orders` + `search_kb`. Return
   `ChatResponse` with evidence (from the tool calls), `missing_info`, `proposed_work_orders`.
2. Scripted fallback: keyword intents for the six example questions (at-risk in 72 h, why A over B, best windows tomorrow, parts/technicians,
   facility unavailable, compare schedules) calling the matching tools. Three working questions beat six flaky ones.
3. `copilot/workorders.py`: `decide()` writes the decision + audit entry; `GET /workorders`, `GET /audit`.

## What you give to others

| To | What | When |
|---|---|---|
| Everyone | `main.py`, `models.py`, fixtures, mock API, Makefile | 9:15 PM |
| Aashirth | `TelemetryRow` shape, the DB tables, `sim/telemetry.py` calling `ml.cmapss` | 9:30 PM |
| Pardhu | `legs_golden.json`, `stations.json`, DB snapshot (`twin.snapshot()` with Aashirth) | 9:45 PM |
| Aadhavan | working endpoints for fleet, aircraft, plan (Pardhu), agents, copilot; agent-run fixture with steps | as built |

## What you need from others

Aashirth: `ml.cmapss.trajectories()`, `ml/sensors.py`, `twin.state.ingest_telemetry/snapshot`. Pardhu: `solve/derive_tasks/ground_windows`, `replan`,
`compare`, `simulate`, KB task cards. If any is late, call its L1 stub or fixture and carry on.

## Cut lines

- 12:45 AM and the loop is not closed: freeze LLM work; scripted agents + plan + replan must work first.
- 3:15 AM: no copilot beyond 3 intents? Ship the 3 intents. The LLM copilot is optional; scripted agents are not.

## Pitfalls

- Don't use `datetime.now()` anywhere in app code; use `SimClock`.
- `AgentRun` is updated from a thread while the UI polls: write each step in one SQLite transaction; use `check_same_thread=False` or a connection per call.
- Never print or log `ANTHROPIC_API_KEY`; `.env` stays out of git; run a secret scan before every push.
- JSON-serialize numpy floats with `float(x)`; pydantic will otherwise fail.
- Keep the mock switch honest: `MOCK=1` must not import heavy modules (so the UI team's laptops start instantly).

## Prompt addendum for your Claude

"My role: platform + integration + agents (features 3.4 and 3.6 backend) per work/01-SHAMIT-platform-agents-copilot.md. First make C0 (skeleton, models, fixtures, MOCK API), then the simulator,
then scripted agents, then LLM mode. Never block teammates: ship L1 stubs early. Numbers shown to users must come from tool outputs; keep the number-check."

## Definition of done

- [ ] C0 pushed on time; teammates unblocked
- [ ] Deterministic fleet and golden constraints tested
- [ ] `make demo-check` passes in scripted mode, then LLM mode
- [ ] Every work order has evidence; approval writes an audit entry
- [ ] Offline demo works (scripted + cached run); no secrets in the repo
