# AeroTwin AI: team brief (read this first)

**In one breath:** machine-learning models estimate how long each aircraft component has left; a digital twin tracks every aircraft; an optimizer plans **where and when** to fix
things around real flight schedules, technicians and spare parts; a team of tool-using AI agents does the investigating and explaining; and when something changes (a delayed
part, a closed facility) the plan is **recomputed automatically**. A human approves every action. The concept was written by **Aadhavan** (`docs/AEROTWIN-CONCEPT.md`).

## Where we are

- The scope is decided: **AeroTwin AI only**, features 3.1 to 3.6 of the concept doc. Shamit's separate climate-routing idea is parked in `parked-meridian/`.
- Docs are written; **no application code yet.** Time left: about 10 hours if the event ends Sun ~7 AM (**unverified**: check the real deadline).
- The event is probably Hacklanta '26 (Georgia State, Oct 9 to 11, teams of up to 4), from an unverified listing. It may require the **hacklanta.dev** application as well as Devpost.

## What to do right now

1. Read [`SCOPE.md`](SCOPE.md) (10 minutes), then [`work/OWNERSHIP.md`](work/OWNERSHIP.md) (**who owns what now**), then **your work package** in [`work/`](work/): `01` Shamit, `02` Aashirth, `03` Pardhu, `04` Aadhavan.
2. Use [`CLAUDE-PROMPT.md`](CLAUDE-PROMPT.md) with your Claude so it starts from the same facts and rules.
3. Check the event rules (pre-written code, AI policy, deadline) and tell the group.
4. Set up the dev environment (`INTEGRATION.md` section 10) and wait for **C0** (Shamit's skeleton + mock API, target 9:15 PM). Meanwhile do the first task in your package.

## Pack contents

| File | What it is |
|---|---|
| `SCOPE.md` | What we build, roles, schedule, demo, limits |
| `docs/AEROTWIN-CONCEPT.md` | The concept doc (defines the project) |
| `FEATURES-3.1-3.6.md` | Each of the six features: what we build, acceptance tests, golden scenario |
| `TASK-BOARD.md` | Timeline per person, dependencies, checkpoints |
| `CONTRACTS.md` | Models, DB tables, endpoints with examples, function signatures, agent tools |
| `INTEGRATION.md` | Architecture, flows, LLM rules, git, testing |
| `work/01..04` | One package per person |
| `CLAUDE-PROMPT.md`, `GLOSSARY.md` | Prompt and plain-English terms |

## Ground rules (short list)

- **Advisory only.** A human approves every action. Never claim certification or "FAA approved".
- **Never recommend maintenance later than an approved deadline** (earlier is fine). Deadlines come from the **conservative** RUL estimate (p10) and are hard constraints.
- **Numbers come from tools, not from the LLM.** The LLM chooses tools and writes explanations.
- **Everything simulated is labeled SIMULATED**; fictional `SIM-xxx` tail numbers; no real manuals, real telemetry or real airline names attached to faults.
- **Report results honestly:** the baseline, the dataset and the assumptions. Simulated improvements are not verified airline savings.
- **Keys never go in chat, issues, commits or screenshots.** `.env` stays out of git.
- Follow the event's AI policy and disclose AI assistance in the README and Devpost if required.
- **One working end-to-end loop beats ten half-built features.**

## How much to trust these docs

- **Tested:** the data models in `CONTRACTS.md` load, and the JSON examples validate (pydantic 2.14, 2026-10-10).
- **Assumptions, to verify:** whether the NASA C-MAPSS files can be downloaded (there is a synthetic fallback), the exact OR-Tools method names for your installed version, the event's end time and rules.
- Any number on a slide must come from our own run (`make demo-check`, `eval.json`), with its method.

If something in these docs is wrong or conflicts with the event rules, say so and fix the file. It is a working document.
