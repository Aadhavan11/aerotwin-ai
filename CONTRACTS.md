# CONTRACTS: the one spec every module follows

> **Owners changed Sat night (Shamit and Aadhavan swapped jobs). `work/OWNERSHIP.md` is the source of truth for who builds what; where this file names a different owner, follow OWNERSHIP.md. The specs themselves are unchanged.**

If two modules disagree, **this file wins**. Shamit publishes it as code (`app/contracts/models.py`) in the first 45 minutes;
everyone else imports from there and never redefines a model. Change a model only with a line in `CONTRACTS-CHANGELOG.md` and a
message to the group chat.

## 1. Conventions (violating any of these is a bug)

| Topic | Rule |
|---|---|
| IDs | Aircraft `SIM-001`..`SIM-024` (fictional, never real tails). Component `SIM-017/engine_2`. Task `T-SIM-017-engine_2-001`. Station = IATA code (`ATL`). Plan `P-<n>`. Event `E-<n>`. Agent run `R-<n>`. Work order `WO-<n>` |
| Time | UTC ISO-8601 with `Z`. All simulated time comes from `SimClock.now()`, **never `datetime.now()`** |
| Units | Durations in **hours (float)**, health/anomaly in **0..1**, RUL in **cycles (= flights)**, money in USD. Sensor units are documented per sensor in `app/ml/sensors.py` |
| Flag | Every record has `synthetic: true`. The UI shows a SIMULATED badge |
| Determinism | One master seed (`SEED=1234`). Same seed -> same fleet, telemetry, plans. Tests rely on it |
| Safety rule | A task deadline is derived from the **conservative** RUL quantile (p10). Code may move maintenance **earlier**, never later than the deadline. Infeasible -> say so, never silently relax a hard constraint |
| Numbers | Any number shown to a user comes from a function return value, not from an LLM |
| Errors | Services raise nothing across module boundaries for expected failures: return a result with `feasible=false` / `data_quality.status="bad"` and reasons |

## 2. Models (copy to `app/contracts/models.py`)

