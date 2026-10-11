# Simulator integration branch

Branch `aadhavan/simulator-integration` combines the simulator with Aashirth's `aashirth/ml-core` and Pardhu's `claude/great-albattani-bv8i0b`. These team branches were not on main when this integration was built. Shared contract conflicts were only identical model definitions with differing introductory comments; the contract model definitions were preserved.

## Run the combined API

```sh
.venv/bin/pip install -r requirements-integration.txt
.venv/bin/uvicorn app.sim.integration:create_app --factory --port 8000
```

The server mounts simulator, twin, solver, what-if and scripted agent routers. It also supplies fleet overview, health and legs for integration testing. It lives in `app/sim/integration.py` so Shamit can take the wiring into his own main application. Run one process; the team stores are currently global and in memory.

Run the real end-to-end check:

```sh
.venv/bin/python -m scripts.simulator_integration_demo
.venv/bin/python -m pytest -q
```

The demo uses the installed ML artifacts and available C-MAPSS data, reports its actual data source and predictions, and asserts that agents finish, work orders remain pending human approval, the initial plan is feasible, and a 36-hour part delay changes the plan. It does not auto-approve or execute maintenance. A 36-hour delay is used to force a visible disruption in this fleet; a 12-hour delay can finish before the overnight maintenance window and need no move.

## What changed

- The twin is seeded with the actual 24-aircraft fleet, linked to the persistent simulation clock and given the simulator's legs/resources provider. Generic telemetry now uses Aashirth's wear-signal keys rather than the earlier generic placeholders.
- Stepping updates scored state, fleet status, location, observed counters and flight timelines. Fault injection calls the twin's public event handler. Reset clears twin histories, plans and agent runs as well as durable simulation state.
- Restart reconstructs telemetry/scoring windows and restores timelines and fault replay mappings from SQLite. A public twin operation-counter hook preserves flight block hours without dropping scoring history.
- Injected engine anomalies choose a deterministic genuinely late-life held-out unit. Some held-out test trajectories end with over 100 cycles remaining, so simply jumping to the end of an arbitrary unit did not produce a late-life fault. Selection uses trajectory `true_rul` to create the scenario; scoring still uses only sensor inputs and reports its actual estimate.
- The agent sensor-trend tool handles an installed but empty twin gracefully. Solver fallback provenance is labeled correctly when the twin has not yet been seeded.
- The downloaded NASA FD001 files remain under ignored `data/cmapss/`. scikit-learn is pinned to the saved model metadata version (1.9.1).

## Avoid applying disruptions twice

After `/scenario/inject` applies a resource event, send the same event to `/plan/replan` with `already_applied: true`. Pardhu's router already supports this flag. If the event is sent directly to `/plan/replan` instead, omit the flag; his world-event overlay applies it once. Do not mix a pending overlay event with a second injection of that same event.

## Remaining platform work

Shamit's shared database and main application have not landed. This integration uses the simulator's contract-shaped SQLite operations tables and the twin's public interfaces; plans and agent runs still use Pardhu's memory stores. Their listeners can be connected to the shared persistence layer later. Work-order approval and the web shell are separate team deliverables. Multiple API instances sharing one file remain unsupported.
