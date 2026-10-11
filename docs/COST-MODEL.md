# Estimated cost of a plan (simulated price list)

Added 2026-10-11. The Maintenance plan page used to show only "logistics cost" (parts shipped between stations), which is $0 whenever every part is on hand,
so the optimized-versus-baseline comparison looked empty. The plan now also carries an **estimated cost** (`PlanMetrics.total_cost_usd`, additive in the contract).

| Part of the cost | How it is counted | Value used |
|---|---|---|
| Parts | price of the part a job consumes x quantity | HYD-PUMP $38,000, AVIONICS-LRU $27,000, BRAKE-ASSY $14,500, BORESCOPE-KIT $2,400 (tool-crib charge per use) |
| Labor | job hours x 2 technicians x labor rate | $145 per technician hour |
| Bay time | job hours x hangar bay rate | $650 per hour |
| Shipping | the existing `logistics_cost_usd` (a part shipped in from another station) | from the station data |
| Delay | hours the job runs past its ground window x cost of an hour of delay | $5,000 per hour |
| Not done in time | an allowance for every task the plan cannot do before its deadline (late or unscheduled): the expected cost of the unplanned event that follows | $30,000 per task |

**These prices are a simulation parameter, not quotes** (`app/solver/costs.py`; the page says "from a simulated price list"). Two plans that do the same work pay the
same parts, labor and bay time. The optimized plan costs less than the static baseline because of shipping, delay and, above all, jobs the baseline cannot fit before
their deadline; when both do everything in time they tie. With no tasks the cost is $0.

Where it shows: Maintenance plan (KPI row, "Optimized vs baseline" metrics and cost breakdown), each maintenance request (estimate for that job), the copilot and
agent tools (`total_cost_usd` and the parts of it). The solver objective is unchanged (it still uses `logistics_cost_usd`), so plans are the same as before.
