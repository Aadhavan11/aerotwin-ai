# Frontend plan: AeroTwin AI interface and demo-video mode

Status: **partly built** on branch `aashirth/frontend-rework`. Built: the visual system (section 3), the shell, Fleet, Aircraft twin (a type-specific 3D model, parts list and engine sensor section instead of the 2D schematic planned in section 4), Planning, Scenarios and the copilot drawer, against the live API. **Not built:** the recording modes in section 5 (`?stage`, `?director`, `?replay`) and the Impact frame. Where this file and the code disagree, the code is current. Extends `work/04-AADHAVAN-frontend-and-story.md` on look and feel only; screens, endpoints and data shapes still come from `CONTRACTS.md`, and who builds what comes from `work/OWNERSHIP.md`.

**Who this is for.** It is a shared design system and recording-mode spec so the screens built by different people look like one product.

| Part of this plan | Builder (per `work/OWNERSHIP.md`) |
|---|---|
| Sections 3 and 6: tokens, shell, Fleet, Planning, Lab, Copilot drawer | Shamit (pieces 4 to 7) |
| Aircraft Twin screen (section 4) | Aadhavan (piece 11) |
| Section 5: stage, director and replay modes, Impact frame, the video | Aadhavan (piece 12), proposed, on top of Shamit's shell |
| Section 2 checklist | Everyone, before recording |

## 1. Design read

An internal maintenance-control tool for an airline operations team, shown to judges through a screen recording. It should look like software that an engineering team built for people who do this job all day: dense, quiet, exact. It should not look like a landing page, a SaaS template, or a generated dashboard.

| Dial | Value | Why |
|---|---|---|
| Design variance | 4 | Left-aligned, ruled, predictable grid. Instruments are not supposed to be surprising. |
| Motion | 3 | Motion only where it explains a change (a replan moving bars). Nothing loops. |
| Density | 7 | Real operations data. Hairline rules instead of card boxes, mono for every number. |

## 2. What "not AI looking" means here (the rules)

These are the tells I am designing against. Run this list on every screen before recording.

| Tell | What we do instead |
|---|---|
| Purple or blue-violet gradients, glow, glass blur | Flat fills. One steel-blue accent. No shadows, no blur. |
| Row of identical rounded KPI cards with icon blobs | One ruled strip: label above, mono figure below, hairline between cells. |
| Sparkle icon, "AI-powered" badges, "Ask AI" | The assistant is called Copilot. Agents are labelled by role code (HEALTH, SCHED, LOGI, OPS, COORD) in mono. No sparkles anywhere. |
| Inter on slate, big friendly headings | IBM Plex Sans (UI) and IBM Plex Mono (figures, IDs, times). Small, tight heading scale. |
| Pure black and pure white | Ink `#17191C` on a cool off-white ground. |
| Decorative status dots, pills everywhere | Status is a square marker plus a word. Shape differs by state so colour is never the only signal. |
| Em-dashes and cute copy | Plain operational wording. Hyphens only. Sentence case. Domain terms (tail, RUL, ground window, cycles) used as-is. |
| Round, perfect numbers (99%, 50%) | Every figure comes from fixtures captured from a real run. Nothing typed by hand. |
| Emoji, illustrations, stock avatars | None. Technicians are IDs (DFW-T2). |
| Everything is a card | Panels separated by 1px rules and whitespace. Cards only when something actually floats (the drawer). |
| Mixed radii | One radius, 2px, everywhere. |
| Empty "welcome back" header, onboarding tour | App opens straight into the fleet. |
| Loading spinners | Skeleton rows in the final table shape. |
| Fake-looking telemetry (smooth sine waves) | Real C-MAPSS or the synthetic degradation series from the twin API, drawn with its noise. |

## 3. Visual system

All values live as CSS variables in `web/src/tokens.css`. Light theme only for the demo. Tokens are semantic, so a dark set can be added later without touching components.

### Colour