```python
from __future__ import annotations
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field

ComponentKind = Literal["engine", "brakes", "landing_gear", "hydraulics", "avionics"]
AircraftStatus = Literal["ok", "watch", "alert", "in_maintenance", "aog"]
EventType = Literal["anomaly", "accelerated_degradation", "part_delay", "tech_unavailable", "facility_closed"]
SolveMode = Literal["cpsat", "greedy", "static_baseline"]

class Stamped(BaseModel):
    synthetic: bool = True

# ---------- 3.1 health intelligence ----------
class RulEstimate(BaseModel):
    p10: float            # cycles; conservative
    p50: float
    p90: float

class DataQuality(BaseModel):
    status: Literal["ok", "degraded", "bad"]
    reasons: list[str] = []          # e.g. "sensor s11 flat for 20 cycles", "value out of training range"

class HealthScore(Stamped):
    tail: str
    component_id: str
    health: float = Field(ge=0, le=1)          # 1 = new, 0 = at failure threshold
    anomaly_score: float = Field(ge=0, le=1)   # 1 = very abnormal
    is_anomaly: bool
    rul_cycles: Optional[RulEstimate] = None
    data_quality: DataQuality
    model_version: str
    top_signals: list[str] = []                # sensors driving the score (explainability)

class TelemetryRow(Stamped):
    tail: str
    component_id: str
    ts: datetime
    cycle: int                                  # cumulative cycles of this component
    values: dict[str, float]                    # sensor name -> value

# ---------- 3.2 digital twin ----------
class Aircraft(Stamped):
    tail: str
    type: str                                   # "A320-class"
    home: str                                   # IATA
    location: str                               # IATA now, or "AIR"
    status: AircraftStatus
    cycles: int
    hours: float

class ComponentState(Stamped):
    tail: str
    component_id: str
    kind: ComponentKind
    cycles: int
    hours: float
    health: float
    anomaly_score: float
    rul_cycles: Optional[RulEstimate]
    data_quality: DataQuality
    last_maintenance: Optional[datetime] = None
    status: AircraftStatus
    updated: datetime

class HistoryPoint(BaseModel):
    cycle: int
    ts: datetime
    health: float
    anomaly_score: float
    rul_p50: Optional[float] = None

class PredictedPoint(BaseModel):                # future health curve with uncertainty
    cycle: int
    health_p50: float
    health_lo: float
    health_hi: float

class TwinEvent(BaseModel):
    ts: datetime
    kind: Literal["flight", "anomaly", "maintenance", "fault_injected", "plan_change"]
    summary: str
    ref: Optional[str] = None                   # id of the plan / event / work order

class TwinComponent(ComponentState):
    history: list[HistoryPoint]
    predicted: list[PredictedPoint]
    sensors_last: dict[str, float] = {}

class TwinView(Stamped):
    aircraft: Aircraft
    components: list[TwinComponent]
    timeline: list[TwinEvent]

# ---------- operations data ----------
class Leg(Stamped):
    id: str
    tail: str
    dep: str
    arr: str
    sched_dep: datetime
    sched_arr: datetime

class GroundWindow(BaseModel):
    tail: str
    station: str
    start: datetime
    end: datetime
    before_leg: Optional[str] = None            # the next leg this window precedes
    @property
    def hours(self) -> float: return (self.end - self.start).total_seconds() / 3600

class Technician(BaseModel):
    id: str
    skills: list[str]                           # "engine", "gear", "hydraulics", "avionics"
    shift_start: datetime
    shift_end: datetime
    available: bool = True

class PartStock(BaseModel):
    part: str
    qty: int
    eta_hours: float = 0.0                      # 0 = on hand; >0 = hours until stock arrives (delivery delay)
    ship_from: Optional[str] = None             # station that could ship it
    ship_hours: float = 12.0
    ship_cost_usd: float = 800.0

class Station(Stamped):
    iata: str
    hangar_slots: int                           # simultaneous maintenance jobs; 0 = closed
    technicians: list[Technician]
    parts: list[PartStock]
    lat: float
    lon: float

class FleetState(BaseModel):                    # immutable snapshot passed into solver / what-if
    now: datetime
    aircraft: list[Aircraft]
    components: list[ComponentState]
    legs: list[Leg]
    stations: list[Station]

# ---------- fleet overview / what-if request / metrics ----------
class AircraftSummary(BaseModel):
    tail: str
    type: str
    home: str
    location: str
    status: AircraftStatus
    worst_component: Optional[str] = None       # component_id with the lowest rul_p10
    rul_p10_min: Optional[float] = None

class FleetKpis(BaseModel):
    aircraft: int
    needing_attention: int                      # status alert or watch
    predicted_maintenance_72h: int              # tasks with deadline inside 72 h
    in_maintenance: int
    readiness_pct: float                        # aircraft not in_maintenance/aog, percent

class FleetOverview(Stamped):
    kpis: FleetKpis
    aircraft: list[AircraftSummary]
    stations: list["Station"]

class Scenario(BaseModel):
    name: str
    events: list["ScenarioEvent"] = []
    constraints: dict = {}                      # {"force_station": "DFW", "earliest": "<iso>"}

class MetricsBundle(BaseModel):
    ml: dict                                    # from app/ml/artifacts/eval.json: rul_mae, rul_rmse, anomaly_precision, anomaly_recall, anomaly_fpr, n_test, data_source
    plan: dict                                  # {"baseline": PlanMetrics, "optimized": PlanMetrics, "replan_seconds": float}

# ---------- 3.3 scheduling ----------
class Task(Stamped):
    id: str
    tail: str
    component_id: str
    title: str
    duration_h: float
    earliest: datetime
    deadline: datetime                          # HARD, from conservative RUL (or fixed schedule)
    required_skill: str
    required_part: Optional[str] = None
    part_qty: int = 1
    source: Literal["rul", "scheduled", "manual"]
    basis: str                                  # human-readable why

class Assignment(BaseModel):
    task_id: str
    tail: str
    station: str
    start: datetime
    end: datetime
    technician_id: Optional[str]
    part_source: Optional[str] = None           # station the part is shipped from, if not on hand
    window_hours: float                         # size of the ground window used
    extension_h: float = 0.0                    # hours beyond the planned ground time (causes a delay)
    reason: str                                 # why this slot beat the alternatives

class PlanMetrics(BaseModel):
    downtime_h: float                           # aircraft hours out of service beyond planned ground time
    flights_affected: int
    logistics_cost_usd: float
    tech_utilization: float                     # 0..1 over the horizon
    hangar_utilization: float
    schedule_changes: int                       # vs the previous plan
    tasks_total: int
    tasks_scheduled: int
    late_or_unscheduled: int                    # must be 0 for a feasible plan

class ObjectiveWeights(BaseModel):
    downtime: float = 10.0                      # alpha
    disruption: float = 25.0                    # beta, per affected flight
    logistics: float = 0.01                     # gamma, per USD
    changes: float = 2.0                        # delta, per changed assignment
    # lateness is not a weight: deadlines are hard constraints

class Plan(Stamped):
    id: str
    created: datetime
    mode: SolveMode
    feasible: bool
    infeasible_reasons: list[str] = []          # e.g. "T-... no window with an engine technician before deadline"
    assignments: list[Assignment]
    metrics: PlanMetrics
    objective_value: Optional[float] = None
    solve_seconds: float
    evidence: list["EvidenceRef"] = []

# ---------- 3.5 what-if ----------
class ScenarioEvent(Stamped):
    id: str = ""
    type: EventType
    at: datetime
    tail: Optional[str] = None                  # anomaly / accelerated_degradation
    component_id: Optional[str] = None
    station: Optional[str] = None               # part_delay / tech_unavailable / facility_closed
    part: Optional[str] = None
    delay_hours: Optional[float] = None
    technician_id: Optional[str] = None
    severity: Optional[float] = None            # 0..1 for anomaly strength
    note: str = ""

class PlanDiff(BaseModel):
    moved: list[dict]                           # [{task_id, from: {station,start}, to: {station,start}, why}]
    added: list[str]
    dropped: list[str]

class ReplanResult(Stamped):
    event: ScenarioEvent
    before: Plan
    after: Plan
    diff: PlanDiff
    solve_seconds: float

class Comparison(BaseModel):
    rows: list[dict]                            # [{name, feasible, metrics: PlanMetrics}]

# ---------- 3.4 agents / 3.6 copilot ----------
class EvidenceRef(BaseModel):
    kind: Literal["tool_call", "health", "plan", "kb", "event"]
    ref: str                                    # tool_call id, component_id, plan id, kb doc id, event id
    note: str = ""

class AgentStep(BaseModel):
    n: int
    agent: Literal["health", "scheduling", "logistics", "operations", "coordinator"]
    kind: Literal["thought", "tool_call", "tool_result", "summary"]
    tool: Optional[str] = None
    input: Optional[dict] = None
    output: Optional[dict] = None
    text: str = ""
    ts: datetime

class WorkOrder(Stamped):
    id: str
    tail: str
    task_id: str
    station: str
    start: datetime
    end: datetime
    description: str
    status: Literal["pending_approval", "approved", "rejected"] = "pending_approval"
    plan_id: str
    evidence: list[EvidenceRef]
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    note: str = ""

class AgentRun(Stamped):
    id: str
    goal: str
    mode: Literal["llm", "scripted"]
    status: Literal["running", "done", "error"]
    steps: list[AgentStep]
    plan: Optional[Plan] = None
    work_orders: list[WorkOrder] = []
    explanation: str = ""                       # may be LLM-written; numbers inside must exist in tool outputs
    missing_info: list[str] = []

class ChatRequest(BaseModel):
    session_id: str
    message: str

class ChatResponse(Stamped):
    answer: str
    evidence: list[EvidenceRef]
    missing_info: list[str] = []
    proposed_work_orders: list[WorkOrder] = []
    run_id: Optional[str] = None

class AuditEntry(BaseModel):
    ts: datetime
    actor: str                                  # "system" | "agent:coordinator" | user name
    action: str                                 # "plan_created" | "work_order_approved" | ...
    ref: str
    detail: str = ""

# Some models refer to classes defined later (string annotations). Resolve them once, at the END of models.py:
for _m in (Plan, FleetOverview, Scenario):
    _m.model_rebuild()
```

