# Scope: we are building AeroTwin AI, exactly as the concept doc describes

**Decision (Sat night, 2026-10-10):** the project is **AeroTwin AI**, the concept written by **Aadhavan** ([`docs/AEROTWIN-CONCEPT.md`](docs/AEROTWIN-CONCEPT.md)).
Shamit's separate climate-routing project ("Meridian") is **parked**, not part of this build; its files are in [`parked-meridian/`](parked-meridian/).
We build the six core features of the doc (sections 3.1 to 3.6), the closed "self-adjusting plan" loop (section 4) and the demo (section 11).

Time: hacking ends **Sun Oct 11, 9:00 AM ET** (official guide, checked 2026-10-10; see [`docs/HACKLANTA-RULES.md`](docs/HACKLANTA-RULES.md)). The schedule below was planned for a 7 AM finish, so 2 hours are spare buffer. Clock times below are ET.

## 0. Do these first (30 minutes, before anyone builds)

1. **Inventory what already exists.** If anyone has code, data or a UI from the last 25 hours, list it in `docs/INVENTORY.md` (what, who, does it run) and build on it instead of restarting.
2. **Confirm the event rules** (pre-written code, open-source use, submission format and deadline). Done: `docs/HACKLANTA-RULES.md`. No AI-use statement in the README or Devpost unless Shamit asks for one or an event rule requires it (then run it by Shamit first). The Hacklanta guide has no such rule (checked 2026-10-10).
3. Everyone reads their work package in `work/` and sets up their Claude with `CLAUDE-PROMPT.md`.
4. Freeze the contracts (`CONTRACTS.md`) and bring up the mock API (Shamit, by 9:15 PM).

## 1. Scope decision

| In (this build) | Out (parked or later) |
|---|---|
| Doc 3.1 predictive health (anomaly + RUL on NASA C-MAPSS) | Climate-aware routing, contrails, NOx, emissions |
| Doc 3.2 digital twin state + interactive UI | Weather feeds, live ADS-B, real-flight data |
| Doc 3.3 fleet-aware scheduling (OR-Tools) | Route optimization of any kind |
| Doc 3.4 multi-agent orchestration with real tools | Redis/Kafka/Timescale/Kubernetes |
| Doc 3.5 what-if simulation and replanning | 3D aircraft, LSTM, LangGraph (stretch only) |
| Doc 3.6 explainable copilot with work-order approval | Real airline data, real manuals, real tail numbers (never) |
| Doc 4 killer feature: detect, predict, investigate, simulate, optimize, recommend, replan, approve | |

**Why:** one connected story that proves the doc's own claim ("predicts, decides, acts through tools, simulates, and adapts") is worth more than two half-built products.
The doc's own must-have list (section 12) is already a complete demo.

## 2. What we drop or postpone (10 hours is short)

- Climate routing and everything weather-related: parked in `parked-meridian/`.
- Heavy infrastructure: no Redis, Kafka, Timescale or Kubernetes. FastAPI + SQLite + in-process calls.
- 3D (react-three-fiber), LSTM models, LangGraph: **stretch**. A 2D clickable aircraft and plain Claude tool-calling are enough.
- Live data feeds: everything runs on NASA C-MAPSS plus a synthetic fleet, as the doc says.

## 3. Decisions

| # | Decision | Status |
|---|---|---|
| 1 | Product name | **AeroTwin AI** (the repo is `Flash0529/aerotwin-ai`, renamed from `meridian-brie`; the old link redirects) |
| 2 | Who wrote the concept doc | **Aadhavan**. He is the **concept owner**: when a question starts with "what did the doc intend?", he decides |
| 3 | What code exists already | Open: inventory in section 0 |
| 4 | Event rules and deadline | **Done 2026-10-10:** [`docs/HACKLANTA-RULES.md`](docs/HACKLANTA-RULES.md). Deadline 9:00 AM Sun; table judging, 3 min pitch + 2 min Q&A for the whole team; Devpost + public repo + public video + Google Form |
| 5 | Where code lives | In this repo: `app/` (Python) and `web/` (React). Public repo: **no keys, ever** |

## 4. Roles

**Who owns what is written in one place: [`work/OWNERSHIP.md`](work/OWNERSHIP.md). It wins over this summary.** Roles were chosen by task difficulty and how much each task depends on the others; each person has a clear interface (`CONTRACTS.md`), a deadline and a fallback.

| Person | Summary (see OWNERSHIP.md for the exact pieces) |
|---|---|
| **Shamit** | Contracts, database, API skeleton + mock API, and the front end (shell, Fleet Overview, Planning, Simulation Lab, Copilot + Agent Activity + approvals); integration lead and merger |
| **Aadhavan** | The simulator (fleet generator, telemetry feed, scenario injection), the Aircraft Twin screen, and the story (demo script, video, Devpost); **concept owner** |
| **Aashirth** | ML health models and the twin state engine (3.1, 3.2 backend); then LLM mode and the copilot backend if time allows |
| **Pardhu** | Scheduler and what-if (3.3, 3.5) and the agent tool registry + scripted runner (3.4) |

