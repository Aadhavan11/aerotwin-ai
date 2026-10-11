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

## Added later the same morning

* **Suggested actions** (Maintenance plan page, `GET /maintenance/suggestions`): work that is not pending or required now but is better planned ahead: components worn or trending toward their limit with no job yet,
  and routine checks falling due in the next three weeks, each with the first ground window that fits (a visit the aircraft already has planned comes first, marked "Same visit"). Advice only: "Plan it" opens a prefilled
  request to review; "Not now" hides it for the session.
* **The copilot can change requests, with your approval** (`app/copilot/actions.py`): ask in plain words to rename a request, change its time, set the part or mark it to be ordered, or create a new request
  ("create a maintenance request for SIM-003 brake replacement tomorrow at 14:00 because the pads are worn"). It answers with a proposal (what changes, from and to, already checked against the same rules as a person's
  edit) and Allow / Deny buttons. Nothing is changed until Allow; Allow sends the change as the signed-in person and the audit trail records them. The language-model path has the same two tools
  (`propose_request_edit`, `propose_new_request`) and can only propose. The copilot never approves or denies a request.
* **Aircraft columns on the calendar can be removed** with the x on the column header (a view choice kept in the browser; nothing is deleted from the fleet) and brought back with "Add an aircraft" or "Show the removed aircraft again".
* **Messages are real** (`app/auth/messages.py`, `/messages`): to a position, to one person, or (administrator and master) to everyone. Each recipient has their own read state: the bell in the top bar shows the number unread,
  opening a message turns its blue dot off and lowers the badge at once, "Mark all as read" clears it. The Administrator page has a "Send a message" box with the messages you sent and how many recipients read each.
  Messages need sign-in (they are not available in local runs without it).
* **Tablets**: the top bar wraps into two rows on iPad widths (the SIMULATED label stays), touch targets are at least 44 px and fields use 16 px text so iPadOS does not zoom, request and calendar layouts reflow.
  Checked in a browser at 820x1180, 1180x820 and 768x1024 on every page of every position: no page scrolls sideways. Not tested on a physical iPad.
* The row of five metrics under the heading was removed from the Maintenance plan page; those numbers are in "Optimized vs baseline" at the bottom.
