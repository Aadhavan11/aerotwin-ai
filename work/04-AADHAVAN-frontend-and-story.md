# Work package 4: Aadhavan. The interface (3.2 UI, 3.5 lab, 3.6 copilot UI) and the story

> **This file is the spec for the screens and the story. Under the new split Aadhavan builds screen 2 (Aircraft Twin) and the story, plus the agents and copilot backend specified in `work/01` sections C, D, E. Screens 1, 3, 4, 5 and the app shell are now Shamit's.**
> Details: [`OWNERSHIP.md`](OWNERSHIP.md).

You build **everything the judges see**. The backend is being built at the same time, so you start from fixtures and the mock API and swap to the
real one without changing your code. Your demo is the product.

## What you own

```
web/                         the React app (Vite + TypeScript + Tailwind + Recharts)
docs/DEMO-SCRIPT.md          the 3-minute script with timings
docs/DEVPOST.md              the submission text
(video)                      the backup screen recording
```

Read: `FEATURES-3.1-3.6.md` (3.2 UI, 3.5 UI, 3.6 UI), `CONTRACTS.md` sections 2 (models), 4 (endpoints + examples), `INTEGRATION.md` sections 3, 4.2, 4.3.

## Setup (30 min, start now)

```
npm create vite@latest web -- --template react-ts
cd web && npm install tailwindcss @tailwindcss/vite recharts react-router-dom
npm install -D openapi-typescript
```

- `.env` in `web/`: `VITE_API=http://localhost:8000`, `VITE_USE_FIXTURES=1` (**start with 1**).
- `src/api/client.ts`: `getJSON(path)`; if `VITE_USE_FIXTURES=1`, read from `../../fixtures/*.json` (Vite can import JSON from outside `web/` if you add the folder to `server.fs.allow`).
  Later flip to `0` to hit the backend. `usePoll(path, ms)` hook for live data.
- Types: once the API is up, `npx openapi-typescript http://localhost:8000/openapi.json -o src/api/types.ts`. Until then, write the TypeScript types by hand from `CONTRACTS.md` section 2.
- **No external CDNs, map tiles or web fonts**: the demo must work offline. Use the system font stack. Keep the style clean and light; avoid heavy blur or glow effects.
- A permanent **SIMULATED** badge in the header. Show simulation time (UTC) from `GET /health`.

## Screens, in build order (time boxes assume you use Claude to generate components from the types)

### 1. App shell + Fleet Overview (9:15 to 10:30 PM)
- Layout with nav: Fleet, Aircraft, Planning, Lab; a Copilot drawer button; the SIMULATED badge.
- KPI tiles from `/fleet.kpis`: aircraft count, needing attention, predicted maintenance events (72 h), in maintenance, readiness %.
- **Map without tiles:** a simple SVG of the contiguous US (a bounding box or a simplified outline) and project `lon/lat` of each station with an equirectangular transform
  (x = (lon + 125) / 60 * width; y = (50 - lat) / 26 * height). Dots for stations; aircraft markers by `location`; color by `status` (ok green, watch amber, alert red, in_maintenance blue).
- Aircraft table sorted by risk (status, then lowest `rul_p10`); click a row -> Aircraft page.

### 2. Aircraft Twin (10:30 PM to 12:15 AM) (feature 3.2)
- Route `/aircraft/:tail` loads `TwinView`.
- **Top-view 2D SVG aircraft** with clickable zones: engine 1, engine 2, landing gear, hydraulics (center), avionics (nose). Fill = health color; pulse or outline when
  `status` is `alert`. Selected component highlighted.
- Detail panel for the selected component: health gauge (0 to 100%), **RUL band** (Recharts `AreaChart`: p10, p50, p90 as a band over cycles), **sensor trend**
  (`LineChart` of the top 3 sensors from `sensors_last`/telemetry endpoint, anomaly region shaded), cycles/hours, last maintenance, data-quality badge with reasons,
  `top_signals` as chips, history table, predicted health curve with its band.
- Timeline list for the aircraft (`timeline[]`): flights, anomalies, injected faults, maintenance, plan changes, newest first.
- Stretch only: 3D (react-three-fiber) after everything else works.

### 3. Planning board (12:15 to 1:30 AM) (feature 3.3)
- Gantt: one row per aircraft that has tasks (plus a couple without); time axis = the next 5 days; bars: legs (grey), ground windows (light), maintenance assignments (colored by
  station), baseline assignments (hatched) when "Baseline" is toggled; vertical line for "now".
- Click an assignment -> side panel with station, technician, window size, extension, `reason`, and evidence chips.
- Metric cards: downtime, flights affected, logistics cost, utilization, tasks scheduled / total, solve time; **Baseline vs Optimized** toggle and a small bar chart.
- If `feasible=false`: red banner with `infeasible_reasons`.

