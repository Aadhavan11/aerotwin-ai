# Maintenance requests, the calendar and editing

Added 2026-10-11 on request ("a maintenance request summary ... then that gives you the approve/deny", "a daily by-the-hour calendar ... people can also go manually input").

* **The request** (`web/src/components/MaintenanceRequest.tsx`): one card with the name, what is damaged or being looked at (component, status, health, conservative life), the work to be
  done, the part and whether it has to be ordered (stock at the station, shipping), when and where (station, technician, time), why it is needed (the task's basis, the deadline and margin,
  flights before the fix, evidence), and an estimated cost. Lists show it as a headline that opens in full; a request waiting for a decision opens in full.
* **Deciding and editing** (operations control and maintenance planners; the server enforces it): Approve, Deny, or **Edit request**: name, work, part, quantity, "needs to be ordered", and the time.
  A changed time is checked against the task's deadline and duration, the aircraft being on the ground at that station for the whole time (the planner's own rule, with its short extension
  allowance) and the technician's shift, and is refused with the reason if it fails. Only a pending request can be edited. Every edit and decision is in the audit trail with who and what changed.
  API: `PATCH /workorders/{id}`, `GET /workorders/{id}/options` (the limits), `POST /workorders/{id}/decision`.
* **Entering a request by hand**: "New request" on the Maintenance plan page, or click an empty hour in an aircraft's column. Aircraft, station, time, name, work, reason, part. The same timing
  check applies (the aircraft must be on the ground there) and it waits for approval like any other. API: `POST /workorders`. Manual requests are recorded, approved and audited; they do not
  reset the twin's component health when they finish (only requests that come from a planner task do).
* **The calendar** (`MaintenanceCalendar.tsx`): a day view by the hour, one column per aircraft, flights in grey, jobs in colour (proposed by the plan, waiting for a decision, approved, entered by hand,
  static baseline hatched), a line for the simulation clock, six days, "Add an aircraft" for columns with no job yet. A job shows the time of its request if one exists (an edited time moves it).
