# Merge plan: AeroTwin AI + Meridian, in the time that is left

**Written Sat 2026-10-10, ~8:30 PM ET. Read this before the older docs: it supersedes the schedule (plan section 11)
and the roles (plan 11.3).** Times assume a 36-hour event that started Fri 7 PM ET and ends Sun 7 AM ET, which is
**about 10.5 hours from now**. That is unverified: check the real submission deadline (it may be earlier).

> **Update (Sat night): features 3.1 to 3.6 of the AeroTwin doc are built FIRST.** The detailed spec is `FEATURES-3.1-3.6.md`, the exact interfaces are `CONTRACTS.md`, how it all connects is
> `INTEGRATION.md`, the per-person timeline is `TASK-BOARD.md`, and each person's package is in `work/`. Where this file disagrees on roles or order, `TASK-BOARD.md` wins.
> The climate route module is Phase 2 (after all six features reach L2).

## 0. Do these first (30 minutes, before anyone builds)

1. **Inventory what already exists.** This plan assumes little or no code. If anyone has code, data or a UI from the
   last 25 hours, list it in `docs/INVENTORY.md` (what, who, does it run) and build on it instead of restarting.
2. **Confirm the rules** (pre-written code, open-source use, AI-assistance policy, submission format and deadline).
3. **Decide the name** (section 3). **Confirm roles** (section 4). **Freeze contracts v0** (section 7).
4. Everyone sets up their Claude with `CLAUDE-PROMPT.md` and reads this file.

## 1. One product, two halves

Both documents describe nearly the same machine. AeroTwin AI is the more complete maintenance and agent story;
Meridian adds the climate-aware routing and the real-data fabric.

| Capability | AeroTwin AI doc | Meridian plan | Merged decision |
|---|---|---|---|
| Component digital twin + RUL | Yes: Isolation Forest, XGBoost/RF, C-MAPSS | Yes: wear laws, Kalman, C-MAPSS | **Use AeroTwin's ML** (Isolation Forest + XGBoost/RF on C-MAPSS) for prediction; keep our wear-law driver table only if time allows |
| Maintenance scheduler | OR-Tools: windows, technicians, parts, deadlines | OR-Tools CP-SAT: stations, turns, parts, deadlines | **One scheduler.** Take AeroTwin's richer inputs (technician skills, facility capacity). Keep our **monotone safety rule** (earlier only, never later than an approved interval) |
| Multi-agent orchestration | Health / Scheduling / Logistics / Operations / Coordinator | Not covered (LLM only explains) | **Adopt AeroTwin's agents.** This is the headline for an "agents and assistants" theme |
| What-if simulation | Discrete-event sim, scenario injection | Scenario runner, fault injection | **Adopt AeroTwin's lab**; our scenario YAML idea is the same thing |
| Copilot Q&A | Yes | Cited Q&A over a synthetic manual | Merge: one copilot, answers only from tool output and the approved knowledge base |
| 3D clickable aircraft | Should-have | 2D SVG is enough | **2D schematic first**; 3D is a stretch |
| **Climate-aware routing** (CO2, contrails, NOx) | **Missing** | Core module M1 | **Keep as the second act** (section 6) |
| Weather + live flight data | Stretch only | Real AWC, OpenSky, NOAA | Keep, light (map overlay + router input) |
| Real-vs-simulated data design, adapters | Brief | Detailed | Keep for the write-up and the "how it would really arrive" answer |
| Avoid-list, legal, safety framing | Brief disclaimers | 52-item list | Keep; the two documents agree on advisory-only and human approval |

**Pitch in one line:** *AeroTwin AI is a team of tool-using agents that predicts what will fail, plans where and when to fix it
across a fleet, and re-plans when reality changes, and it prices the climate cost of the flights that plan causes.*
Lead with the agents and the closed loop; the climate coupling is the differentiator nobody else on the floor will have.

## 2. What we drop or postpone (10 hours is short)

- Redis Streams, Kafka, Timescale, Kubernetes: **no.** FastAPI + SQLite (or Postgres if already running) + in-process events.
- 3D aircraft (React Three Fiber), LSTM models, LangGraph: **stretch.** Plain tool-calling with the Claude API is enough.
- The 36-hour schedule and the full 52-item risk process: keep the avoid-list (`HACKATLANTA-PLAN.md` section 9), drop the rest.
- Full contrail physics (`pycontrails` CoCiP) live: **no.** Precompute offline or use a contrail-risk proxy field (section 6).
- Real-feed adapters beyond a map overlay: **no.**

## 3. Decisions needed now (Shamit)

