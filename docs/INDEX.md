# Reviewer reading order

Everything in this repository is SIMULATED. No airline, manufacturer or regulator data is used, part numbers and limits are
fictional or assumed, and nothing is for airworthiness decisions. Read in this order; each step says what you will learn.

| # | Document | Why read it |
|---|---|---|
| 1 | [`README.md`](../README.md) | What the project is, how to run it, the scope statement |
| 2 | [`LIMITATIONS.md`](LIMITATIONS.md) | What no number here can tell you; data actually used; claim / evidence / strength table |
| 3 | [`model-cards/README.md`](model-cards/README.md), then the cards ([engine](model-cards/engine.md), [brakes](model-cards/brakes.md), [tires](model-cards/tires.md), [landing_gear](model-cards/landing_gear.md), [hydraulics](model-cards/hydraulics.md), [electrical](model-cards/electrical.md), [avionics](model-cards/avionics.md), [apu](model-cards/apu.md), [ecs](model-cards/ecs.md)) | Per model: mechanism, parameter provenance (assumed / sensor-shape-checked / calibrated), known limits |
| 4 | [`SIM-V2-ENGINE.md`](SIM-V2-ENGINE.md), [`SIM-V2-CALIBRATION.md`](SIM-V2-CALIBRATION.md), [`SIM-V2-SERVICE.md`](SIM-V2-SERVICE.md) | Engine surrogate and its (in-sample) validation; hydraulics and battery fits; live service, runner, maintenance semantics |
| 5 | [`RELIABILITY.md`](RELIABILITY.md) | Reliability pack, assumed removal-rate bands, NFF and infant-mortality priors, constants |
| 6 | [`PARAMETERS.md`](PARAMETERS.md), [`SENSITIVITY.md`](SENSITIVITY.md), [`SCENARIOS.md`](SCENARIOS.md), [`ML-VALIDITY.md`](ML-VALIDITY.md), [`REPRODUCIBILITY.md`](REPRODUCIBILITY.md) | Parameter registry, sensitivity analysis, scenario sets, ML validity checks, manifests and reruns. These five are being added in the same review pass and may not exist yet in every checkout |
| 7 | [`ML-GENERIC.md`](ML-GENERIC.md), [`HEALTH.md`](HEALTH.md) | Sim-trained health models (circular evaluation, retrained 2026-10-14) and the C-MAPSS engine scorer |
| 8 | [`LLP.md`](LLP.md), [`MEL.md`](MEL.md), [`MONTECARLO.md`](MONTECARLO.md) | Life-limited parts and shop-visit forecast, MEL register, Monte-Carlo plan risk (all assumed priors) |

Other documents (concept, demo script, Devpost text, UI plans) are context, not evidence.

Two things that are easy to miss: the engine model's single LLP counter and the four-LLP-per-engine tracker in `app/ops/llp.py`
do not read each other; and `World._do` clears all active hidden faults of a unit on any non-inspect maintenance action.
