# Aadhavan's data simulator

Implemented against main `12b9ed9` on branch `aadhavan/simulator`. All aircraft and operations are SIMULATED. This is the data simulator, not Pardhu's what-if scheduler.

## Run

From the repository root:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements-sim.txt
.venv/bin/python -m pytest tests/test_simulator.py -q
.venv/bin/python -m app.sim
.venv/bin/uvicorn app.sim.api:create_app --factory --port 8000
```

`python -m app.sim` regenerates the three fixtures and runs reset → 24-hour step → anomaly → part delay in memory. It does not assert a feasible maintenance plan: the solver, agents and twin modules have not landed yet.

## Integration

- `generate_fleet(seed)` returns a contract-valid `FleetState`; `dump_fixtures` exports the FleetOverview, stations, and SIM-017 legs.
- `Simulator(db_path, seed, ingest, trajectories)` persists simulation-owned state to the SQLite tables in CONTRACTS.md. `SimClock` stores UTC time in `kv`.
- Mount `create_router(simulator)` from `app.sim.api` in the shared FastAPI app. It provides `/sim/step`, `/scenario/inject`, `/scenario/reset`. The standalone health route belongs only to the development app.
- Pass Aashirth's `ingest_telemetry` as `ingest` and `ml.cmapss.trajectories()` as `trajectories`. The standalone factory discovers these modules when present. Standalone synthetic fallback uses the fourteen engine sensor names in the contract.
- Replay assigns units deterministically and starts each engine at the beginning of its trajectory, while maintaining the component's cumulative operational cycle counter. Anomaly injection advances the replay near the final 25 cycles and feeds eight rows. No real RUL estimate is asserted by the fallback.
- The simulator seeds components with explicitly labeled placeholder health/RUL values from the fixture initialization; ML ingestion replaces component state when connected. These initial values are not evaluation results.
- Events must have `at` equal to simulation time. Injection applies the resource/telemetry change and stores an event and audit entry. Returned `twin_changes` contains fault timeline records for the twin owner to incorporate; these are also reconstructible from the persisted events. Resource events do not invoke a solver: callers use the existing `/plan/replan` flow, avoiding a second application of the same event.
- Reset clears generated and derived state, including plans/work orders when those tables exist. Use only on the dedicated simulated database.
- Step counts completed legs in `(old_time, new_time]`, updates location/cycles/hours, and emits six component telemetry rows per completed leg. Zero-hour stepping emits no duplicate rows. Source position, origins and fault settings survive restart.

## Current limits

A single simulator instance owns the database while running; do not run the standalone server beside the shared API against the same file. Shared state changes outside this service are not automatically refreshed into its in-memory snapshot. Coordinate mutations through the simulator and twin callback. Twin callbacks should return a `ComponentState` and propagate unexpected errors; ingestion across separate database connections is not an atomic distributed transaction. Real C-MAPSS download is the ML owner's task; the replay interface is tested with a small synthetic trajectory. The proposed Aircraft Twin UI requires Shamit's web shell and Aashirth's twin backend, neither of which exists in this checkout yet.
