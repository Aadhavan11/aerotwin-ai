# AeroTwin demo: three minutes

This is the team's single pitch: the Judging Expo gives the whole team **3 minutes plus 2 minutes of Q&A** at a table with 2 judges (see [`HACKLANTA-RULES.md`](HACKLANTA-RULES.md)). Rehearse on `main` with `MOCK=0`, the NASA FD001 files downloaded and the shared API/web app running. Keep the SIMULATED badge visible. Do not present the copilot's mock approvals as live decisions.

| Time | Click / action | Say |
|---|---|---|
| 0:00–0:25 | Fleet; show the module status and aircraft | “This is a simulated fleet. We estimate component health and help a person choose where and when maintenance can fit.” |
| 0:25–0:45 | Simulation Lab → Reset to golden → Advance simulation 24 h → Inject engine anomaly (SIM-017) | “The simulator replays flight cycles and sensor data. Here we introduce a late-life engine scenario.” |
| 0:45–1:20 | Fleet → SIM-017 → Engine 2 | “The twin connects the fault to its sensor trend, health estimate and remaining-life uncertainty. Planning uses the conservative p10 estimate.” Read the actual numbers shown. |
| 1:20–1:55 | Copilot & agents → Agent activity → scripted → Run agents; then Planning | “These agents use deterministic tools to investigate, check resources and draft maintenance proposals. DFW has the kit, technician and a long enough ground window.” Confirm the plan is feasible and read its explanation. |
| 1:55–2:30 | Simulation Lab → Delay borescope kit at DFW by 36 h; inspect moved task | “When the part is delayed, the first slot becomes invalid. The optimizer proposes a new slot while keeping the maintenance deadline hard.” Show before/after slots from the returned diff. |
| 2:30–3:00 | Planning → baseline comparison, then scroll to "Risk while the fault is open" and read the hours until fixed, flights flown with the fault open and margin (optimized vs baseline) | “These are computed results for this simulated scenario. A human still has to approve a work order; this prototype does not certify an aircraft or execute maintenance.” |

Preparation: run `.venv/bin/python -m scripts.simulator_integration_demo`. Use its measured report to check expected behavior, not as a promise that every scenario has the same numbers. The earlier verified report had conservative RUL 23.4 cycles, one pending proposal, and a feasible replan that moved DFW service by one day. Re-run before quoting any result.

Backup recording: capture the same flow only after visual rehearsal. The recording and rehearsal are not yet completed. The live copilot backend, persisted human approval and any free-form LLM interaction are separate team work.