| # | Decision | Recommendation |
|---|---|---|
| 1 | Product name: **AeroTwin AI** or Meridian? | **AeroTwin AI** for the product (the doc already has the pitch and tagline); call the climate module **AeroTwin Route** (Meridian). Rename the repo from `meridian-brie` when decided |
| 2 | Who wrote the AeroTwin doc? | Whoever wrote it should own the part it describes most (ML + scheduler or the agents). Swap rows in section 4 accordingly |
| 3 | What code exists already? | Inventory first (section 0) |
| 4 | Event rules and deadline | Check on Devpost / hacklanta.dev now |
| 5 | Where does code live? | In this repo, in folders (`sim/ ml/ solver/ agents/ api/ web/ router/`), once the rules allow. Public repo: **no keys, ever** |

## 4. Roles

Chosen by task difficulty and how much each task depends on the others, not by seniority. Each person gets the piece that
fits, a clear interface (section 7) and a fallback.

| Person | Owns | Deliverable by 1:00 AM | Fallback if stuck |
|---|---|---|---|
| **Shamit** | **Platform + integration + agents + copilot backend:** contracts, fleet/telemetry simulator, API, scenario injection, agent runner (scripted then LLM), copilot, work orders, final demo build | C0 (skeleton, models, fixtures, mock API) by 9:15 PM | Hard-code the fleet; agents become a scripted pipeline with the same tool interface |
| **Aashirth** | **ML + twin state (3.1, 3.2 backend):** anomaly model + RUL on C-MAPSS (MAE/RMSE), health scoring, data quality, component state engine | Sensor mapping pushed; model trained and scoring one engine; twin serving `GET /aircraft/{tail}` | Score with a rule on sensor z-scores; keep state in memory |
| **Pardhu** | **Scheduler + what-if (3.3, 3.5):** task cards, ground windows, tasks from RUL, greedy/static baseline/CP-SAT, replan, compare. *Climate route module comes after all six features reach L2* | Greedy + baseline plan on the golden state; CP-SAT by 12:15 AM | Ship greedy as "optimized" and say so |
| **Aadhavan** | **Front end + story:** fleet map, aircraft twin (2D, clickable), planning Gantt, simulation lab, copilot drawer + agent activity + approvals; then demo script, video, Devpost | UI running against fixtures / the **mock API** | Static screens fed by JSON files |

Pairs for sleep shifts (so a stronger and a newer builder are always awake together): **Shamit + Aadhavan**, **Aashirth + Pardhu**.
Everyone works with their own Claude using `CLAUDE-PROMPT.md`. Names are first names only in this public repo.

## 5. Schedule (clock times, ET)

| Time | What | Checkpoint |
|---|---|---|
| Sat 8:30-9:15 PM | Section 0: inventory, rules, name, roles, **contracts v0**, mock API up | **C0:** everyone has the contracts and can run the mock |
| 9:15 PM-12:45 AM | Parallel build of the four owned pieces (section 4) | |
| 12:45-1:30 AM | **First end-to-end (ugly):** inject anomaly -> model -> scheduler -> plan on screen | **C1:** the loop closes, even with fake numbers |
| 1:30-3:15 AM | Disruption + **replan**, agents with real tools, route panel wired in, baseline-vs-optimized numbers. Sleep shift 1 (1:30-3:30) starts | **C2 (3:15 AM): FEATURE FREEZE** |
| 3:15-5:45 AM | Hardening, `make demo-check`, README, Devpost write-up, record **backup video**, rehearse 3x. Sleep shift 2 | **C3:** demo rehearsed, video recorded |
| 5:45-6:30 AM | Submit (earlier if the real deadline is earlier) | Submitted |
| 6:30-7:00 AM | Buffer, final checks | |

**Cut lines.** At C1 (1:30 AM) if the loop is not closed: drop the copilot Q&A, the data-health panel and the route panel's live
wiring (serve precomputed route results). At the freeze: no new features, only fixes and story.

## 6. Scope: merged must / should / could

**MUST (the demo)** — this is AeroTwin's own must-have list, plus one climate panel:
1. Synthetic fleet (20-50 aircraft or fewer), airports, stations with technicians and parts.
2. One working model: anomaly detection or RUL (C-MAPSS), with MAE/RMSE reported.
3. Component-level twin state (engine, brakes, landing gear, hydraulics, avionics).
4. OR-Tools scheduler with technician, part, facility and deadline constraints; reports "no feasible plan" honestly.
5. Two or three agents using real tools (Health, Scheduling, Logistics; Coordinator assembles).
6. One disruption (delayed part or closed facility) that triggers automatic replanning.
7. Baseline vs optimized results (downtime, flights affected), with the baseline stated.
8. **Climate panel:** for the flight(s) the plan repositions, show route options (fuel vs CO2e) and the climate cost of the
   detour, labeled with its metric (GWP100) and as a prototype estimate.

**SHOULD:** clickable 2D twin; fleet map with live weather overlay; maintenance timeline; copilot Q&A; scenario comparison chart.
**COULD:** 3D aircraft, live ADS-B overlay, technician skill matching depth, multiple simultaneous faults.