| Token | Value | Use |
|---|---|---|
| `--ground` | `#F2F3F1` | Page |
| `--panel` | `#FAFAF8` | Drawer, popovers only |
| `--ink` | `#17191C` | Primary text, primary buttons |
| `--ink-2` | `#4A4F57` | Secondary text |
| `--ink-3` | `#646A73` | Labels, axes (checked >= 4.5:1 on ground) |
| `--rule` | `#D6D9D4` | 1px dividers |
| `--rule-strong` | `#B9BEB7` | Table header underline, focus-adjacent |
| `--accent` | `#2F5D8A` | Selection, links, "in maintenance", the optimized plan |
| `--ok` | `#3E7B57` | Status ok |
| `--watch` | `#A8680F` | Status watch |
| `--alert` | `#B3261E` | Status alert, infeasible |

Baseline plan is drawn in `--ink-3` with hatching. Optimized plan is `--accent`. That pairing carries the whole comparison story, so nothing else is allowed to use `--accent` on the planning screen.

### Type

IBM Plex Sans 400/500/600 and IBM Plex Mono 400/500, bundled from npm (`@fontsource`), so the demo works offline. Scale: 11, 12, 13, 14 (base), 16, 20, 28. Tabular numerals on. No text above 28px. Column headers are 11px, 500, sentence case, `--ink-3`; no tracked uppercase labels except the single SIMULATED tag.

### Shape and spacing

4px spacing unit. Radius 2px. Borders 1px. Row height 32px in tables (28px in the Gantt). No shadows, except the drawer, which gets a 1px left rule.

### Icons

`@phosphor-icons/react`, regular weight, 16px, one family. Used sparingly: nav-free, mostly in buttons and the agent feed.

## 4. Screens

Chrome on every screen: a 48px top bar. Left: `AeroTwin` wordmark in plain text. Centre-left: Fleet, Aircraft, Planning, Lab (text tabs, underline for current). Right: simulated time in UTC (mono), the `SIMULATED` tag (bordered, never hidden, visible in every frame), and a `Copilot` button.

### Fleet (`/`)

```
KPI strip: aircraft | needing attention | maintenance due 72 h | in maintenance | readiness
-------------------------------------------------------------------------------------
US map (stations, aircraft)            | Aircraft, sorted by risk
real state outlines, no tiles          | tail  type  loc  status  worst component  RUL p10
8 station labels, aircraft as ticks    | click row -> /aircraft/:tail
```

Map uses `us-atlas` (`states-albers-10m.json`, public domain, bundled) with the same `geoAlbersUsa().scale(1300).translate([487.5,305])` projection for stations. This replaces the bounding-box idea in the work package: real outlines read as professional and still work offline.

### Aircraft twin (`/aircraft/:tail`)

```
SIM-017  A320-class  ATL  status
-------------------------------------------------------------
top-view schematic        | engine_2                 status
(5 clickable zones)       | health 0.18  anomaly 0.91  quality ok
                          | RUL band chart  p10 / p50 / p90
                          | sensor trend, anomaly span shaded
                          | top signals, history, last maintenance
-------------------------------------------------------------
timeline (newest first): flights, anomalies, injected faults, plan changes
```

The schematic is the one custom drawing in the app: a single-weight technical outline, zones filled by status. Budget 45 minutes. Fallback if it is not good by then: a labelled component diagram in aircraft layout built from plain rectangles.

Charts (Recharts) are drawn bare: hairline gridlines, no gradients, no rounded bars, direct labels instead of legends, mono axis ticks.

### Planning (`/planning`)

```
Metrics strip: downtime | flights affected | logistics cost | tech utilization | tasks | solve time
[ Optimized | Baseline | Both ]                                          plan P-2, cpsat
-------------------------------------------------------------------------------------
Gantt, next 5 days.  rows = aircraft.  legs grey, ground windows light,
maintenance accent, baseline hatched, vertical "now" line (sim time)
-------------------------------------------------------------------------------------
Selected assignment: station, technician, window, extension, reason, evidence
```

