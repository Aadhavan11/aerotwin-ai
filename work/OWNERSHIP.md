# OWNERSHIP: who owns what (this file wins over every other doc)

**Changed Sat night:** Shamit and Aadhavan swapped some jobs. This table replaces the owners named in `SCOPE.md`, `TASK-BOARD.md`, `FEATURES-3.1-3.6.md`,
`CONTRACTS.md`, `INTEGRATION.md` and the four work packages. **The specs did not change, only who builds them.**
The spec for each piece is still in the old file and section (column "Spec"). If two docs disagree about an owner, this file is right.

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
| 9 | **Agent tools + runner** (scripted first, then LLM), prompts, number-check, cache (feature 3.4) | `work/01` §C, §D | **Aadhavan** | proposed (the jobs Shamit gave up) |
| 10 | **Copilot backend**, KB search, work orders, audit, `/copilot/chat`, `/workorders`, `/audit` (feature 3.6 backend) | `work/01` §E | **Aadhavan** | proposed |
| 11 | **Aircraft Twin screen** (clickable 2D aircraft, RUL band, sensor trend, timeline) (feature 3.2 UI) | `work/04` screen 2 | **Aadhavan** | proposed (he keeps it) |
| 12 | Demo script, backup video, Devpost text; **concept owner** | `work/04` "The story" | **Aadhavan** | proposed (he wrote the doc) |
| 13 | **Telemetry source** (C-MAPSS replay + synthetic generators), `POST /sim/step`, **scenario injection** (`POST /scenario/inject`, `/scenario/reset`) | `work/01` §B.4-B.7 | **Aashirth** | proposed (next to her C-MAPSS loader and twin ingest) |
| 14 | **Fleet generator** (`sim/fleet.py`: aircraft, rotations/legs, stations, technicians, parts, **golden constraints**), `SimClock` | `work/01` §B.1-B.3 | **Pardhu** | proposed (his scheduler needs these exact windows) |
| 15 | ML health (3.1), twin state engine (3.2 backend), `/aircraft/*`, `/metrics` ML block | `work/02` | **Aashirth** | unchanged |
| 16 | Scheduler (3.3), what-if (3.5), task cards, `/plan*`, `/whatif/*`, `/metrics` plan block | `work/03` | **Pardhu** | unchanged |

## Load check (rough hours with Claude; about 10 hours remain)

| Person | Pieces | Hours (estimate) | Note |
|---|---|---|---|
| Shamit | 1-8 | about 9 to 10 | 1.5 h foundation + 8 h of four screens + integration. **Cut line:** Lab UI becomes three buttons and a diff list; Planning board without hatching/zoom |
| Aadhavan | 9-12 | about 10 | Agents 4 h + copilot backend 2 h + twin screen 3 h + story. **Cut line:** LLM mode and copilot chat after 1:30 AM; scripted agents are the priority |
| Aashirth | 13, 15 | about 8 | Model + twin state ~6 h + telemetry/scenarios ~2 h |
| Pardhu | 14, 16 | about 8.5 | Scheduler + what-if ~7 h + fleet generator ~1.5 h |

If a person is behind at **C1 (about 1:30 AM)**, the others take their cut-line items first: Aashirth and Pardhu have the most slack after the first loop closes.

## What this changes in the first hour (before C0 at 9:15 PM)

| Person | First deliverable | Needs |
|---|---|---|
| **Shamit** | Pieces 1-3: models, DB, mock API, fixtures, Makefile. Push and message the group | nothing |
| **Aashirth** | `app/ml/sensors.py` (sensor names) by 9:00, then the C-MAPSS loader | nothing |
| **Pardhu** | `copilot/kb/task_cards.yaml` by 9:15, then the fleet generator interface `generate_fleet(seed)` writing legs/stations to the DB tables from `CONTRACTS.md` §3 | models from Shamit (or copy from `CONTRACTS.md`) |
| **Aadhavan** | Read `work/01` §C-§E and `CONTRACTS.md` §5-§6; write the **tool registry skeleton** returning fixtures, so the agent steps and the UI can run before the real modules exist | fixtures from Shamit at 9:15 |

## Handoff notes for the moved pieces

- **Agents (Aadhavan):** build against `fixtures/` and the L1 stubs first (`INTEGRATION.md` §3). Shamit's Agent Activity screen already polls `GET /agents/runs/{id}`; the mock
  serves `fixtures/agent_run_golden.json` until your runner is real, so the screen never waits on you. Scripted mode before LLM mode, always.
- **Copilot backend (Aadhavan):** `ChatResponse` shape is fixed in `CONTRACTS.md` §2; Shamit's drawer renders it as-is.
- **Telemetry and scenarios (Aashirth):** Shamit's Lab screen calls `POST /scenario/inject` and `POST /scenario/reset`; the mock handles them until yours are real.
- **Fleet generator (Pardhu):** must satisfy the golden constraints in `work/01` §B.3 (SIM-017 has a >= 6 h ground window at DFW; ATL has a hangar but no BORESCOPE-KIT on hand; DFW has kits and an engine technician).
- **Everyone:** the endpoints and function signatures in `CONTRACTS.md` did not change; only the person behind each one did.
