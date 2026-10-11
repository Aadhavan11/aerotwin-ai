# Integration findings (Pardhu, Sun 2026-10-11)

I merged `main` (Shamit), `aadhavan/simulator-integration` (Aadhavan) and `aashirth/ml-core` (Aashirth) into `claude/great-albattani-bv8i0b` and ran everything together,
including `python -m scripts.golden` against a live `app.main`. This branch is therefore **a complete integration base**: it contains all three branches plus the
scheduler, what-if and agent code. Every item below was reproduced; the patches were tested in a scratch copy and are NOT applied to anyone's files.

## Where things stand (updated after merging `integration/backend`)

* Whole repo on this branch: **292 passed, 13 skipped, 0 failed**. Everything below under "Items" that was about wiring is now fixed upstream
  (simulator mounted, telemetry ids aligned, order-dependent test fixed); items 3 to 6 are still open.
* `python -m scripts.golden` against a live `app.main`: **all six groups real, 14/16**. The two failures ("Agents: plan feasible" and
  "Replan: plan changed") have ONE cause: the injected anomaly comes back from the real model as **RUL p10 = 0** (p50 4, p90 10.8) when the NASA files are absent
  (synthetic fallback), so the deadline is hours away and the planner correctly says "no feasible slot ... too short for the 6 h job". Nothing is force-fitted.
  With RUL about 18 to 25 as in the concept doc the plan is feasible (that is what the golden fleet and the tests use).

## Items, by owner

### Aadhavan (simulator)

1. **`app/sim/router.py` is missing**, so Shamit's `main.py` keeps `sim` as a mock. Tested content (5 lines):
   ```python
   import os
   from app.sim.api import create_router
   from app.sim.integration import IntegratedSimulator

   simulator = IntegratedSimulator(os.getenv("DB_PATH", "data/aerotwin.db"), int(os.getenv("SEED", "1234")))
   router = create_router(simulator)
   ```
2. **`POST /sim/step` returns 500 (`sqlite3.IntegrityError: datatype mismatch`) inside Shamit's app.** Shamit's `app/db.py` creates `telemetry(id INTEGER PRIMARY KEY AUTOINCREMENT, ...)`;
   `app/sim/service.py` inserts text ids (`'SIM-017/engine_2:12'`) and its own `CREATE TABLE IF NOT EXISTS` is skipped because the table exists. Tested fix in `Simulator._ingest`:
   ```python
   self.connection.execute('DELETE FROM telemetry WHERE component_id=? AND cycle=?', (row.component_id, row.cycle))
   self.connection.execute('INSERT INTO telemetry (tail,component_id,cycle,ts,values_json) VALUES (?,?,?,?,?)',
                           (row.tail, row.component_id, row.cycle, row.ts.isoformat(), json.dumps(row.values)))
   ```
   (Check `events` / `audit` for the same clash.)
3. **`/scenario/inject` is stricter than the contract example.** It raises `event.at must equal the current simulation time`; the golden script and the UI send a fixed time
   (`CONTRACTS.md` example: `2026-10-12T14:00:00Z`) while sim time has moved. Either accept `at <= sim_now` and store `sim_now`, or tell Shamit's script/UI to send the current sim time.
4. **Demo calibration.** On your fleet SIM-017's first DFW night window starts at +14 h (Oct 13 02:00Z). The doc's 12 h kit delay (event at +2 h) ends exactly when it opens, so the
   golden replan shows an **empty diff**. Your 36 h delay works. Either keep 36 h for the demo or move SIM-017's first long window earlier.

### Aashirth (ML, twin, copilot)

5. **Injected anomaly gives RUL p10 = 1.9 cycles** on the synthetic fallback (data quality "window has 12 of 30 rows"). The doc's example is 18 to 25. With p10 below 2 the deadline
   is the next landing, so the planner correctly says "no feasible slot" (and suggests the earliest slot after the deadline for a human). If the demo should show a normal plan,
   the injection must leave a fuller scoring window or a milder RUL.
6. `tests/test_twin.py` errors with `StopIteration` (8 tests) and `tests/test_ml.py::test_late_life_truth_inside_band` fail on a machine without the NASA files: the `traj`
   fixture needs a unit with `true_rul > 100` and 120+ rows, which the synthetic fallback does not provide. Please make them skip, or fall back, when the real data is absent.

### Shamit (platform)

7. `tests/test_db_mode.py` is order dependent: on plain `origin/main` `test_fleet_from_database` fails when run alone (1 of 2). I did not touch it.
8. `tests/conftest.py` sets `MOCK=1` for every test. My scheduler used to treat `MOCK=1` as "use the stand-in fleet", which hid the live twin from Aadhavan's integration test.
   Fixed: only `MOCK_SOLVER=1` forces the stand-in now. Please keep `MOCK` meaning "mount the mock routers".

### Pardhu (me): fixed in this branch

* Routers now use the shared request models in `app/contracts/requests.py`; extras are query parameters (`?already_applied=true`, `?time_limit_s=`).
* `draft_work_orders` delegates to the copilot's audited store, so `POST /workorders/{id}/decision` finds the orders my agents draft.
* `get_sensor_trend` uses the twin's `get_telemetry` and says so when there is none.
* An infeasible task gets a "least-bad" suggestion (earliest slot after the deadline), labelled NOT a plan and never scheduled.

### Shamit (web app): one small thing I saw by rendering the screens against my live API

9. The Lab's scenario "Force service at ATL" sends `constraints: {force_station: "ATL"}` without a tail, which forces EVERY aircraft's work to ATL, so it always shows "not feasible".
   Add `"tail": "SIM-017"` to that constraint (my backend supports it) and it becomes the intended what-if (feasible, about $1,400 logistics on the golden fleet).
   Planning and Lab otherwise render correctly with the real solver: cost cards ($600 optimized vs $2,200 baseline), the Gantt, the replan diff with its "why", and the compare table.