**How the router joins the loop (small, honest version).** When the scheduler considers repositioning an aircraft to a
maintenance station, it asks the route module for `route_cost(from, to, depart_time)` -> `{fuel_kg, minutes, co2e_kg}`
and adds that to the plan cost. The climate result is a number in the plan and a panel in the UI, not a separate product.

## 7. Contracts v0 (freeze in the first 45 minutes; keep tiny)

All times UTC ISO-8601 (`...Z`). Units SI inside. Every record has `synthetic: true`. Tails are fictional (`SIM-xxx`).

| Entity | Fields |
|---|---|
| `Aircraft` | `tail, type, location_iata, status` |
| `Component` | `tail, id, kind, health (0-1), anomaly_score, rul_cycles {p10,p50,p90}, updated` |
| `Leg` | `id, tail, dep_iata, arr_iata, sched_dep, sched_arr` |
| `Station` | `iata, hangar_slots, technicians [{id, skills[]}], parts [{part, qty}], parts_eta_h` |
| `Task` | `id, tail, component, duration_h, deadline, required_skill, part` |
| `Plan` | `assignments [{task, station, start, end}], metrics {downtime_h, flights_affected, cost, co2e_kg}, feasible, evidence []` |
| `Event` | `type (anomaly | part_delay | facility_closed | tech_unavailable), params, at` |

| Endpoint | Purpose |
|---|---|
| `GET /fleet`, `GET /aircraft/{tail}` | Fleet and one twin |
| `GET /plan`, `POST /plan/replan` | Current plan; recompute |
| `POST /scenario/inject` | Inject an `Event` |
| `POST /agents/run` | Run the agent workflow, return plan + evidence |
| `GET /route/options?from=&to=&t=` | Route options (fuel, minutes, CO2e) |
| `POST /plan/{id}/decision` | Approve / reject (audited) |
| `GET /metrics` | Baseline vs optimized |

Rule: **change a contract only with a one-line note in `contracts/CHANGELOG.md` and a ping to the other three.**

## 8. Agents and their tools (so "agents" means real tool calls)

| Agent | Tools (all deterministic code) |
|---|---|
| **Health** | `get_component_health(tail)`, `get_rul(tail, component)`, `get_history(tail)` |
| **Scheduling** | `find_ground_windows(tail, horizon)`, `solve_schedule(tasks, constraints)`, `compare_plans(a, b)` |
| **Logistics** | `check_parts(station, part)`, `match_technicians(station, skill, window)`, `shortage_report()` |
| **Operations** | `simulate_plan(plan)` -> metrics, `route_cost(from, to, t)`, `flights_affected(plan)` |
| **Coordinator** | `assemble_plan(...)`, `draft_work_orders(plan)` -> *pending human approval*, writes the evidence trail |

Rules (from both documents): the LLM picks tools and writes the explanation; **every number comes from a tool result**; the
schema is enforced in code; nothing is "approved" without a human click; the system never proposes maintenance **later** than
an approved interval.

## 9. Demo (3 minutes)

1. **Fleet** (20 s): aircraft on a map, health colors, stations with technicians and parts. SIMULATED badge visible.
2. **Anomaly** (30 s): inject an engine fault; the model flags it and shows RUL with uncertainty.
3. **Agents** (40 s): Health, Scheduling and Logistics call their tools on screen; the Coordinator assembles a plan with evidence.
4. **Plan** (30 s): best station and window, constraints that mattered, and the climate cost of the reposition leg.
5. **Disruption** (40 s): delay the part or close the facility; the plan recomputes; show what changed and why.
6. **Impact** (20 s): baseline vs optimized (downtime, flights affected), with the baseline and "simulated" stated.

## 10. Honest limits to say out loud

- Wear and degradation constants are assumptions; the ML model is validated on NASA C-MAPSS, not on airline data.
- Savings are **simulated**, relative to a stated baseline; not verified airline savings.
- Climate numbers depend on the metric (GWP100 here) and on weather-model quality; contrail-only optimization can raise NOx.
- Advisory only; humans approve; not certified; no real tail numbers or manuals.

## 11. Top risks now

| Risk | Mitigation |
|---|---|
| Not enough time to close the loop | Closed loop with fake numbers by 1:30 AM (C1), then improve numbers |
| Four people, one repo, merge pain | Folders per owner; small commits; contracts first; one integrator (Shamit) |
| Teammates cannot push yet | Add them as collaborators, or they open pull requests from forks (public repo) |
| Agent flakiness in the demo | Cache the golden run's agent outputs; offline mode; backup video |
| Rules conflict (pre-written code, AI use) | Check in the first 30 minutes; disclose AI use where required |
| Tired team | Sleep shifts in pairs; freeze at 3:15 AM |
