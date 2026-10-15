# AeroTwin AI

**Predict the failure. Simulate the future. Prevent the disruption.**

An AI-powered aircraft digital twin for a simulated airline fleet. It estimates how much life each component has left (with uncertainty), simulates what happens when
something changes (a fault, a delayed part, a closed hangar), and re-plans maintenance around flights, parts, technicians and hangar space. A person approves every work
order. Built in 36 hours at Hacklanta 2026 by Shamit, Aashirth, Pardhu and Aadhavan (AI/ML track). Concept by Aadhavan.

**Everything about aircraft health is simulated and labeled SIMULATED. The system is advisory only; it certifies nothing.**
No airline, manufacturer, MRO or regulator data is used and there is no affiliation with any of them. Tail numbers, part numbers, serials and life limits are fictional or assumed. It is not for airworthiness, dispatch or maintenance decisions. Read [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) before trusting any number; reviewer reading order: [`docs/INDEX.md`](docs/INDEX.md).
Devpost text: [`docs/DEVPOST.md`](docs/DEVPOST.md). Event rules and checklist: [`docs/HACKLANTA-RULES.md`](docs/HACKLANTA-RULES.md). Demo script: [`docs/DEMO-SCRIPT.md`](docs/DEMO-SCRIPT.md).

**Live demo:** https://shamitbasu.com/hacklanta (a shared public instance on simulated data: every visitor shares one simulation, so reset it under System if it looks different). It asks you to sign in: pick a demo account on the sign-in screen (one per position), create an account and choose your position, or use Google. Details: [docs/AUTH.md](docs/AUTH.md). Local runs have no sign-in unless `AUTH_REQUIRED=1`.

## Run it (about 5 minutes, no keys, no internet at demo time)

You need **Python 3.13** (the version we tested; slightly older 3.x should work but is untested) and **Node 20+**. Internet is needed once, to download the NASA data. We tested on Linux (Raspberry Pi); Windows and macOS are untested.
Without `make` (Windows), run the command shown after each step instead.

```bash
make setup        # python -m pip install -r requirements.txt   and   cd web && npm install
make data         # python -m app.ml.cmapss --download     (NASA C-MAPSS FD001 into data/, not committed)
make reset        # python -m scripts.reset_db --scenario golden   (rebuilds the demo database, under 10 s)
make api          # python -m uvicorn app.main:app --port 8000     (leave running)
make web          # cd web && npm run dev                          (second terminal) then open http://localhost:5173
```

Check that it works: `GET http://localhost:8000/health` should list all six module groups as `real`, and `make demo-check` (third terminal) walks the whole story and should print `16/16 passed`.
`make test` runs the automated tests (about 3 minutes). Without `make data` the app still starts but falls back to synthetic engine data and the numbers differ.

**If `make demo-check` shows 14/16, or the fault shows a remaining life near 0 and the plan says "not feasible":** the NASA data is missing (the app then uses a synthetic stand-in, and the app shows a red banner). Run `python -m app.ml.cmapss --download` (2 seconds, needs internet), restart the API, and try again. `make reset` now fetches the data automatically when it can.

**Optional LLM mode:** copy `.env.example` to `.env` and put your own `ANTHROPIC_API_KEY` in it (never commit `.env`, never paste it in chat), restart the API, then `make check-llm` asks one copilot question and runs the agents once in LLM mode and says whether each used the model or fell back to scripted (and why). The Copilot drawer shows which mode is active. The demo and all numbers use the scripted path and need no key.

## What you will see
Role views for operations control, maintenance planners, pilots, crew and dispatch, and administrators ([`docs/ROLE-VIEWS.md`](docs/ROLE-VIEWS.md)); fleet map and health of 24 aircraft with stations, technician shifts and parts stock; a 3D aircraft twin (737-800 and A320 geometry) with component and part health and remaining-life bands; a Maintenance section per aircraft (next planned job, where and when, parts and technicians, suggestions, work-order approval, history); a planning timeline with the optimized plan against a static baseline and the
risk of waiting; a scenario lab (fault, part delay, hangar closed, technician out) with explained re-plans; agent and copilot workspace; human approval and an audit log.

## Credits

3D aircraft model: "B737-800 Model" by [hikami3150](https://sketchfab.com/hikami3150), licensed [CC BY 4.0](http://creativecommons.org/licenses/by/4.0/) ([source](https://sketchfab.com/3d-models/b737-800-model-6cbe380405794ea69d6b0a3d144dd1af); changes listed in [`docs/ASSETS.md`](docs/ASSETS.md)). NASA C-MAPSS engine data. Geist fonts (OFL).

## Team docs

| Read in this order | |
|---|---|
| [`SCOPE.md`](SCOPE.md) | **Start here.** What we build (and what we do not), roles, schedule, demo |
| [`docs/AEROTWIN-CONCEPT.md`](docs/AEROTWIN-CONCEPT.md) | The concept doc that defines the project |
| [`FEATURES-3.1-3.6.md`](FEATURES-3.1-3.6.md) | The six core features: exactly what we build, acceptance tests, the golden scenario |
| [`TASK-BOARD.md`](TASK-BOARD.md) | Who builds what and when, dependencies, checkpoints |
| [`CONTRACTS.md`](CONTRACTS.md) | Every model, table, endpoint and function signature (the single spec) |
| [`INTEGRATION.md`](INTEGRATION.md) | How the modules connect, runtime flows, LLM rules, git, testing |
| [`work/OWNERSHIP.md`](work/OWNERSHIP.md) | **Who owns what (wins over every other doc)** |
| [`work/`](work/) | One work package per person: Shamit, Aashirth, Pardhu, Aadhavan |
| [`CLAUDE-PROMPT.md`](CLAUDE-PROMPT.md) | Prompt to paste into your Claude so it starts from the same facts and rules |
| [`GLOSSARY.md`](GLOSSARY.md) | Plain-English terms |

## Run it (works today on mock data)

```
make setup        # pip install -r requirements.txt  +  cd web && npm install       (Python 3.11+, Node 20+)
make reset        # builds the golden database (< 10 s)
make api          # http://localhost:8000   (open /docs for the API, /health for which modules are real)
make web          # http://localhost:5173   (second terminal)
make test         # 30+ tests
make demo-check   # the golden path end to end against the running API
```

`MOCK=1 make api` serves fixtures for everything. Replacing a mock with your real module: **`INTEGRATION.md` section 13**. Who owns what: `work/OWNERSHIP.md`.

Independent student project. Not affiliated with or endorsed by any airline, aircraft manufacturer or event organizer.