Gantt is custom SVG or CSS grid (Recharts cannot do it). Infeasible plan: a full-width alert band listing `infeasible_reasons`, per the contract. On replan, moved bars translate to their new position (300 ms ease-out, transform only), added bars fade in, dropped bars strike through and fade. That is the one place motion carries information.

### Simulation lab (`/lab`)

```
Scenarios (ruled list)                    | Result
Inject engine anomaly, SIM-017    [Run]   | before / after metrics, paired bars
Delay BORESCOPE-KIT at DFW, 12 h  [Run]   | diff table: moved / added / dropped, with why
Close ATL hangar                  [Run]   | solve seconds
Technician unavailable            [Run]   | compare table (POST /whatif/compare)
Reset to golden                           |
```

### Copilot drawer (any screen, 420px, right)

Three tabs: Activity, Chat, Work orders.

- Activity: agent steps as they arrive, one line each: role code, tool name, one-line summary. Running step has a thin indeterminate rule, not a spinner.
- Chat: six quick prompts from the concept doc as a ruled list. Answers show evidence as inline references. `missing_info` renders as a plain note.
- Work orders: `pending_approval` rows with Approve and Reject. Approving asks for name and note, then the audit list updates in place.

### Impact (`/impact`, optional, build last)

One uncluttered frame for the end of the video: baseline against optimized on downtime, flights affected, logistics cost, utilization. Baseline definition and the word SIMULATED stated on the frame. All figures from `/metrics` and `/plan`.

## 5. Recording mode (the part that makes this usable for the video)

### Stage mode: `?stage=1`

- Base type 16px instead of 14px so text survives video compression.
- Fixed regions with internal scroll hidden; no layout that depends on a scrollbar.
- Designed and checked at 1440x900 and 1920x1080. No information that exists only in a hover tooltip.
- Reduced motion respected; no looping animation exists to turn off.

### Director strip: `?director=1`

A 28px strip at the very bottom, outside the 16:9 capture region when cropped. Right-arrow advances one step, left-arrow goes back, `R` resets. Each step performs the real action and navigates, so a take is repeatable and nobody mis-clicks on camera.

| Step | Action | Source in SCOPE.md section 9 |
|---|---|---|
| 1 | Fleet view, hold | Fleet |
| 2 | Inject anomaly on SIM-017 engine_2 | Anomaly |
| 3 | Open SIM-017, RUL band visible | Anomaly |
| 4 | Run agents, feed streams | Agents |
| 5 | Planning, select the engine inspection, show reason | Plan |
| 6 | Delay BORESCOPE-KIT at DFW by 12 h | Disruption |
| 7 | Replan result, diff on screen | Disruption |
| 8 | Impact frame, then approve the work order | Impact |

### Capture and replay: `?replay=1`

`scripts/capture.ts` runs the golden path against the real backend once and saves every response to `web/replay/*.json`. Replay mode serves those files in the same order. This keeps a recording deterministic and offline while every number still comes from a real run (rule from `work/04`: results are never pre-written). Capture must be re-run after any backend change that touches the demo numbers.

## 6. Technical plan

Stack: Vite, React, TypeScript, Tailwind v4 (Vite plugin), Recharts, react-router, Phosphor icons, `d3-geo`, `topojson-client`, `us-atlas`. All installed in `web/`.

```
web/src/
  tokens.css            CSS variables, base styles, font imports
  api/client.ts         getJSON / postJSON; fixtures, live, or replay chosen by env + query
  api/types.ts          hand-written from CONTRACTS.md section 2, replaced by openapi-typescript later
  api/usePoll.ts        polling, paused when the tab is hidden, updates in place
  app/Shell.tsx         top bar, routes, drawer host
  ui/                   Tag, Stat, Rule, Table, Skeleton, Button (nothing else)
  features/fleet/       Kpis, StationMap, AircraftTable
  features/twin/        AircraftSchematic, ComponentPanel, RulBand, SensorTrend, Timeline
  features/planning/    Gantt, MetricsStrip, AssignmentPanel
  features/lab/         ScenarioList, DiffTable
  features/copilot/     Drawer, ActivityFeed, Chat, WorkOrders
  features/director/    steps.ts, Strip.tsx
```

