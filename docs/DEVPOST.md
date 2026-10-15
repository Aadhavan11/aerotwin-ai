# Devpost submission text (updated 2026-10-11 ~06:50 EDT)

Paste each section into the matching Devpost field. Numbers were re-read from a fresh run at 06:45 on Oct 11 (`make demo-check`: 16 of 16 checks, NASA C-MAPSS files present, seed 1234) and from the live demo; if you re-run and one changed, use the new one. Nothing here is a claim about real airlines.

**Before you submit (yours to decide):**
1. The repository's default branch `main` is the team's UI (the light glass UI). The version in the live demo and the video is branch **`shamit/ui`**. Either link https://github.com/Flash0529/aerotwin-ai/tree/shamit/ui in Devpost, or make `shamit/ui` the default branch in the repository settings (agree it with the team first).
2. Video link, once uploaded as public: `[ ]`.
3. The Google Form (one member), after the Devpost link exists.

---

## Project name
AeroTwin AI

## Tagline (60 characters or fewer)
Predict the failure. Plan the fix. A person approves it.
(Alternative: "A digital twin that plans aircraft maintenance for you")

## Elevator pitch (about 200 characters)
AeroTwin AI estimates remaining life for simulated aircraft components, finds the maintenance slot that fits flights, parts, technicians and hangars, and drafts work orders. A person approves every one.

## Scope and honesty
All aircraft data in this project is simulated. No airline, manufacturer or operator data was used, part numbers and life limits are fictional, and nothing here is for airworthiness, dispatch or maintenance decisions (see `docs/LIMITATIONS.md`).

## Main track
AI/ML: agents, assistants and data pipelines that take real tasks off someone's plate. No side challenges.

## The problem and who it serves
Aircraft maintenance is a coordination problem. A component starts to degrade, but the aircraft, the hangar, the spare part and the qualified technician are in different cities, and the aircraft still has to fly its schedule. Planners work from fixed schedules and several separate systems, and when something changes (a delayed part, a closed hangar, a new fault) they re-plan by hand.

AeroTwin AI serves the people who make those decisions: maintenance control and operations planners first, and the engineers, technicians, pilots, dispatchers and administrators who depend on the plan. A person stays in control: every maintenance request waits for human approval.

## What it does
- **Estimates (on simulated data).** A health model estimates each component's remaining life with uncertainty (a conservative p10, a median p50, an optimistic p90) and flags abnormal sensor behavior. The aircraft twin shows the whole aircraft, then flies the camera in on the engine in trouble, marks the section the sensors point to, and lists every sensor against its healthy baseline.
- **Simulates.** A digital twin of 24 simulated aircraft (16 A320-class, 8 B737-class, 8 stations, 144 monitored components, 480 scheduled legs). A scenario lab injects a fault, delays a part, closes a hangar or removes a technician.
- **Plans.** A constraint solver (OR-Tools CP-SAT) finds the slot that fits flights, ground windows, parts, technicians and hangar space; the conservative p10 deadline is a hard constraint. The plan is shown as an hourly day calendar, one column per aircraft.
- **Re-plans.** When the world changes the plan is recomputed in a fraction of a second and the change is explained.
- **One maintenance request, whole.** What is damaged or inspected, the work, the part and whether it must be ordered, when and where, why it is needed, and an estimated cost, in one card. People who may decide can edit the name, work, part, ordering or time (the server checks the deadline, the aircraft's ground time and the technician's shift) and then approve or deny. Requests can also be entered by hand from the calendar. Every edit and decision is in the audit trail.
- **An AI built into the work.** An ask bar on every page and ask fields where questions come up; the copilot sees the screen (page, aircraft, selection), so "what is the tail number?" or "what is wrong with this aircraft?" is answered from it. Five agents (coordinator, health, logistics, operations, scheduling) call deterministic tools and draft requests. The copilot can also propose editing or creating a request in plain words; it shows the exact change with Allow and Deny and changes nothing until a person allows it.
- **Suggested actions.** Work that is not required yet but is better planned ahead (components trending toward their limit, routine checks falling due, a shared hangar visit), each with a ground window that fits.
- **Positions and sign-in.** Operations control, maintenance planner, pilot / flight crew, crew and dispatch, administrator, and a master account that can view the app as any of them. Positions are enforced on the server (only operations and maintenance approve; only operations and administrators drive the simulation). Messages between positions with unread tracking and a notification bell. Works on a tablet.
- **Honest labels.** Everything is simulated and says so. Duty and rest, weather, defect reporting and the crew roster are labeled previews with sample data.

## Results from the golden scenario (simulated; `make demo-check`)
A late-life engine fault on SIM-017 (engine 2) is injected into the fleet.
- Remaining life estimate: **p10 20.2 / p50 31.4 / p90 57.8 flights.**
- The optimized plan fixes it at DFW **42 h** from now, after **6** flights with the fault open, with **73.6 h** of margin before the hard deadline.
- A static baseline (service at the home base in the last window before the deadline) fixes it after **86 h**, **14** flights, **29.6 h** of margin.
- A 36-hour delay of the borescope kit at DFW re-plans in under a second (0.15 s in the last run) and moves the job to a feasible slot.
- Estimated cost (a simulated price list: parts, labor, bay time, a per-job overhead, shipping, delay, and an allowance for any job not done in time): the two plans tie at $27,210 in this scenario because both do all four jobs, so the difference there is risk. In the live demo's seeded scenario the optimized plan is an estimated $15,670 against $40,290 for the baseline, which cannot fit one of three jobs before its deadline (these figures change if the demo is reset).

