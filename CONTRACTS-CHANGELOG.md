# Contracts changelog

One line per change to `CONTRACTS.md` / `app/contracts/models.py`: date time, who, what, who must update.

- Sat 2026-10-10 ~9 PM, initial version (Claude for Shamit). Added `AircraftSummary`, `FleetKpis`, `FleetOverview`, `Scenario`, `MetricsBundle`; `ml.cmapss.trajectories()` interface.
- Sun 2026-10-11, Pardhu (with Claude), NO model changes. Optional keyword arguments only, defaults keep the old behaviour:
  `ground_windows(..., aircraft=None)`, `solve(..., force_station=None, force_tails=None)`, `replan(..., weights=None, time_limit_s=5.0, already_applied=False, mode="cpsat")`,
  `simulate(..., tasks=None)`; `Scenario.constraints` accepts an optional `"tail"`. New helpers: `derive_tasks_ex`, `simulate_detailed`, `affected_legs`, `app.solver.store.STORE`,
  `app.whatif.metrics.plan_block`. Shamit: `app/contracts/models.py` in this branch is a verbatim copy of CONTRACTS.md section 2; keep yours. Extra endpoint: `GET /metrics/plan`.