(Tested 2026-10-10 with pydantic 2.14: all 39 models define and rebuild, and every JSON example below validates.)

## 3. Database (SQLite, `data/aerotwin.db`, created by `app/db.py:init_db()`)

Plain `sqlite3` + pydantic, no ORM. JSON columns hold the pydantic `.model_dump_json()`.

| Table | Columns |
|---|---|
| `aircraft` | `tail PK, type, home, location, status, cycles, hours, json` |
| `components` | `tail, component_id, kind, state_json, PRIMARY KEY(tail, component_id)` |
| `telemetry` | `id PK, tail, component_id, cycle, ts, values_json` (index `(tail, component_id, cycle)`) |
| `health_history` | `tail, component_id, cycle, ts, health, anomaly, rul_p50` |
| `legs` | `id PK, tail, dep, arr, sched_dep, sched_arr` |
| `stations` | `iata PK, json` (includes technicians, parts) |
| `tasks` | `id PK, plan_id NULL, json` |
| `plans` | `id PK, mode, feasible, created, json` |
| `events` | `id PK, type, at, json` |
| `agent_runs` | `id PK, status, json` (steps appended live) |
| `work_orders` | `id PK, status, json` |
| `audit` | `id PK, ts, actor, action, ref, detail` |
| `kv` | `key PK, value` (sim clock, current plan id, scenario name) |

