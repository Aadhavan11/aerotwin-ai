# SIM-V2 live service, backfill runner and flags

All data is SIMULATED (`provenance='SIMULATED'` on every row, file and response).

## `SIM_ENGINE=v2` (env, default `v1`)
`app/settings.py: SIM_ENGINE`. When `v2`, `app.sim.service.Simulator` (and `IntegratedSimulator`, `PlatformSimulator`,
which inherit it) take telemetry from `app/sim/v2/live.py: V2Source`, a drop-in for `TelemetrySource`; v1 code paths are
unchanged and byte-identical when the flag is off (`tests/test_sim_v2_service.py::test_flag_off_is_byte_identical_to_v1`).
* Interface: `step(hours)` ingests one row per component per completed leg; `inject(ScenarioEvent)`; checkpoint/resume in the
  existing kv snapshot (`damage` of the source holds the world npz + counters + pending rows); restore on maintenance via
  `source.restore(cid)` = v2 `overhaul` (engine, apu, ecs, tires) or `replace` per `world.POLICY`.
* Contract: row values are exactly `TELEMETRY_KEYS[kind]`; extra v2 channels (egt margin, LLP, ...) and hidden truth stay inside.
* The world steps all tails one flight per world cycle; a tail's n-th completed leg consumes world cycle n.
  `World(auto_maintain=False)`: nothing is repaired unless a work order restores it.
* Initial state: non-engines start at wear `1 - health` of `fleet.py` (= `wear_profile` start fraction); engines at
  `cycles / (87 * 206.3)` capped at 0.6 (flights since overhaul over mean on-wing life). Realised by `World.pre_age`
  (units accelerated to their target health index, captured when reached; typical error < 0.01, max 0.04).
* `anomaly`: engine wear jumps to p = 0.72 + 0.16 x severity of its life axis (HPT erosion also logged in ground truth), so the
  RUL scorer sees a late-life engine (SIM-017/engine_2: p50 RUL ~116 -> ~22, falling after further steps); other kinds force
  their v2 fault mode (`live.ANOMALY_MODES`). `accelerated_degradation`: `stress_ext = 1 + 4 x severity` until maintained.
  Eight cycles are projected on a state copy so an event shows in telemetry immediately.
* Limits: the first `inject` on a never-stepped world and rows of the 8 projected cycles use projected, not stepped, state.

## Backfill runner (`app/sim/v2/runner.py`, routes in `runs_api.py`, mounted by `app/sim/router.py`)
| route | role | |
|---|---|---|
| `POST /sim/runs {years, seed, fleet?, initial_wear?, checkpoint_every?}` | ops, admin | 202 + run status |
| `GET /sim/runs`, `GET /sim/runs/{id}` | ops, admin | status, progress, cycles_done, eta_seconds |
| `POST /sim/runs/{id}/resume` | ops, admin | continue from the latest checkpoint |
| `GET /sim/runs/{id}/export` | ops, admin | telemetry: parquet if pyarrow/fastparquet importable, else `csv.gz` (concatenated gzip members) |

* Worker thread, off the request path; SQLite tables `sim_runs`, `sim_checkpoints` (`CREATE TABLE IF NOT EXISTS`, additive).
* Output `data/sim_runs/<id>/` (git-ignored via `data/`): `parts/telemetry_*.csv.gz`, `ckpt_*.npz`, `removals.csv.gz`,
  `ground_truth.json` (hidden truth), `events.json`, `manifest.json`. Override the base dir with `SIM_RUNS_DIR`.
* 1 run-year = 2,190 world cycles (6 legs/day via utilisation 2.0): 525,600 telemetry rows for 24 tails, ~18 s on a laptop.
  Checkpoints every `checkpoint_every` cycles (default 365); resume and re-runs are bit-identical.
* pyarrow is NOT in requirements.txt; csv.gz is used unless it is installed (untested branch here).
* Under `MOCK=1` the sim group is served by the mock router, which has no `/sim/runs`.
