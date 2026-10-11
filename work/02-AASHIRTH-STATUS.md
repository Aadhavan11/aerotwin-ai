# Aashirth: status (work/OWNERSHIP.md pieces 12 and 15)

Branches: `aashirth/ml-core` (model, twin, copilot) and `aashirth/llm-mode` (that plus the agent LLM mode, on the integration branch). Nothing is merged to `main`; Shamit merges.
Details: `docs/HEALTH.md` (model, twin), `docs/COPILOT.md` (copilot, LLM mode, known problems), `docs/DATA-SOURCES.md`.

## Done

- [x] `ml/sensors.py`, `ml/cmapss.py` (loader, `trajectories()` on held-out test engines, synthetic fallback)
- [x] RUL model with calibrated p10/p50/p90, anomaly model, data quality, rule fallback, `evaluate()` / `eval.json` (includes `n_test` for `/metrics`)
- [x] Non-engine components: rule-based demo models (`ml/sensors.py` `GENERIC` has the telemetry keys)
- [x] Twin state engine: `ingest_telemetry`, `apply_event`, `apply_maintenance`, `get_twin`, `snapshot`, `status_from`; `GET /aircraft/{tail}`, `/telemetry`, `/metrics/ml`
- [x] Copilot: scripted six questions, LLM chat, work orders (one store), audit (newest first), KB search, endpoints as in the mock
- [x] LLM mode for the agents on Pardhu's registry and run store, with fallback and run cache (branch `aashirth/llm-mode`)
- [x] Tests: 132 on `aashirth/ml-core`; 285 on the integrated branch

## Not verified

- [ ] **The live Anthropic API.** Both LLM paths are tested against a fake client only. Put your own `ANTHROPIC_API_KEY` in `.env`, restart the API and run `make check-llm`: it asks one question and runs the agents once in LLM mode, and says whether each used the model or fell back (and why). `GET /copilot/status` and the Copilot drawer show the mode. Nobody can do this step for you: the key must not go in chat or a commit.
- [x] Numbers on the real fleet: `make demo-check` passes 16/16 on the integrated app with the NASA data (re-run it after every merge, and read the numbers off the screen before they go on a slide).

## Waiting on others

- [ ] Aadhavan: `app/sim/router.py` for Shamit's registry (see `docs/COPILOT.md`)
- [ ] Shamit: merge; decide the fixture name collisions; call `copilot.reset_all()` and `twin.state.reset()` on `/scenario/reset` if his reset does not clear the shared tables
- [ ] Pardhu: nothing blocking; `app/agents/workorders.py` now delegates to the shared store (branch `aashirth/llm-mode`)
- [ ] Everyone: `requirements.txt` needs scikit-learn **pinned to 1.9.1** (the committed model needs it), joblib, anthropic, pyyaml, ortools, simpy, httpx; run `python -m app.ml.cmapss --download` once

## Honest limits

- RUL error about 11 cycles on held-out engines (MAE 10.84, RMSE 14.39); 3.8 cycles late in life. Trained on C-MAPSS FD001, not airline data.
- Anomaly recall 0.72: about 3 in 10 late-life windows are not flagged by that model alone; the `rul_p10 < 30` rule also raises `alert`.
- Work orders are advisory; a person approves every one; the copilot never approves.
