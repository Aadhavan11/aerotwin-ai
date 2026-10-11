# Role views: one console, arranged for the job

The console has a **"Viewing as"** switch at the top of the sidebar. A role changes three things: the page you land on, the navigation, and whether you see the approve / reject buttons.
Every role reads the same API and the same simulated data; nothing here needs a new backend endpoint. The choice is remembered in the browser.

| Role | Lands on | Built for | What it shows | Can approve work orders |
|---|---|---|---|---|
| **Operations control** (default) | Fleet overview, then **Control room** | Airline operations controllers and managers | Decisions waiting (pending work orders with evidence), the watch list ordered by conservative remaining life, the maintenance in the plan with its reason, the risk of waiting (optimized vs baseline), recent activity | Yes |
| **Maintenance planner** | **Work orders & resources** | Maintenance planners, engineers, technicians | Work-order board (pending / approved / rejected) with evidence and decision notes, technicians (skills, shifts, availability), parts stock and where missing parts ship from, hangar slots, jobs planned per station | Yes |
| **Pilot / flight crew** | **Aircraft readiness brief** | Pilots and flight crew | One aircraft in plain language: health banner, each monitored system with remaining life, planned maintenance and how many flights depart before it, next legs, what changed recently. Advisory only, with a note that the technical log and minimum equipment list stay authoritative | No (read-only note) |
| **Crew & dispatch** | **Departures & impact** | Crew schedulers and dispatchers | Departures for the next 12 to 72 h with a filter, aircraft needing attention, flights that touch a maintenance window (the plan keeps this at 0), and for each window the last flight before and the first flight after it | No (read-only note) |
| **Administrator** | **System & audit** | Platform administrators and reviewers | Which services run real code, import errors, data source, model quality (held-out error, interval coverage, anomaly precision / recall / false-positive rate), optimized vs baseline plan metrics, the full audit trail with filter and CSV export, scenario controls (advance the clock, reset) | No |

The existing screens (Fleet overview, Aircraft twin, Maintenance plan, Simulation lab, agent workspace) stay where they were and appear in the navigation of the roles that need them. The default role lands on the same Fleet overview as before, so the demo script is unchanged.

## What it does not do (be honest about it)
- **Roles are not authentication.** They choose what is shown and who sees the approve buttons; the API does not check who calls it. The approver name comes from the "Acting as" field in the agent workspace.
- **No crew roster, duty or rest data exists in the simulation**, so the crew view works from flights, aircraft status and maintenance windows only.
- **Pilots cannot report observations** in the prototype (no endpoint for it); the brief tells them to go to maintenance control.
- All data is simulated and labeled SIMULATED; nothing certifies an aircraft.

## Where the code is
`web/src/lib/role.tsx` (roles, navigation, permissions), `web/src/pages/{Control,Maintenance,FlightBrief,Dispatch,Admin}.tsx`, `web/src/components/WorkOrderCard.tsx`, the switcher in `web/src/App.tsx`.
