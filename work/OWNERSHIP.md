# OWNERSHIP: who owns what (the ONLY place owners are written; it wins over every other doc)

**Changed Sat night:** Shamit and Aadhavan swapped jobs. If any other doc names a different owner, this file is right. **The specs did not change, only who builds them.**
The spec for each piece is in the file and section named in the "Spec" column.

Status: **confirmed** = said by Shamit. **proposed** = my suggestion to fill a gap or balance the load; Shamit confirms or changes it.

| # | Piece | Spec | Owner | Status |
|---|---|---|---|---|
| 1 | Contracts: `app/contracts/models.py`, `CONTRACTS-CHANGELOG.md` | `CONTRACTS.md`, `work/01` §A | **Shamit** | confirmed |
| 2 | Database `app/db.py`, `reset_db.py`, fixtures, `Makefile`, `requirements.txt` | `work/01` §A | **Shamit** | confirmed |
| 3 | API skeleton `app/main.py`, `MOCK=1` mode, router mounting | `work/01` §A | **Shamit** | confirmed |
| 4 | Web app shell + **Fleet Overview** screen | `work/04` Setup, screen 1 | **Shamit** | confirmed ("front end") |
| 5 | **Planning board** UI (Gantt, baseline vs optimized) | `work/04` screen 3 | **Shamit** | confirmed |
| 6 | **Simulation Lab** UI | `work/04` screen 4 | **Shamit** | confirmed |
| 7 | **Copilot drawer + Agent Activity + work-order approval** UI | `work/04` screen 5 | **Shamit** | confirmed |
| 8 | Merging to `main`, `make demo-check`, integration lead | `INTEGRATION.md` §9 | **Shamit** | confirmed (unchanged) |
| 9 | **Simulator, part 1:** fleet generator (`sim/fleet.py`: aircraft, rotations/legs, stations, technicians, parts, **golden constraints**) and `SimClock` | `work/01` §B.1-B.3 | **Aadhavan** | confirmed ("he is doing the simulations") |
| 10 | **Simulator, part 2:** telemetry source (C-MAPSS replay + synthetic generators), `POST /sim/step`, **scenario injection** (`/scenario/inject`, `/scenario/reset`) | `work/01` §B.4-B.7 | **Aadhavan** | confirmed (same) |
| 11 | **Agent tool registry + scripted runner** (feature 3.4, scripted mode) | `work/01` §C | **Pardhu** | proposed (the tools wrap his solver and what-if functions) |
| 12 | **LLM mode** for agents (tool loop, prompts, number-check, cache) **and the copilot backend** (chat, KB search, work orders, audit; features 3.4 LLM + 3.6 backend) | `work/01` §D, §E | **Aashirth** | proposed. **First thing cut if time is short** (scripted agents are enough for the demo) |
| 13 | **Aircraft Twin screen** (clickable 2D aircraft, RUL band, sensor trend, timeline) (3.2 UI) | `work/04` screen 2 | **Aadhavan** | proposed (he keeps it) |
| 14 | Demo script, backup video, Devpost text; **concept owner** | `work/04` "The story" | **Aadhavan** | proposed (he wrote the doc) |
| 15 | ML health (3.1), twin state engine (3.2 backend), `/aircraft/*`, `/metrics` ML block | `work/02` | **Aashirth** | unchanged |
| 16 | Scheduler (3.3), what-if (3.5), task cards, `/plan*`, `/whatif/*`, `/metrics` plan block | `work/03` | **Pardhu** | unchanged |

Open question for Shamit: "simulations" is read here as the **data simulator** (9 and 10). The **what-if simulation** (3.5, piece 16) stays with Pardhu unless Shamit says otherwise.
Also: if "Copilot" in Shamit's list meant the backend too, the copilot part of piece 12 moves to Shamit.

## Load check (rough hours with Claude; about 9 hours remain)

| Person | Pieces | Hours (estimate) | Cut line if behind at C1 (~1:30 AM) |
|---|---|---|---|
| Shamit | 1-8 | about 9 to 10 | Lab UI = three buttons + a diff list; Planning board without hatching/zoom |
| Aadhavan | 9, 10, 13, 14 | about 9 | Twin screen on fixtures only; story starts at 3:15 AM at the latest |
| Aashirth | 12, 15 | about 10 | **Drop piece 12 entirely** (no LLM mode, copilot = 3 canned questions from the scripted runner) |
| Pardhu | 11, 16 | about 10 | SimPy simulation; stretch goals; greedy shipped as "optimized" if CP-SAT slips |

**The honest summary:** everyone is near capacity. The plan only fits if **LLM mode and the free-form copilot are treated as optional**. The demo needs scripted agents with real tools, the scheduler,
the model, the twin, and the replan loop. Decide at C1 whether piece 12 continues.

## What this changes in the first hour (before C0 at 9:15 PM)

| Person | First deliverable | Needs |
|---|---|---|
| **Shamit** | Pieces 1-3: models, DB, mock API, fixtures, Makefile. Push and message the group | nothing |
| **Aashirth** | `app/ml/sensors.py` (sensor names) by 9:00, then the C-MAPSS loader `ml.cmapss.trajectories()` (**Aadhavan's telemetry source uses it**) | nothing |
| **Aadhavan** | Piece 9 first: `generate_fleet(seed)` writing aircraft/legs/stations to the DB tables (`CONTRACTS.md` §3) and the golden constraints; dump `fixtures/fleet.json`, `stations.json`, `legs_golden.json` | models + DB from Shamit (or copy from `CONTRACTS.md`) |
| **Pardhu** | `copilot/kb/task_cards.yaml` by 9:15; then solver L1 against fixtures; then the tool registry skeleton (piece 11) returning fixtures | fixtures from Aadhavan/Shamit |

## Handoff notes

- **Fleet generator and telemetry (Aadhavan):** the golden constraints in `work/01` §B.3 are the test: SIM-017 has a >= 6 h ground window at DFW; ATL has a hangar but no BORESCOPE-KIT on hand; DFW has kits and an engine technician on shift.
  Telemetry rows use the sensor names from Aashirth's `ml/sensors.py`. Until `ml.cmapss.trajectories()` exists, use a stand-in that fabricates a drifting `T50`.
- **Tool registry + scripted runner (Pardhu):** build against `fixtures/` and the L1 stubs first (`INTEGRATION.md` §3). Shamit's Agent Activity screen polls `GET /agents/runs/{id}`; the mock serves
  `fixtures/agent_run_golden.json` until your runner is real.
- **LLM mode + copilot backend (Aashirth):** only after the model and twin reach L2. `ChatResponse` shape is fixed in `CONTRACTS.md` §2; Shamit's drawer renders it as-is.
- **Shamit's screens** call: `/fleet`, `/plan`, `/plan/replan`, `/whatif/compare`, `/scenario/inject`, `/scenario/reset`, `/agents/run`, `/agents/runs/{id}`, `/copilot/chat`, `/workorders`, `/audit`. The mock covers all of them from minute one.
- Endpoints and function signatures in `CONTRACTS.md` did not change; only the person behind each one did.
