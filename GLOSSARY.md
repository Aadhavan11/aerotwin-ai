# Glossary (so nobody has to be an aviation or ML person)

## Aircraft and operations

| Term | Meaning |
|---|---|
| **Tail number** | The registration of one specific aircraft. We use fictional `SIM-001`..`SIM-024`. |
| **Cycle** | One flight (takeoff to landing). Engines and landing gear wear per cycle, so RUL is counted in cycles. |
| **Rotation** | The chain of flights one aircraft flies over a few days. |
| **Ground window** | The time an aircraft sits at an airport between an arrival and its next departure. Maintenance fits into these. |
| **Station / hub** | An airport where maintenance can happen (has a hangar, technicians and parts). |
| **Hangar slot** | How many aircraft a station can work on at once. 0 = closed. |
| **AOG** | Aircraft on ground: grounded and costing money. |
| **OCC / MCC** | Airline operations control center / maintenance control center: where these decisions are made. |
| **Delay vs extension** | If maintenance runs past the ground window, the next flight is delayed. We call the extra hours the *extension*. |

## Maintenance

| Term | Meaning |
|---|---|
| **Component** | A maintainable part group: engine, brakes, landing gear, hydraulics, avionics. |
| **Task / work order** | A piece of maintenance work (what, how long, which skill, which part, deadline). A work order is a task proposed for human approval. |
| **Deadline** | The latest time a task may be done. Ours is derived from the conservative RUL estimate and is a **hard** constraint. |
| **Technician skills** | `engine`, `gear`, `hydraulics`, `avionics`. A task needs a technician with the matching skill on shift. |
| **Spare part** | A stocked item (e.g. `BORESCOPE-KIT`). It may be on hand, delayed, or shippable from another station at a cost. |
| **Borescope inspection** | Looking inside an engine with a camera probe. Our golden task. |
| **ATA chapter** | Standard numbering of aircraft systems (e.g. 32 landing gear, 72 engine). Used loosely in the synthetic knowledge base. |
| **MEL** | Minimum Equipment List: which broken items an aircraft may fly with, and for how long. Out of scope, mentioned only for context. |
| **Digital twin** | A software model of one specific aircraft whose state follows its flights, sensors and maintenance. |
| **Advisory only** | The system recommends; an authorized person approves. It does not replace approved procedures or airworthiness assessments. |

## Machine learning

| Term | Meaning |
|---|---|
| **NASA C-MAPSS** | Public simulated turbofan-engine run-to-failure datasets (sensors per cycle until failure). Our training and replay data. |
| **RUL** | Remaining useful life: cycles left before a component needs attention. We report **p10 / p50 / p90** (pessimistic / middle / optimistic). |
| **p10 (conservative)** | The pessimistic RUL (only 10% chance it is lower). Deadlines use this. |
| **Anomaly detection** | Flagging behavior that looks unlike normal. We use an **Isolation Forest**. |
| **Random Forest / XGBoost** | Tree-based models for predicting RUL from sensor features. |
| **Health score** | 0 to 1 summary of a component (1 = new, 0 = at the failure threshold). |
| **Data quality check** | Flags for flat sensors, out-of-range values, missing data or too little history. |
| **MAE / RMSE** | Average error / error that punishes big misses, in cycles. Precision / recall / false-positive rate describe the anomaly flags. |

## Optimization, simulation and agents

| Term | Meaning |
|---|---|
| **OR-Tools CP-SAT** | Google's constraint solver. It picks the station, time and technician for each task while respecting every constraint. |
| **Hard vs soft constraint** | Hard (deadlines, skills, parts, capacity) can never be broken. Soft ones (downtime, cost) are traded off in the objective. |
| **Baseline** | The comparison policy: do each task at the home base in the last window before its deadline (a fixed schedule). |
| **Discrete-event simulation** | Simulating a system as a sequence of events (flights, jobs) to measure downtime and delays. |
| **What-if / replan** | Change something (delay a part, close a hangar), recompute the plan, and show the difference. |
| **Agent** | An AI role with a goal and a set of tools. Ours: Health, Scheduling, Logistics, Operations, Coordinator. |
| **Tool calling** | The LLM asks our code to run a function and reads the result. Numbers come from the function, not from the LLM. |
| **Scripted mode** | The same tools run in a fixed order without an LLM. The demo must work this way. |
| **Evidence** | Links from a recommendation to the data that produced it (tool calls, plans, knowledge-base items). |
| **Level 1 / 2 / 3** | Stub with the right shape / real implementation / polish (`INTEGRATION.md` section 3). |
| **C0..C3** | Checkpoints in the schedule (`TASK-BOARD.md`). |
| **Golden scenario** | The one scripted story we test and demo: SIM-017 engine anomaly, plan, delayed part, replan. |