`scripts/reset_db.py --scenario golden` rebuilds everything from `SEED` and the golden fixtures in under 10 seconds.

## 4. HTTP API (FastAPI, `http://localhost:8000`, OpenAPI at `/openapi.json`)

All responses are JSON of the models above. Errors: `{"error": "...", "detail": ...}` with a 4xx/5xx status. The front end generates its
TypeScript types with `npx openapi-typescript http://localhost:8000/openapi.json -o web/src/api/types.ts`.

| Method + path | Request | Response | Owner |
|---|---|---|---|
| `GET /health` | | `{ok, mock, version, sim_now}` | Shamit |
| `GET /fleet` | | `FleetOverview` | Shamit |
| `GET /aircraft/{tail}` | | `TwinView` | Aashirth |
| `GET /aircraft/{tail}/telemetry?component=&last=60` | | `TelemetryRow[]` | Aashirth |
| `GET /legs?tail=` | | `Leg[]` | Shamit |
| `GET /plan` | | `{current: Plan, baseline: Plan, compare: Comparison}` | Pardhu |
| `POST /plan/solve` | `{mode: SolveMode, weights?: ObjectiveWeights}` | `Plan` | Pardhu |
| `POST /plan/replan` | `{event: ScenarioEvent}` | `ReplanResult` | Pardhu |
| `POST /whatif/compare` | `{scenarios: Scenario[]}` | `Comparison` | Pardhu |
| `POST /scenario/inject` | `ScenarioEvent` | `{event_id, effects: [...], twin_changes: [...]}` | Shamit |
| `POST /scenario/reset` | `{name: "golden"}` | `{ok}` | Shamit |
| `POST /sim/step` | `{hours}` | `{sim_now, ingested}` | Shamit |
| `POST /agents/run` | `{goal, scope: {tails?: [], horizon_h?}, mode: "llm"|"scripted"}` | `AgentRun` (returns immediately with `running`; poll) | Shamit |
| `GET /agents/runs/{id}` | | `AgentRun` (steps grow while running) | Shamit |
| `POST /copilot/chat` | `ChatRequest` | `ChatResponse` | Shamit |
| `GET /workorders` | | `WorkOrder[]` | Shamit |
| `POST /workorders/{id}/decision` | `{decision: "approve"|"reject", user, note}` | `WorkOrder` | Shamit |
| `GET /audit` | | `AuditEntry[]` | Shamit |
| `GET /metrics` | | `MetricsBundle` | Aashirth (ml) + Pardhu (plan); Shamit merges |

### Example payloads (these are also the files in `fixtures/`)

`GET /aircraft/SIM-017` (trimmed):