Pairs for sleep shifts: **Shamit + Aadhavan**, **Aashirth + Pardhu** (at least one person from each pair is awake until the freeze). Everyone works with their own Claude using `CLAUDE-PROMPT.md`. Names are first names only in this public repo.

## 5. Schedule (clock times, ET)

| Time | What | Checkpoint |
|---|---|---|
| Sat 8:30-9:15 PM | Section 0, contracts, mock API up | **C0:** everyone can run the mock |
| 9:15 PM-12:45 AM | Parallel build of the four owned pieces (L1 stubs by 10:00 PM, then L2) | |
| 12:45-1:30 AM | **First end-to-end (ugly):** inject anomaly -> model -> scheduler -> plan on screen | **C1:** the loop closes, even with fake numbers |
| 1:30-3:15 AM | Disruption + replan, agents with real tools (LLM mode), copilot, baseline-vs-optimized numbers. Sleep shift 1 starts | **C2 (3:15 AM): FEATURE FREEZE** |
| 3:15-5:45 AM | Hardening, `make demo-check`, README, Devpost, backup video, rehearse 3x. Sleep shift 2 | **C3:** demo rehearsed, video recorded |
| 5:45-6:30 AM | Submit (earlier if the real deadline is earlier) | Submitted |
| 6:30-7:00 AM | Buffer | |

**Cut lines.** At C1 if the loop is not closed: drop the copilot Q&A (keep 3 questions), the data-quality panel, chart polish. At the freeze: only fixes and story.

## 6. Scope tiers

**MUST** (this is the doc's own section 12 must-have list):
1. Synthetic fleet and airport data (20-50 aircraft or fewer).
2. One working anomaly detection or RUL model, with MAE/RMSE reported.
3. Component-level digital twin state.
4. Constraint-based (OR-Tools) maintenance scheduler; reports "no feasible plan" honestly.
5. Two or three agents using real tools (Health, Scheduling, Logistics; Coordinator assembles).
6. One disruption scenario with automatic replanning.
7. Baseline versus optimized results, with the baseline stated.

**SHOULD** (the doc's should-have list): clickable 2D aircraft, live-looking telemetry, fleet map, maintenance timeline, explainable recommendations, scenario comparison charts.
**COULD / stretch:** 3D aircraft, technician skill matching depth, spare-part repositioning, multiple simultaneous faults, a retrieval assistant over (synthetic) maintenance documents.

## 7. Contracts

Exact models, tables, endpoints and function signatures: [`CONTRACTS.md`](CONTRACTS.md). How they connect: [`INTEGRATION.md`](INTEGRATION.md).

## 8. Agents and their tools

Health, Scheduling, Logistics, Operations and Coordinator agents, each with deterministic tools: table in `CONTRACTS.md` section 6. Rules: the LLM picks tools and writes the explanation;
**every number comes from a tool result**; nothing is "approved" without a human click; the system never proposes maintenance **later** than an approved deadline.

## 9. Demo (3 minutes; the doc's section 11 flow)

1. **Fleet** (20 s): 20-50 simulated aircraft on a map, health colors, stations with technicians and parts. SIMULATED badge visible.
2. **Anomaly** (30 s): inject an abnormal engine temperature trend; the model flags it and shows RUL with uncertainty.
3. **Agents** (40 s): Health, Scheduling and Logistics call their tools on screen; the Coordinator assembles a plan with evidence.
4. **Plan** (30 s): best station and window, and the constraints that decided it.
5. **Disruption** (40 s): delay the part or close the facility; the plan recomputes; show what changed and why.
6. **Impact** (20 s): baseline vs optimized (downtime, flights affected, logistics cost, utilization), with the baseline and "simulated" stated.

## 10. Honest limits to say out loud

- The prediction model is trained on NASA C-MAPSS (or synthetic C-MAPSS-shaped data if the download fails), not on airline data.
- Savings are **simulated**, relative to a stated baseline; they are not verified airline savings or real-world safety improvements.
- The knowledge base and the task durations are synthetic demo values, not real maintenance data.
- Advisory only: an authorized professional approves every action; it does not replace approved procedures or airworthiness assessments.
- Fictional tail numbers; no real manuals; no real telemetry.

## 11. Top risks now

| Risk | Mitigation |
|---|---|
| Not enough time to close the loop | Closed loop with fake numbers by 1:30 AM (C1), then improve the numbers |
| Four people, one repo, merge pain | Folders per owner; small commits; contracts first; Shamit merges to `main` |
| Teammates cannot push yet | Add them as collaborators, or they open pull requests from forks |
| Agent flakiness in the demo | Scripted mode and a cached golden run; backup video |
| Rules conflict (pre-written code) | Checked on 2026-10-10: see `docs/HACKLANTA-RULES.md` |
| Tired team | Sleep shifts in pairs; freeze at 3:15 AM |
