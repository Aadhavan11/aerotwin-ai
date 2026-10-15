# Model cards: simulator v2 component models

All models here are SIMULATED. They are demonstration surrogates, not validated predictors of any real component or fleet.
Read `docs/LIMITATIONS.md` first; each card repeats only what is specific to one model.

| Card | Kind | Code | ATA chapter | Data actually used |
|---|---|---|---|---|
| [engine](engine.md) | `engine` (2 per tail) | `app/sim/v2/models/engine.py` | 72 | NASA C-MAPSS FD001-FD004 (sensor curves, noise, life), rest assumed |
| [brakes](brakes.md) | `brakes` | `.../models/brakes.py` | 32 (32-42) | none (assumed) |
| [tires](tires.md) | `tires` | `.../models/tires.py` | 32 (32-41) | none (assumed) |
| [landing_gear](landing_gear.md) | `landing_gear` | `.../models/landing_gear.py` | 32 (32-11) | none (assumed) |
| [hydraulics](hydraulics.md) | `hydraulics` | `.../models/hydraulics.py` | 29 | UCI hydraulic rig: sensor-shape check only, 0 values changed |
| [electrical](electrical.md) | `electrical` | `.../models/electrical.py` | 24 | NASA 18650 cells: 3 values fitted (battery part only) |
| [avionics](avionics.md) | `avionics` | `.../models/avionics.py` | 34 | none (assumed) |
| [apu](apu.md) | `apu` | `.../models/apu.py` | 49 | none (assumed) |
| [ecs](ecs.md) | `ecs` | `.../models/ecs.py` | 21 | none (assumed) |

Shared code: `app/sim/v2/core.py` (registry, `Fleet`), `drivers.py` (operating context), `faults.py` (fault library),
`coupling.py` (cross-kind stress), `maintenance.py` and `world.py` (policy, removals), `app/taxonomy/ata.py` (ATA-style codes).
Fleet shape: one unit per tail per kind, except `engine` (2 per tail). Each unit is a lumped, per-tail aggregate.

## Legend: parameter provenance

Every parameter in the cards is placed in exactly one of three columns.

* **assumed**: a value chosen by the authors. The code comments now say "assumed (no citation)" (older text said "literature prior, unverified"); no specific
  citation is recorded in the repository, so treat them as engineering guesses of plausible order of magnitude.
  Nothing was compared with data. Most parameters are here.
* **sensor-shape-checked**: a public dataset was used to check that a sensor reacts to a condition with the sign and
  rough size the model assumes, or to estimate a noise floor. The value was not changed (or the dataset could not
  support a change). The check does not make the value a prediction of a fielded component.
* **calibrated**: the value was set by a committed fit script from public data (`app/calib/*`, output in
  `app/sim/v2/calibration/*.json`), and the model loads it. Calibrated here means "a number from a lab dataset",
  never "validated on aircraft data".

## Legend: validation

* **circular**: the model is scored against output of the same simulator (for example ML hold-out seeds in
  `docs/ML-GENERIC.md`). It shows internal consistency and learnability, not real-world accuracy.
* **in-sample**: scored against the data the fit used.
* **distributional**: marginal distributions compared (KS, Wasserstein, classifier AUC). Not a prediction test.
* No card claims out-of-sample validation against fielded aircraft data, because no such data is in the repository.

## Conventions

* "values: see PARAMS in <file>" means numbers are deliberately not repeated here because they are tuned and may move.
* Health index `hi` in [0, 1] (1 new, 0 failed). `failed` latches until a maintenance action recomputes it.
* Maintenance actions: `inspect`, `repair` (imperfect, keeps fraction `q`), `overhaul`, `replace`
  (`DEFAULT_Q` in `app/sim/v2/maintenance.py`: repair 0.6, overhaul 0.15, replace 0.0; see each card for what `q` multiplies).
  In `World._do`, any non-inspect action also clears all active hidden faults of that unit (`FaultInjector.clear`),
  independent of `q`.
* Hidden faults (`GroundTruthFault`) are never written into telemetry. Sensor faults (`app/sim/sensor_faults.py`,
  dropout, stuck, drift, spike, noise) are a separate layer applied after emission.
* Channels listed in a card are v2 emitted channels. The live service and the ML layer use only two per kind
  (`TELEMETRY_KEYS` in `app/ml/sensors.py`).

## Reproduce (all kinds)

```
~/.claude/bin/qrun pytest -q tests/test_sim_v2_<kind>.py        # per-kind model tests
~/.claude/bin/qrun pytest -q tests/test_sim_v2_coupling.py tests/test_sim_v2_faults.py tests/test_sim_v2_removal_bands.py
~/.claude/bin/qrun pytest -q tests/test_calib_engine.py tests/test_calib_hydraulic.py tests/test_calib_battery.py
python -m app.ml.train_generic          # sim-trained ML models (circular evaluation)
```
