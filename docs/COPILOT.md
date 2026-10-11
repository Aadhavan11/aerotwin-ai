# Copilot backend and LLM mode (feature 3.6 backend, 3.4 LLM mode)

Code: `app/copilot/` (`chat.py`, `tools.py`, `llm.py`, `workorders.py`, `audit.py`, `kb.py`, `router.py`). Owner: Aashirth (`work/OWNERSHIP.md` piece 12).

## What works

| Piece | Behaviour | Tested with |
|---|---|---|
| Scripted copilot | Keyword intents for the six example questions call deterministic tools and fill a template. Always available. | A hand-built fleet and plan (34 tests) |
| LLM copilot | Claude chooses tools and writes the answer; any numeral not in tool output replaces the answer with the scripted one; any API error, refusal or truncation falls back to scripted. | A fake client only |
| Work orders | Drafted from a plan, always `pending_approval`. Only a named person can approve or reject (`system`, `unknown` and `agent:*` are refused). Repeat decisions return 409. Each step writes an audit entry. One store for the chat and the agent runs. | Unit and HTTP tests |
| Storage | Shared SQLite tables (`work_orders`, `audit`, `kv`) when the DB is seeded and `MOCK` is off; memory otherwise. Survives a restart; a scenario reset that clears those tables clears the copilot. | Unit tests on a temp DB |
| Never later | A new plan supersedes a pending order. An approved order is replaced only by an earlier slot, never a later one. | Unit tests |
| Completion | `complete_due(now)` marks approved orders finished and resets the twin component. Call it after `POST /sim/step`. | Unit test |
| Knowledge base | Keyword search over the synthetic task cards in `app/copilot/kb/`. | Unit tests |

**Not verified:** the live Anthropic API. The LLM loop is exercised against a fake client that returns the SDK's block shapes; no call was made to the real service from this machine (no key in the shell). Before the demo, run one question with a key set and check it answers without falling back (the server log says `copilot LLM failed ...` when it does).

## Endpoints (mount `app.copilot.router.router`)

| Method and path | Body | Response |
|---|---|---|
| `POST /copilot/chat` | `ChatRequest` | `ChatResponse`: `answer`, `evidence[]`, `missing_info[]`, `proposed_work_orders[]` |
| `GET /copilot/quick-prompts` | | `{prompts: [six questions]}` |
| `GET /workorders` | | `WorkOrder[]` |
| `POST /workorders/{id}/decision` | `DecisionRequest` from `app/contracts/requests.py`: `{decision: "approve"\|"reject", user, note}` | `WorkOrder`; errors `{error, detail}` with 404, 409 or 422 |
| `GET /audit` | | `AuditEntry[]`, **newest first** (as the mock API) |

Call `app.copilot.reset_all()` and `app.twin.state.reset()` from `POST /scenario/reset`. In database mode the copilot's rows are in the shared tables, so the reset that clears them clears the copilot.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | none | Presence enables LLM mode. Never commit it; keep it in the gitignored `.env`. |
| `LLM_MODEL` | `claude-sonnet-5-5` | Model for the copilot and agents |
| `LLM_EFFORT` | `medium` | `output_config.effort` (`low` is faster) |
| `LLM_TIMEOUT_S` | `30` | Per-call timeout, one retry |
| `COPILOT_MODE` or `AGENT_MODE` | unset | `scripted` forces scripted mode |
| `AGENT_CACHE_PATH` | `data/agent_cache.json` | Run cache used by `llm.RunCache` |

Request shape follows the current model rules: `tool_choice` is left at `auto` (forced tool use is rejected by Sonnet 5.5), `thinking` is not sent (adaptive by default), and no sampling parameters are set.

## LLM mode for the agent workflow (piece 12 on top of Pardhu's runner)

Branch **`aashirth/llm-mode`** = Aadhavan's `aadhavan/simulator-integration` + this branch + three small changes. It exists because the LLM mode has to run on Pardhu's registry and run store, which are not on `main` yet.

| File | Change |
|---|---|
| `app/agents/llm_mode.py` (new) | `run_llm(run_id, goal, scope)`: the five agents with Claude over `TOOLS`; steps stream into `RUNS`; every summary is number-checked against tool output; finished runs are cached (`data/agent_cache.json`, replay with `scope.replay` or `AGENT_CACHE_REPLAY=1`). Raises `LlmModeError` on anything untrustworthy. |
| `app/agents/runner.py` | `mode="llm"` calls it when a key is present; on any failure the steps are cleared and the scripted workflow runs on the **same run id**, `mode="scripted"`, with the reason in `missing_info`. No key: scripted plus a note. |
| `app/agents/workorders.py` | `draft()` delegates to `app.copilot.workorders`, so agent-drafted orders are in the same store the Approve button uses (before this, they lived only on the run and Approve returned 404). |
| `tests/test_llm_mode.py` (new) | 10 tests with a fake client |
| `tests/test_simulator_integration.py`, `pytest.ini` | clear `MOCK` for the live-simulator test (Shamit's `tests/conftest.py` sets `MOCK=1` for the whole session, which makes the solver plan on the stand-in fleet); put `tests/` on the path so Pardhu's `solver_helpers` import works next to Shamit's `tests/__init__.py` |

Verified on that branch: **285 tests pass**; 2 fail (see below).

## Known problems that are not in this module

| Problem | Where | Effect |
|---|---|---|
| `app/sim/router.py` does not exist | Aadhavan (Shamit's `app/main.py` loads `app.sim.router.router`) | The `sim` group is marked "mock", so `/fleet` stays on fixtures and `tests/test_db_mode.py` (2 tests) fails. |
| `scripts/reset_db.py` calls `generate_fleet`, which returns a `FleetState` but does not write the DB | Shamit and Aadhavan | The DB is seeded only when `IntegratedSimulator` is constructed. |
| Same fixture file names written by two generators | Shamit (mock fixtures) and Pardhu / Aadhavan (generated) | Merging collides on `fleet.json`, `stations.json`, `legs_golden.json`, `plan_*.json`, `replan_result.json`, `agent_run_golden.json`. The generated ones were kept on `aashirth/llm-mode`; Shamit decides. |
| Global in-memory stores | everyone | Run one API process. |

## Integration facts that were checked

- Real held-out C-MAPSS telemetry for SIM-017 engine_2 through the model and twin gave `alert` with RUL p10/p50/p90 = 11.8/18.3/25.1 (NASA's true value 19); the scheduler planned it at DFW against the ATL baseline ($600 vs $2,200 logistics cost); a 12 h DFW kit delay replanned to the next DFW window in 0.03 s. Stand-in fleet numbers: re-run on the real fleet before any slide.
- Aadhavan's branch already treats an empty twin as unavailable inside the solver (`list_aircraft()`), so the change this document used to ask Pardhu for is no longer needed. `twin.state.snapshot()` itself never substitutes another fleet.