```json
{
  "synthetic": true,
  "aircraft": {"tail": "SIM-017", "type": "A320-class", "home": "ATL", "location": "ATL", "status": "alert", "cycles": 8123, "hours": 14210.5},
  "components": [{
    "tail": "SIM-017", "component_id": "SIM-017/engine_2", "kind": "engine", "cycles": 231, "hours": 410.2,
    "health": 0.18, "anomaly_score": 0.91, "status": "alert",
    "rul_cycles": {"p10": 18, "p50": 22, "p90": 25},
    "data_quality": {"status": "ok", "reasons": []},
    "updated": "2026-10-12T12:00:00Z",
    "history": [{"cycle": 200, "ts": "2026-10-09T12:00:00Z", "health": 0.41, "anomaly_score": 0.30, "rul_p50": 51}],
    "predicted": [{"cycle": 232, "health_p50": 0.17, "health_lo": 0.15, "health_hi": 0.19}],
    "sensors_last": {"T50": 1431.2, "Nc": 9061.4, "Ps30": 47.2}
  }],
  "timeline": [{"ts": "2026-10-12T12:00:00Z", "kind": "anomaly", "summary": "engine_2 abnormal temperature trend", "ref": "E-1"}]
}
```

`POST /scenario/inject`:

```json
{"type": "anomaly", "at": "2026-10-12T12:00:00Z", "tail": "SIM-017", "component_id": "SIM-017/engine_2", "severity": 0.9}
```

`POST /plan/replan` (the disruption):

```json
{"event": {"type": "part_delay", "at": "2026-10-12T14:00:00Z", "station": "DFW", "part": "BORESCOPE-KIT", "delay_hours": 12}}
```

`GET /plan` -> `current` (trimmed):

```json
{"id": "P-2", "mode": "cpsat", "feasible": true, "infeasible_reasons": [],
 "assignments": [{"task_id": "T-SIM-017-engine_2-001", "tail": "SIM-017", "station": "DFW",
   "start": "2026-10-13T03:00:00Z", "end": "2026-10-13T09:00:00Z", "technician_id": "DFW-T2",
   "part_source": null, "window_hours": 9.0, "extension_h": 0.0,
   "reason": "Only window before the 4.5-day deadline with an engine technician, a borescope kit on hand, and 0 h of extension."}],
 "metrics": {"downtime_h": 0.0, "flights_affected": 0, "logistics_cost_usd": 0, "tech_utilization": 0.12,
   "hangar_utilization": 0.08, "schedule_changes": 0, "tasks_total": 5, "tasks_scheduled": 5, "late_or_unscheduled": 0},
 "solve_seconds": 0.8, "synthetic": true}
```

(The numbers above are shape examples, not results. Real numbers come from running the code.)

## 5. Python service interfaces (modules call each other **only** through these)

```python
# app/ml/health.py                               [Aashirth]  3.1
def score_engine(tail: str, component_id: str, window: list[TelemetryRow]) -> HealthScore
def score_generic(kind: ComponentKind, tail: str, component_id: str, window: list[TelemetryRow]) -> HealthScore
def evaluate() -> dict            # MAE, RMSE, precision, recall, fpr on held-out data; cached to app/ml/artifacts/eval.json

# app/ml/cmapss.py + sensors.py                  [Aashirth]  used by Shamit's simulator
def trajectories() -> dict[int, list[dict]]       # unit -> ordered rows {cycle, values: {sensor_name: float}, true_rul}; synthetic fallback if no dataset
SENSOR_NAMES: list[str]                           # exact keys used in TelemetryRow.values for engines (T24, T30, T50, P30, Nf, Nc, Ps30, phi, NRf, NRc, BPR, htBleed, W31, W32)

# app/twin/state.py                              [Aashirth]  3.2 backend
def ingest_telemetry(row: TelemetryRow) -> ComponentState          # stores, scores (calls ml), updates history + status
def apply_event(event: ScenarioEvent) -> list[TwinEvent]           # anomaly / accelerated degradation change the telemetry source
def apply_maintenance(tail: str, component_id: str, at: datetime) -> ComponentState   # resets health
def get_twin(tail: str) -> TwinView
def snapshot() -> FleetState                                       # includes legs/stations from db

# app/solver/*.py                                [Pardhu]    3.3
def ground_windows(legs: list[Leg], now: datetime) -> list[GroundWindow]
def derive_tasks(state: FleetState) -> list[Task]                  # from RUL p10 + scheduled items
def solve(state: FleetState, tasks: list[Task], weights: ObjectiveWeights, mode: SolveMode, time_limit_s: float = 5.0, previous: Optional[Plan] = None) -> Plan

# app/whatif/*.py                                [Pardhu]    3.5
def apply_event_to_state(state: FleetState, event: ScenarioEvent) -> FleetState    # pure, returns a modified copy
def simulate(plan: Plan, state: FleetState, runs: int = 1, seed: int = 0) -> PlanMetrics
def replan(state: FleetState, prior: Plan, event: ScenarioEvent) -> ReplanResult
def compare(state: FleetState, plans: dict[str, Plan]) -> Comparison

# app/agents/*.py                                [Shamit]    3.4
def run_workflow(goal: str, scope: dict, mode: Literal["llm", "scripted"]) -> AgentRun
TOOLS: dict[str, ToolSpec]        # see section 6

# app/copilot/chat.py                            [Shamit]    3.6
def chat(req: ChatRequest) -> ChatResponse
def decide(work_order_id: str, decision: str, user: str, note: str) -> WorkOrder
```

