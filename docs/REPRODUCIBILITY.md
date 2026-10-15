# Reproducibility manifests

Every completed run extends its existing export `manifest.json` with a
`reproducibility` object. Existing export keys and telemetry, removals, ground
truth and events files retain their formats. Reliability packs add a `manifest`
object with the same stamp, excluding `created_at` for stable comparisons.
Reliability cache version 4 includes `params_hash` in its configuration key.

Each stamp contains:

- `schema_version`: manifest schema version (currently 1).
- `created_at`: UTC ISO-8601 creation time (run exports only).
- `git`: commit `sha` and tracked-file `dirty` flag; unknown on git failure.
- `versions`: Python, numpy, scipy and sklearn distribution versions.
- `flags`: `SIM_ENGINE` and `HEALTH_MODEL` selections.
- `seed`: configured seed, overridden with the actual seed for run exports.
- `params_hash`: full SHA-256 of canonical JSON containing every model's
  module-level `PARAMS`, world `POLICY`, `FALSE_CALL`, `INFANT`, and the explicit
  `SIMULATION_CONSTANTS` listed in `app/sim/v2/manifest.py`. These include time
  scales, engine life and LLP limits, world maintenance thresholds and reliability
  analysis parameters. ML artifacts and timestamps do not enter this hash.
- `calibration`: calibration JSON file SHA-256 hashes, keyed by filename.
- `ml_artifacts`: generic joblib and metadata/evaluation file SHA-256 hashes,
  keyed by repository-relative POSIX path. Missing expected files have null hashes.
  Any engine `eval.json` found under `app/sim/v2` is included.
- `data_provenance`: NASA C-MAPSS or synthetic data source, or `unknown`.
- `provenance`: always `SIMULATED`.

Run stamps also carry `run_id`, `fleet_size` (actual tail count), and `years`
(from the stored run request). Reliability stamps carry `basis`, `years`, and
`cache_key`, together with the configured seed.

## Verify a run

Use the same checkout, environment, calibration files and ML artifacts. Compare
versions, flags, seed and run dimensions, then recompute the parameter and file
hashes. For example, replace the export directory below:

```python
import json
from pathlib import Path
from app.sim.v2.manifest import calibration_hashes, file_hashes, params_hash

stamp = json.loads(Path("data/sim_runs/RUN_ID/manifest.json").read_text())["reproducibility"]
assert params_hash() == stamp["params_hash"]
assert calibration_hashes() == stamp["calibration"]
assert file_hashes(stamp["ml_artifacts"]) == stamp["ml_artifacts"]
```

The stamp fingerprints inputs; the existing export `state_hash` fingerprints
simulation state. Re-run with the recorded spec to compare state and outputs.
For manifest and integration checks:

```sh
~/.claude/bin/qrun ruff check .
~/.claude/bin/qrun pytest -q tests/test_manifest.py tests/test_sim_v2_runner.py tests/test_reliability_pack.py
```

## Limits

Cross-platform floating-point differences in numpy/scipy/sklearn and different
BLAS implementations can affect results. Joblib artifacts are Python pickles tied
to sklearn versions; hashes identify their bytes rather than ensuring portability.
The git SHA identifies only the commit; the dirty flag does not capture untracked
files or record the contents of modifications. Wall-clock `created_at` differs by
design and is removed by `stable_view`. Python hash randomisation is not involved
in parameter hashing because sorted canonical JSON is used. The stamp does not
include raw dataset hashes or promise equivalence of different dataset contents.
