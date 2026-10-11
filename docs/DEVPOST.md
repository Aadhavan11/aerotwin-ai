# Devpost submission text: DRAFT (Aadhavan owns the story: edit freely)

Paste each section into the matching Devpost field. Every number below came from a run on Sat/Sun 2026-10-10/11 (seed 1234, NASA C-MAPSS files present).
**Re-run `make demo-check` and re-read the numbers before submitting**; if one changed, use the new one. Nothing here is a claim about real airlines.

Links to fill in at the end: **video** `[ ]`, **repo** https://github.com/Flash0529/aerotwin-ai

---

## Project title
AeroTwin AI

## Tagline
Predict the failure. Simulate the future. Prevent the disruption.

## Main track
AI/ML (agents, assistants and data pipelines that take real tasks off someone's plate). No side challenges.

## The problem and who it serves
Aircraft maintenance is a coordination problem. A component starts to degrade, but the aircraft, the hangar, the spare part and the qualified technician are in different cities, and the aircraft still has to fly its schedule. Planners often work from fixed schedules and several separate systems, and when something changes (a delayed part, a closed hangar, a new fault) they re-plan by hand.

AeroTwin AI serves the people who make those decisions: maintenance control and operations planners first, and the engineers, technicians and flight-operations staff who depend on the plan. It keeps a person in control: every work order waits for human approval.

## What it does
- **Predicts.** A health model estimates each component's remaining life with uncertainty (a conservative p10, a median p50 and an optimistic p90) and flags abnormal sensor behavior.
- **Simulates.** A digital twin of 24 simulated aircraft (16 A320-class, 8 B737-class, 8 stations, 144 monitored components, 480 scheduled legs) replays flights and sensor data. A scenario lab injects a fault, delays a part, closes a hangar or removes a technician.
- **Plans.** A constraint solver (OR-Tools CP-SAT) finds the maintenance slot that fits flights, ground windows, parts, technicians and hangar space, with the conservative p10 deadline as a hard constraint.
- **Re-plans.** When the world changes, the plan is recomputed in a fraction of a second and the change is explained.
- **Coordinates.** Five scripted agents (coordinator, health, logistics, operations, scheduling) call deterministic tools and draft work orders. A copilot answers questions using only numbers returned by those tools.
- **Fits the job.** One console, five role views (operations control, maintenance planner, pilot, crew and dispatch, administrator): each lands on what that person needs, such as decisions waiting, work orders with technicians and parts, a plain-language readiness brief for one aircraft, departures against maintenance windows, or service health and the audit trail. Only operations and maintenance can approve.
- **Keeps a person in charge.** Work orders are created "pending approval"; only a named person approves or rejects, and every step is written to an audit log.

## Results from our golden scenario (simulated)
A late-life engine fault on SIM-017 (engine 2) is injected into the fleet.
- Remaining life estimate: **p10 20.2 / p50 31.4 / p90 57.8 flights.**
- The optimized plan fixes it at DFW **42 h** from now, after **6** flights flown with the fault open, leaving **73.6 h** of margin before the hard deadline.
- A static baseline (service at the home base in the last window before the deadline) fixes it after **86 h**, **14** flights, **29.6 h** of margin.
- A 36-hour delay of the borescope kit at DFW re-plans in **0.03 s** and moves the job to a feasible slot.
- Cost metrics (downtime, flights delayed, dollars) tie at zero for both plans in this scenario; the difference is risk, so the app shows hours until fixed, flights flown with the fault open and margin side by side.

Health model (NASA C-MAPSS FD001, 100 held-out test engines that no model was fitted or calibrated on): remaining-life error MAE 10.84 and RMSE 14.39 cycles on the last window, 3.8 cycles late in life (true RUL <= 30); the p10-p90 interval covers 92.9% of cases (nominal 80%); anomaly detector precision 0.79, recall 0.72, false-positive rate 0.6%.

## How we built it (ideation and development process)
The idea came from Aadhavan's concept document ("the self-optimizing aircraft fleet"). We had a 36-hour window and four people, so we worked contracts first: Shamit wrote the shared data models, API contract, database and web shell on day one, with mock data for every endpoint, so everyone could build against the same shapes. Then each person built a module against that contract, and the real modules replaced the mocks one group at a time: the health model and twin (Aashirth), the scheduler, what-if engine and agent tools (Pardhu), the simulator and the aircraft twin screen (Aadhavan), and the platform, front end and integration (Shamit). `GET /health` shows which module groups are real, so a broken module is visible instead of silently mocked. A golden-path script (`make demo-check`) walks the whole story end to end and a mutation check deliberately breaks scheduling rules to confirm the tests catch them.

No language model decides anything: numbers come from the health model, the solver and the tools.

## Challenges
- Four people and four AI assistants building in parallel: we kept a single written contract (`CONTRACTS.md`) and a single ownership file so branches fit together.
- Honest data: aircraft sensor data is not public, so engine degradation uses NASA C-MAPSS and everything else is simulated and labeled SIMULATED everywhere.
- Making the comparison meaningful: with one fault, cost metrics tie, so we added a risk view (time to fix, flights flown with the fault open, margin).

## Accomplishments we are proud of
A closed loop that runs end to end on real modules: fault, remaining-life estimate, plan, human approval, audit, a part delay, a re-plan. Over 300 automated tests, an independent constraint checker, and a golden-path script that passes 16 of 16 checks.

## What we learned
Safety-critical advice needs hard constraints, explicit uncertainty and a human in the loop; an LLM is best used to choose tools and phrase answers, not to produce numbers.

## What's next
Real airline data connectors (maintenance records, parts, rosters), validation on airline sensor data, a live LLM copilot mode tested against the real service, and real crew rosters and duty data for the crew view, and a way for pilots to report observations.

## Built with
Python, FastAPI, pydantic, scikit-learn (random forest, isolation forest), OR-Tools CP-SAT, SimPy, SQLite, React, TypeScript, Vite, Tailwind CSS, Recharts, three.js, openapi-typescript. Optional: the Anthropic API (Claude) for the LLM mode of the copilot and agents.

## Data and APIs used
NASA C-MAPSS turbofan degradation data (FD001; A. Saxena and K. Goebel, "Turbofan Engine Degradation Simulation Data Set", NASA Ames Prognostics Data Repository, 2008). It is public; we download a public mirror of the files at setup time and do not commit them. Everything else (fleet, flights, stations, parts, technicians, task cards) is simulated or written by us during the event.

## Disclosure: what existed before Hacklanta
- The AeroTwin AI concept document (Aadhavan) and a separate planning document for a different idea (the parked `parked-meridian/` folder): planning text only.
- Public data and open-source libraries listed above.
- No application code existed before the event; the repository was created after hacking opened.

## Instructions for judges
Everything is simulated; no login, no keys, no internet needed at demo time. To run it yourself: see the README (about 5 minutes: `make setup`, `make data`, `make reset`, `make api`, `make web`, open http://localhost:5173). The demo uses the scripted agent path; the optional LLM path needs an `ANTHROPIC_API_KEY` and was tested only with a fake client during the build. The system is advisory only and does not certify an aircraft or execute maintenance.

## Team (max 4)
Shamit, Aashirth, Pardhu, Aadhavan.