Env, in `web/.env` (git-ignored by the repo rule): `VITE_API=http://localhost:8000`, `VITE_USE_FIXTURES=1`.

Rules carried over from `work/04`: sim time comes from `GET /health`, never `Date.now()`; nothing hard-coded; every list has loading, empty and error states; on a failed call show the message and keep the last good data. SIMULATED stays visible in every frame.

## 7. Build order

Clock times are ET and follow `SCOPE.md`. Freeze is 3:15 AM.

| When | Build | Owner | Done when |
|---|---|---|---|
| 9:05 to 9:45 PM | Tokens, shell, router, api client, hand-written types, SIMULATED tag, sim clock | Shamit | `npm run dev` shows the shell on fixtures |
| 9:45 to 10:45 | Fleet: KPI strip, station map, aircraft table | Shamit | Click-through to an aircraft route |
| 10:45 to 12:30 AM | Twin: schematic, component panel, RUL band, sensor trend, timeline | Aadhavan | SIM-017 fixture renders fully |
| 12:30 to 1:30 | Planning: Gantt, metrics, baseline toggle, assignment panel | Shamit | Baseline and optimized both draw |
| 1:30 to 2:15 | Lab, and director steps 1 to 8 wired to the same calls | Shamit (Lab), Aadhavan (director) | One full golden path on keyboard |
| 2:15 to 3:15 | Copilot drawer: activity, chat, work orders, audit | Shamit | Approve writes to audit |
| 3:15 AM | Freeze | | Only fixes from here |
| 3:15 to 4:30 | Impact frame, slop audit (section 2), contrast pass, 1080p check | Aadhavan, everyone reviews | Checklist clean |
| 4:30 to 5:30 | Capture real run, rehearse three takes, record | Aadhavan | Backup video exists |

Time boxes are `work/04`'s, split by the ownership table. Shamit's load is already about 9 to 10 hours (`work/OWNERSHIP.md`), so the cut lines below matter.

Cut lines (in this order): Impact route, compare table, schematic detail, map hover. Never cut: Fleet, Twin, Planning, Lab buttons, agent feed, work-order approval, SIMULATED tag.

## 8. Deviations from `work/04` (decide before building)

| Work package says | This plan does | Reason |
|---|---|---|
| System font stack, no web fonts | Bundled IBM Plex via npm | Still fully offline. System stack on a Mac is San Francisco, which reads as default. Easy to revert. |
| Bounding box or simple outline for the map | Real state outlines from `us-atlas`, bundled | Looks professional, still offline, about 100 KB. |
| `npm install` with `openapi-typescript` as dev dependency | Not pinned; run with `npx` when the API is up | It declares TypeScript 5 only and the scaffold uses 6; it is a one-off CLI. |
| Planning, Lab, Copilot only | Adds `?stage`, `?director`, `?replay` modes and an optional Impact frame | Needed to make a clean, repeatable video. |

## 9. Risks

- **One `web/`, two builders.** Shamit builds the shell and four screens, Aadhavan builds the Twin screen and the video modes. Shamit must push the shell early (target 9:45 PM) so Aadhavan builds inside it. Nobody should create a second app scaffold. A throwaway scaffold exists on branch `aashirth/web-shell` (local only) purely as a reference for the dependency list; Shamit's shell wins.
- **Shamit is overloaded.** Four screens plus integration. If tokens and the Fleet screen are late, the visual rules in section 2 are the first thing that gets dropped. The tokens file is small enough to hand over as-is.
- **Backend lateness.** Real `/aircraft`, `/plan` and `/whatif` arrive around 12:15 AM. Fixtures carry the UI until then; if a field is missing, tell the owner with the exact JSON (rule from `work/04`).
- **Schematic quality.** A hand-drawn aircraft can look amateur. Time-boxed, with a fallback (section 4).
- **Capture drift.** Replay files go stale if the backend changes. Re-capture after the freeze.