Health model (NASA C-MAPSS FD001, 100 held-out test engines that no model was fitted or calibrated on): remaining-life error MAE 10.84 and RMSE 14.39 cycles on the last window, 3.8 cycles late in life (true RUL <= 30); the p10-p90 interval covers 92.9% of cases (nominal 80%); anomaly detector precision 0.79, recall 0.72, false-positive rate 0.6%.

## How we built it
The idea came from Aadhavan's concept document ("the self-optimizing aircraft fleet"). With 36 hours and four people we worked contracts first: Shamit wrote the shared data models, API contract, database and web shell on day one, with mock data for every endpoint, so everyone could build against the same shapes. Each person then built a module against that contract and the real modules replaced the mocks one group at a time: the health model and twin (Aashirth), the scheduler, what-if engine and agent tools (Pardhu), the simulator (Aadhavan), and the platform, front end, sign-in, calendar and integration (Shamit). `GET /health` shows which module groups are real, so a broken module is visible instead of silently mocked. A golden-path script walks the whole story end to end, a mutation check deliberately breaks scheduling rules to confirm the tests catch them, and more than 340 automated tests pass. A headless-browser pass clicked every page and button of every position, on a laptop and at three iPad sizes.

No language model decides anything on its own: numbers come from the health model, the solver and the tools. When a language model writes an answer, every figure in it is checked against what the tools returned, and a request change is only ever proposed, never applied, until a person allows it.

## Challenges
- Four people building in parallel: one written contract (`CONTRACTS.md`) and one ownership file kept the branches fitting together.
- Honest data: aircraft sensor data is not public, so engine degradation uses NASA C-MAPSS and everything else is simulated and labeled.
- A meaningful baseline: with one fault the cost metrics tie, so we show risk too (hours until fixed, flights flown with the fault open, margin) and added a simulated cost model with a documented price list.
- Keeping edits safe: a person's change to a request's time is checked against the same rules the planner keeps, on the server, and refused with the reason.

## Accomplishments we are proud of
A closed loop that runs end to end on real modules: fault, remaining-life estimate, plan, a request a person can read, edit and approve, audit, a part delay, a re-plan. A copilot that can propose changes but cannot make them. More than 340 automated tests, an independent constraint checker, and a golden path that passes 16 of 16 checks.

## What we learned
Safety-critical advice needs hard constraints, explicit uncertainty and a human in the loop. A language model is best used to choose tools, phrase answers and prepare changes, not to produce numbers or to act alone.

## What's next
Real airline data connectors (maintenance records, parts, rosters, weather), validation on airline sensor data, real crew and duty data behind the crew and dispatch previews, pilot defect reporting that reaches maintenance control, and drag-to-reschedule on the calendar.

## Built with
Python, FastAPI, pydantic, scikit-learn (random forest, isolation forest), OR-Tools CP-SAT, SimPy, SQLite, React, TypeScript, Vite, Tailwind CSS, Recharts, three.js, Geist (font), the Anthropic API (Claude) for the language-model mode of the copilot and agents.

## Data and APIs used
NASA C-MAPSS turbofan degradation data (FD001; A. Saxena and K. Goebel, "Turbofan Engine Degradation Simulation Data Set", NASA Ames Prognostics Data Repository, 2008). It is public; we download a public mirror at setup time and do not commit it. Everything else (fleet, flights, stations, parts, technicians, task cards, prices) is simulated or written by us during the event. 3D aircraft model: "B737-800 Model" by hikami3150 (Sketchfab, CC BY 4.0), credited in the app and in `docs/ASSETS.md`.

## Disclosure: what existed before Hacklanta
- The AeroTwin AI concept document (Aadhavan). A separate planning document for a different idea was removed from the repo before submission (it stays in git history): planning text only.
- Public data and open-source libraries listed above, and the one third-party asset above, downloaded during the event.
- No application code existed before the event; the repository was created after hacking opened.

## Instructions for judges
**Live demo, no install:** https://shamitbasu.com/hacklanta. It is a shared instance on simulated data: every visitor shares one simulation (reset it under the Administrator position if it looks different).

**Sign in:** the sign-in screen lists five demo accounts, one per position (`operations`, `maintenance`, `crew`, `dispatch`, `administrator`); one click signs you in and they share the password `aerotwin-demo`. Or create an account and choose your position. Start with *Operations control*: Fleet overview, then SIM-017's Aircraft twin (the camera flies in on the engine), then Maintenance plan (the calendar; click a job for its request, then edit, approve or deny), then Copilot (ask a question; try "rename WO-1 to Borescope check" and Allow it; run the agents). Pages marked Preview use sample data.

**Run it yourself:** see the README (about 5 minutes: `make setup`, `make data`, `make reset`, `make api`, `make web`, open http://localhost:5173; no sign-in locally). The system is advisory only and does not certify an aircraft or execute maintenance.

## Links
- Live demo: https://shamitbasu.com/hacklanta
- Code: https://github.com/Flash0529/aerotwin-ai (branch `shamit/ui` is the version in the demo and the video)
- Video (2-3 minutes, public): `[ ]`

## Image gallery
Screenshots are in `/home/pi/hackatlanta/presentation/screenshots/` (`devpost-1-fleet.png`, `devpost-2-fault-closeup.png`, `devpost-3-calendar.png`, `devpost-4-maintenance-request.png`, `devpost-5-optimized-vs-baseline.png`, `devpost-6-copilot-proposal.png`). Thumbnail: the fault close-up (`devpost-2`) or the calendar (`devpost-3`).

## Team (max 4)
Shamit Basu, Aashirth, Pardhu, Aadhavan.
