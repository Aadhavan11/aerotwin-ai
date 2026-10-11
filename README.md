# AeroTwin AI: team build docs

**Predict the failure. Simulate the future. Prevent the disruption.**

An AI-powered aircraft digital twin that predicts component degradation, simulates future aircraft health, and keeps re-planning maintenance across an airline fleet
as conditions change (a delayed part, a closed facility, a new fault). Concept by Aadhavan; built for a 36-hour hackathon.

**Status: documentation so far.** Code lands in per-owner folders (`app/`, `web/`). Everything about aircraft health is simulated and labeled so.

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
| [`parked-meridian/`](parked-meridian/) | Parked: a separate climate-routing idea. **Not part of this build** |

Independent student project. Not affiliated with or endorsed by any airline, aircraft manufacturer or event organizer.