### 4. Simulation Lab (1:30 to 2:15 AM) (feature 3.5)
- Buttons that POST events: **Inject engine anomaly on SIM-017**, **Delay BORESCOPE-KIT at DFW by 12 h**, **Close ATL hangar**, **Technician unavailable**. Payload examples are in `CONTRACTS.md` section 4.
- Result view: before/after metrics (bar chart), the **diff** (moved/added/dropped with `why`), solve seconds; a **scenario compare table** from `POST /whatif/compare`.
- A "Reset to golden" button (`POST /scenario/reset`).

### 5. Copilot drawer + agent activity + approval (2:15 to 3:15 AM) (features 3.4 and 3.6)
- Agent Activity panel: start with `POST /agents/run`, poll `GET /agents/runs/{id}` every second; list `steps` with an icon per agent (Health, Scheduling, Logistics, Operations,
  Coordinator), tool name, a short summary of input/output, and a spinner while `status=running`.
- Copilot chat: message list, input, the **six quick prompts** from the AeroTwin doc as buttons, `answer` text, **evidence chips** (click opens the plan/component/KB item),
  `missing_info` shown as a yellow note.
- Work-order cards (`pending_approval`) with **Approve / Reject** (`POST /workorders/{id}/decision`, ask for a name and a note). Audit list from `GET /audit`.
- Loading, empty and error states everywhere (a failed call shows the message and the last good data).

## The story (start at 1:30 AM in parallel; this is your second job)

- `docs/DEMO-SCRIPT.md`: the 3-minute flow from `SCOPE.md` section 9, with exact clicks and timings: fleet -> inject anomaly -> twin shows RUL -> run agents -> plan with the reason -> delay the
  part -> replan and diff -> baseline vs optimized. Rehearse 3 times with a timer.
- **Numbers on screen and in the video come from the real run** (`make demo-check` output). Write them into the slide from that output; never pre-write results.
- Record a **backup screen video** (OBS or the OS recorder) of a clean run by 5:00 AM.
- `docs/DEVPOST.md`: problem, what it does, how it works (the six features), what is simulated vs real (C-MAPSS, synthetic fleet), honest limits, what is next. Use `SCOPE.md` section 10 for the limits.
- README top section: how to run (`INTEGRATION.md` section 10), the architecture picture, the team (first names), and an AI-assistance disclosure if the event requires it.

## What you need from others

| From | What | When |
|---|---|---|
| Shamit | `fixtures/*.json`, mock API (`MOCK=1`), real `/fleet`, `/agents`, `/copilot`, `/workorders` | 9:15 PM / as built |
| Aashirth | `TwinView` shape, real `/aircraft/{tail}` | fixture now, real by 12:15 AM |
| Pardhu | `plan_*.json` shapes, real `/plan`, `/plan/replan`, `/whatif/compare` | fixture now, real by 12:15 AM |

If a backend piece is late, stay on its fixture; the screen should look the same. Tell the owner within 10 minutes if a field is missing or wrong (post the endpoint and the exact JSON you got).

## Cut lines

- 1:30 AM and a screen is not done: drop 3D, the scenario compare table, and chart polish; keep Fleet, Twin, Planning, Lab buttons, Agent Activity and Work-order approval.
- 3:15 AM: freeze. From then on only fixes, the demo script and the video.

## Pitfalls

- No `datetime.now()` for "now": use the sim time from the API. Display UTC with a label, or convert consistently.
- Don't hard-code data: everything from the API/fixtures, or the live replan won't show.
- Avoid layout shift while polling; update in place. Debounce the polling when a tab is hidden.
- Keep colors accessible and the SIMULATED badge visible in every screenshot and in the video.

## Prompt addendum for your Claude

"My role: front end + story per work/04-AADHAVAN-frontend-and-story.md. Build a Vite + React + TypeScript + Tailwind + Recharts app that works offline against fixtures first and the FastAPI backend later
(VITE_USE_FIXTURES switch). Screens in order: Fleet Overview (tile-less SVG US map), Aircraft Twin (clickable 2D SVG aircraft, RUL band chart), Planning Gantt with baseline vs optimized,
Simulation Lab, Copilot drawer with agent activity and work-order approval. Use the TypeScript types from the OpenAPI schema when available. Always show a SIMULATED badge."

## Definition of done

- [ ] App runs on fixtures (`VITE_USE_FIXTURES=1`) and on the real API with no code change
- [ ] Five screens working; click-through of the golden path with the real backend
- [ ] Agent steps appear live; work order approval writes to the audit list
- [ ] `DEMO-SCRIPT.md` rehearsed 3x; backup video recorded; Devpost text and README done
