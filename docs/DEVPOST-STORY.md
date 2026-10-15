# Devpost "Project Story": raw markdown to paste into the About the project box

(Open `presentation/devpost-story.txt` or copy from a code view: copying from a rendered preview drops the ## and ** markers.)

---

**AeroTwin AI is a digital twin of an airline fleet. It estimates which simulated components are running out of life, plans the fix around flights, parts, technicians and hangars, and drafts the maintenance request. A person approves every one.**

*All aircraft data in this project is simulated. No airline, manufacturer or operator data was used, part numbers and life limits are fictional, and nothing here is for airworthiness, dispatch or maintenance decisions (see `docs/LIMITATIONS.md`).*

## Inspiration

Aircraft maintenance is a **coordination puzzle**. A component starts to wear out, but the aircraft, the hangar, the spare part and the qualified technician are in different cities, and the aircraft still has to fly its schedule. Planners work from fixed schedules and several separate systems, and when something changes (a delayed part, a closed hangar, a new fault) they re-plan by hand.

Aadhavan's concept, *"the self-optimizing aircraft fleet"*, asked a simple question: what if a digital twin of the fleet predicted the failure, simulated the options and drafted the plan, while a person stayed in charge of every decision?

## What it does

- **Estimates (on simulated data).** A health model estimates each component's remaining life with uncertainty (a conservative p10, a median p50, an optimistic p90) and flags abnormal sensor behavior. The aircraft twin shows the whole aircraft, then flies the camera in on the engine in trouble, marks the section the sensors point to, and lists every sensor against its healthy baseline.
- **Simulates.** A digital twin of **24 simulated aircraft, 8 stations, 144 monitored components and 480 scheduled legs**. A scenario lab injects a fault, delays a part, closes a hangar or removes a technician.
- **Plans.** A constraint solver (OR-Tools CP-SAT) finds the slot that fits flights, ground time, parts, technicians and hangar space, with the conservative p10 as a hard deadline. The plan is an **hourly calendar, one column per aircraft**, and it re-plans in a fraction of a second when something changes.
- **One whole maintenance request.** What is damaged or inspected, the work, the part and whether it must be ordered, when and where, why it is needed, and an estimated cost, in one card. People who may decide can **edit** the name, work, part, ordering or time (the server checks the deadline, the aircraft's ground time and the technician's shift), then **approve or deny**. Requests can also be entered by hand. Every change is in an audit trail.
- **An AI built into the work.** An ask bar on every page, ask fields where questions come up, and a copilot that sees your screen. Five agents (health, scheduling, logistics, operations, coordinator) call deterministic tools and draft requests. The copilot can **propose** editing or creating a request in plain words; it shows the exact change with **Allow** and **Deny** and changes nothing until a person allows it.
- **Suggested actions** for work that is not required yet but better planned ahead, each with a ground window that fits.
- **Five positions plus a master account:** operations control, maintenance planner, pilot, crew and dispatch, administrator. Sign-in, roles enforced on the server, messages with unread tracking, and it works on a tablet.

> Everything in it is simulated and says so. A few pages (duty and rest, weather, defect reporting, crew roster) are labeled **previews** with sample data.

## How we built it

We had 36 hours and four people, so we worked **contracts first**: Shamit wrote the shared data models, API contract, database and web shell on day one, with mock data for every endpoint. Each person then built a module against that contract, and the real modules replaced the mocks one group at a time:

- **Aashirth:** the health model and the twin
- **Pardhu:** the scheduler, the what-if engine and the agent tools
- **Aadhavan:** the simulator
- **Shamit:** the platform, front end, sign-in, calendar and integration

`GET /health` shows which module groups are real, so a broken module is visible instead of silently mocked.

Remaining-life estimates come from NASA's C-MAPSS turbofan data (random forest with calibrated intervals, isolation forest for anomalies). The planner is OR-Tools CP-SAT, the simulator uses SimPy, the API is FastAPI on SQLite, and the front end is React, TypeScript, Vite and Tailwind, with three.js for the 3D aircraft.

**No language model decides anything on its own.** When one writes an answer, every figure in it is checked against what the tools returned, and any change to a request is only *proposed* until a person allows it. We checked the whole thing with a golden-path script (**16 of 16 checks**), **more than 340 automated tests**, and a headless-browser pass over every page and button of every position, at laptop and iPad sizes.

## Challenges we ran into

- **Four people building in parallel.** One written contract and one ownership file kept the branches fitting together.
- **Honest data.** Airline sensor data is not public, so engine degradation uses NASA C-MAPSS and everything else is simulated and labeled.
- **A meaningful baseline.** With a single fault the cost metrics tie, so we also show risk (hours until fixed, flights flown with the fault open, margin) and added a documented, simulated price list for the cost estimate.
- **Keeping edits safe.** A person's change to a request's time is checked on the server against the same rules the planner keeps, and refused with the reason.

## Accomplishments that we're proud of

- A **closed loop** that runs end to end on real modules: a fault, a remaining-life estimate, a plan, a request a person can read, edit and approve, an audit trail, a part delay and a re-plan.
- In our golden scenario (a late-life engine fault on SIM-017), the optimized plan fixes it in **42 hours** with **73.6 hours of margin** and **6 flights** flown with the fault open. The static baseline takes **86 hours**, **14 flights** and **29.6 hours** of margin.
- The health model's p10 to p90 interval covers **92.9%** of 100 held-out engines it never saw.
- A copilot that can **propose changes but cannot make them**.

## What we learned

Safety-critical advice needs **hard constraints, explicit uncertainty and a human in the loop**. A language model is best used to choose tools, phrase answers and prepare changes, not to produce numbers or act alone.

## What's next for AeroTwin AI

- Real airline data connectors (maintenance records, parts, rosters, weather)
- Validation on airline sensor data
- Real crew and duty data behind the crew and dispatch previews
- Pilot defect reports that reach maintenance control
- Drag-to-reschedule on the calendar

## Built before Hacklanta

Only the **AeroTwin AI concept document** (Aadhavan). A planning document for a different idea was removed from the repo before submission; it stays in git history as planning text only. Public data and open-source libraries (NASA C-MAPSS and the libraries listed above) and one third-party asset, the 3D model "B737-800 Model" by hikami3150 ([Sketchfab](https://sketchfab.com/3d-models/b737-800-model-6cbe380405794ea69d6b0a3d144dd1af), CC BY 4.0), were downloaded during the event and are credited in the app. No application code existed before the event.

## Try it

**Live demo:** [shamitbasu.com/hacklanta](https://shamitbasu.com/hacklanta) (a shared simulation). **Code:** [github.com/Flash0529/aerotwin-ai](https://github.com/Flash0529/aerotwin-ai/tree/shamit/ui)

The sign-in screen has **one-click demo accounts** for each position; they share the password `aerotwin-demo`. Start as *Operations control*:

1. Fleet overview
2. SIM-017's aircraft twin (watch the camera fly in on the engine)
3. Maintenance plan (click a job to open its request)
4. Copilot (try `rename WO-1 to Borescope check`, then **Allow** it)

---

# Other Devpost fields

**Built with** (type each, press Enter; up to 25): python, fastapi, pydantic, scikit-learn, or-tools, cp-sat, simpy, sqlite, react, typescript, vite, tailwindcss, recharts, three.js, anthropic-claude-api, nasa-c-mapss

**"Try it out" links** (one per box, "Add another link" for more):
1. https://shamitbasu.com/hacklanta
2. https://github.com/Flash0529/aerotwin-ai/tree/shamit/ui

**Video demo link** (Project Media): the public YouTube or Vimeo link of `aerotwin-demo.mp4`.

**Image gallery** (JPG/PNG/GIF, 5 MB max, 3:2 recommended; these are already 3:2 in `presentation/screenshots/devpost-3x2/`), in this order:
1. `devpost-2-fault-closeup.png`: "The camera flies in on the engine in trouble; every sensor against its healthy baseline" (also the thumbnail)
2. `devpost-3-calendar.png`: "An hourly maintenance calendar, one column per aircraft"
3. `devpost-4-maintenance-request.png`: "One maintenance request: what, when and where, why, part and cost, then approve or deny"
4. `devpost-6-copilot-proposal.png`: "The copilot proposes a change; nothing happens until you allow it"
5. `devpost-5-optimized-vs-baseline.png`: "Optimized plan against the static baseline"
6. `devpost-1-fleet.png`: "Fleet overview"
