# Project Meridian / AeroTwin AI: team brief (read this first)

> **UPDATE Sat 2026-10-10, 8:30 PM ET: read [`MERGE-PLAN.md`](MERGE-PLAN.md) first.** It merges this plan with a teammate's AeroTwin AI doc, sets roles, and replaces the schedule below (section 11) with one for the ~10.5 hours left.

**In one breath:** an AI decision platform for airlines that treats two big costs as one loop. (1) A **route
optimizer that prices climate impact** (CO2, contrails, NOx), not only fuel and time. (2) A **digital twin of each
aircraft** that simulates wear from how it is actually flown and uses the real flight plan and stops to pick the
**best place and time to do maintenance** with the least wasted ground time. Both sit in one **ops platform**
(flight paths, weather, aircraft condition, what-if). Shamit Basu is leading it.

**What is new and what is not (say this honestly in the pitch):** contrail-aware routing and predictive
maintenance each have serious incumbents. Our claim is the **coupling**: maintenance needs change the route, the
route changes component wear, and one platform scores both. Do not claim to "replace the airline industry's tech";
claim "a decision layer that interoperates first."

## Where we are

- A full plan exists: `HACKATLANTA-PLAN.md` (architecture, how data is simulated vs. how it really arrives, a 36-hour
  schedule, 52-item "things to avoid" list, risks, demo script).
- **No code from the plan exists yet.** It was held back deliberately until the event rules are known.
- The event we assume is **Hacklanta '26** (Georgia State University, Oct 9 to 11, 2026, 36 hours, teams of up to 4,
  kickoff Fri 7 PM ET). That came from a third-party listing and is **unverified**. The listing also says you must
  complete the **hacklanta.dev** application *and* Devpost, or you cannot submit or win.
- The event may already be running. The schedule in the plan counts hours from Fri 7 PM; compress it to the time left
  (keep the order: contracts, thin end-to-end slice, then polish; feature freeze about 8 hours before the end).

## What we need from you

1. Read this file, then skim `HACKATLANTA-PLAN.md` sections 0, 1, 6, 9 and 11.
2. Take a role (plan section 11.3): **A** Atmosphere and Route (router, weather, contrails), **B** Twin and Planner
   (wear models, maintenance solver), **C** Data fabric and Platform (simulator, bus, API, infra), **D** Front end
   and Story (UI, demo, video, pitch).
3. Use the prompt in `CLAUDE-PROMPT.md` with your Claude so it starts from the same facts and rules.
4. Before writing code: confirm with Shamit that the event rules allow it, and agree the shared contracts (plan 7.1).

## What is in this pack

| File | What it is |
|---|---|
| `TASK-BOARD.md`, `FEATURES-3.1-3.6.md`, `CONTRACTS.md`, `INTEGRATION.md`, `work/0*.md` | **Build docs:** who does what and when, the six features, exact interfaces, how it connects, one package per person |
| `MERGE-PLAN.md` | **Read first:** merges AeroTwin AI and Meridian; scope, roles, contracts, schedule for the time left |
| `docs/AEROTWIN-CONCEPT.md` | The teammate's AeroTwin AI concept doc |
| `START-HERE.md` | This brief |
| `HACKATLANTA-PLAN.md` | The master plan (about 1,000 lines). Sections: 0 read-first, 1 pitch, 4 architecture, 5 data, 6 modules, 7 integration, 9 things to avoid, 11 schedule, 15 open questions |
| `DATA-CHEATSHEET.md` | Live endpoints we tested (with results), working code snippets, library licences, datasets, links |
| `GLOSSARY.md` | Aviation, weather and climate terms in plain English |
| `CLAUDE-PROMPT.md` | The prompt to paste into your Claude, plus a short version |

## Ground rules (the short list; the full one is plan section 9)

- **Advisory only.** We recommend; humans decide. Never claim certification or "FAA approved".
- **Never recommend maintenance later than an approved interval** (earlier is fine). Extensions are a question for a human.
- **LLMs read and explain; code decides.** An LLM never produces a limit, interval or torque value.
- **Everything simulated is labeled SIMULATED**, in the UI, the API and the video. Use fictional `SIM-xxx` tails, never real tail numbers with fake faults.
- **Open tools only:** OpenAP (not BADA), NOAA, AWC, OpenSky. No scraping flight-tracking sites. No Boeing/Airbus/airline manuals.
- **Units:** SI inside, convert at the edge. UTC everywhere. Test the wind sign convention and the flight-level rule.
- **Do not state a "% reduction" without the metric** (GWP100 vs ATR20) and do not optimize contrails while ignoring NOx.
- **Keys never go in chat, issues, commits or screenshots.**
- **Follow the event's AI policy** and disclose AI assistance in the README and Devpost if required.
- **One thin end-to-end slice beats ten half-built features.**

## Open decisions (plan section 15)

Confirm the event and registration; the rules on pre-written code and AI; which track or sponsor challenge; who is A/B/C/D;
who provides the LLM key; GitHub repo (name, owner, visibility); primary and backup demo machine; whether anything gets a public URL.

## How much to trust the plan

- **Tested:** the weather, OpenSky, NOAA and package facts in `DATA-CHEATSHEET.md` section 1 and 3 were run on 2026-10-09.
- **From search results, not independently confirmed:** the event details, OpenSky limits, the contrail trial numbers, the NOx finding.
- **From general knowledge, to verify before showing on a slide:** MEL categories, the flight-level rule details, the 3.16 CO2 factor, OpenAP's supported aircraft types.
- Any number on a slide must come from our own evaluation run (plan section 10), with its method.

If something in the plan is wrong, vague, or conflicts with the event rules, say so and fix the file: it is a working document.