## 6. Agent tools (all deterministic; the LLM only chooses and explains)

Each tool returns JSON-serializable data and an `evidence` reference. Names are stable: prompts and tests use them.

| Agent | Tool | Input | Output | Calls |
|---|---|---|---|---|
| Health | `list_at_risk(horizon_cycles)` | int | `[{tail, component_id, health, rul_p10, is_anomaly}]` sorted by risk | `twin.snapshot` |
| Health | `get_component_health(component_id)` | str | `ComponentState` | `twin` |
| Health | `get_sensor_trend(component_id, last)` | str, int | `[{cycle, values}]` | `db` |
| Health | `get_maintenance_history(tail)` | str | `[TwinEvent]` | `db` |
| Scheduling | `find_ground_windows(tail, horizon_h)` | str, float | `[GroundWindow]` | `solver.ground_windows` |
| Scheduling | `derive_tasks()` | | `[Task]` | `solver.derive_tasks` |
| Scheduling | `solve_schedule(mode, weights?)` | | `Plan` | `solver.solve` |
| Scheduling | `compare_plans(names)` | | `Comparison` | `whatif.compare` |
| Logistics | `check_parts(station, part)` | str, str | `{on_hand, eta_hours, ship_from, ship_cost}` | `db` |
| Logistics | `match_technicians(station, skill, start, end)` | | `[Technician]` | `db` |
| Logistics | `shortage_report()` | | `[{task_id, missing: "part"|"technician"|"hangar", detail}]` | `solver` |
| Operations | `simulate_plan(plan_id)` | | `PlanMetrics` | `whatif.simulate` |
| Operations | `flights_affected(plan_id)` | | `[Leg]` | `whatif` |
| Operations | `replan_with_event(event)` | `ScenarioEvent` | `ReplanResult` | `whatif.replan` |
| Coordinator | `draft_work_orders(plan_id)` | | `[WorkOrder]` (`pending_approval`) | `copilot` |
| Coordinator | `search_kb(query)` | str | `[{doc_id, title, excerpt}]` | `kb/` |

## 7. Fixtures (committed in `fixtures/`; created by Shamit at C0, refined by owners)

| File | Content | Used by |
|---|---|---|
| `fleet.json` | 24 aircraft, statuses | UI, solver tests |
| `stations.json` | 8 stations with technicians, parts, hangar slots | solver, agents, UI |
| `legs_golden.json` | rotations; SIM-017 has a >= 6 h ground window at DFW in the golden window | solver, what-if |
| `twin_SIM-017.json` | the `TwinView` example above, full | UI (before the API exists) |
| `health_scores.json` | `HealthScore` for ~20 components | solver (`derive_tasks`), UI |
| `tasks_golden.json` | 5 tasks incl. the engine inspection | solver |
| `plan_current.json`, `plan_baseline.json`, `replan_result.json` | shape examples | UI |
| `agent_run_golden.json` | a complete `AgentRun` with steps | UI, demo fallback |
| `chat_examples.json` | the six copilot questions with answers | UI, demo fallback |

`MOCK=1 uvicorn app.main:app` serves these fixtures from every endpoint so the front end never waits for a backend.
