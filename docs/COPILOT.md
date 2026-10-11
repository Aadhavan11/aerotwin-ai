# Copilot backend and LLM mode (feature 3.6 backend, 3.4 LLM mode)

Code: `app/copilot/` (`chat.py`, `tools.py`, `llm.py`, `workorders.py`, `audit.py`, `kb.py`, `router.py`). Owner: Aashirth (`work/OWNERSHIP.md` piece 12).

## What works

| Piece | Behaviour | Tested with |
|---|---|---|
| Scripted copilot | Keyword intents for the six example questions call deterministic tools and fill a template. Always available. | A hand-built fleet and plan (34 tests) |
| LLM copilot | Claude chooses tools and writes the answer; any numeral not in tool output replaces the answer with the scripted one; any API error, refusal or truncation falls back to scripted. | A fake client only |
| Work orders | Drafted from a plan, always `pending_approval`. Only a named person can approve or reject (`system` and `agent:*` are refused). Repeat decisions return 409. Each step writes an audit entry. | Unit and HTTP tests |
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
| `POST /workorders/{id}/decision` | `{decision: "approve"\|"reject", user, note}` | `WorkOrder`; errors `{error, detail}` with 404, 409 or 422 |
| `GET /audit` | | `AuditEntry[]`, oldest first |

Call `app.copilot.reset_all()` from `POST /scenario/reset` (and `app.twin.state.reset()`).

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

## For Pardhu: using the LLM loop from the agent runner (piece 11)

```python
from app.copilot import llm
from app.copilot.tools import Toolbox, Tool          # Tool(name, description, input_schema, fn, agent)

res = llm.run_agent("health", goal, context_dict, tools={"list_at_risk": ...},
                    ts_fn=clock.now, start_n=1, template=scripted_summary)
res.steps   # list[AgentStep], numbers already n=1.. ; res.text ; res.numbers_ok ; res.error
```

If `res.error` is set, run that agent in scripted mode and record `AgentRun.mode="scripted"`. `llm.state_hash(state)` and `llm.RunCache` implement the "(goal, snapshot) -> finished run" cache. The `Toolbox` in `app/copilot/tools.py` already wraps the twin, the scheduler service and `what_if` for the copilot; his registry can reuse or replace it. Registry tool names in `CONTRACTS.md` section 6 are kept where they exist (`list_at_risk`, `get_component_health`, `get_sensor_trend`, `get_maintenance_history`, `search_kb`, `draft_work_orders`).

## Integration with the scheduler (verified in a throwaway merge)

`Toolbox` reads the world through `app.solver.service.get_state()` and `ensure_plans()` and calls `app.whatif.replan.replan` for the facility what-if (pure; it does not change the live plan). With Pardhu's branch merged:

- The merged test suites pass together (208 tests with the NASA files, 195 passed and 13 skipped without them) **after one change in `app/solver/service.py`**: treat an empty twin as unavailable, so its tests and its source label stay honest.

```python
    snap = snapshot()
    if not snap.components:      # the twin exists but holds nothing yet
        log.warning("twin is empty: using the golden stand-in state")
        return golden_state(), "golden stand-in (twin is empty)"
    return snap, "twin.snapshot"
```

  The twin's `snapshot()` never substitutes another fleet by itself.
- End to end on real data: held-out C-MAPSS telemetry for SIM-017 engine_2 through the model and twin gave `alert` with RUL p10/p50/p90 = 11.8/18.3/25.1 (NASA's true value 19); the scheduler planned it at DFW against the ATL baseline ($600 vs $2,200 logistics cost); a 12 h DFW kit delay replanned to the next DFW window in 0.03 s. These are stand-in fleet numbers and must be re-run on Aadhavan's real fleet before any slide.
