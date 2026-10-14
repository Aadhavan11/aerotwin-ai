"""AeroTwin AI data contracts. SINGLE SOURCE OF TRUTH (copied from CONTRACTS.md, tested with pydantic 2.14).

Do not redefine these models elsewhere. To change one: edit CONTRACTS.md, note it in CONTRACTS-CHANGELOG.md, tell the group.
"""
from __future__ import annotations
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field

ComponentKind = Literal["engine", "brakes", "landing_gear", "hydraulics", "avionics", "apu", "tires", "ecs", "electrical"]
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
    # Estimated cost from a simulated price list (app/solver/costs.py); additive, 0 when not computed
    cost_parts_usd: float = 0.0
    cost_labor_usd: float = 0.0
    cost_bay_usd: float = 0.0
    cost_overhead_usd: float = 0.0              # a fixed amount per job (consumables, tooling, paperwork, release sign-off)
    cost_delay_usd: float = 0.0                 # delay hours x cost per hour of delay
    cost_exposure_usd: float = 0.0              # allowance for each task the plan cannot do before its deadline
    total_cost_usd: float = 0.0                 # parts + labor + bay + overhead + shipping (logistics_cost_usd) + delay + exposure

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
    # The maintenance request as a person can edit it before deciding (additive; empty on orders drafted before 2026-10-11)
    title: str = ""                             # short name of the job
    work: str = ""                              # the work to be done, in plain words (empty: the task card text is shown)
    part: Optional[str] = None                  # item being replaced or used
    part_qty: int = 0
    order_part: bool = False                    # the part has to be ordered / shipped in
    edited_by: Optional[str] = None
    edited_at: Optional[datetime] = None

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

class ScreenContext(BaseModel):
    """What the person is looking at, sent by the web app with every question and agent run so the AI can see the screen."""
    page: str = ""                              # "Aircraft twin", "Fleet overview", "Control room" ...
    path: str = ""                              # "/aircraft/SIM-017"
    role: str = ""                              # "Pilot / flight crew"
    tail: Optional[str] = None                  # the aircraft on screen, if any
    selected: Optional[str] = None              # selected system or item, as shown on screen
    sim_now: Optional[str] = None
    text: str = ""                              # the visible text (trimmed)

class ChatRequest(BaseModel):
    session_id: str
    message: str
    screen: Optional[ScreenContext] = None      # optional and additive

class ChatResponse(Stamped):
    answer: str
    evidence: list[EvidenceRef]
    missing_info: list[str] = []
    proposed_work_orders: list[WorkOrder] = []
    proposed_actions: list[dict] = []           # changes to maintenance requests the copilot proposes; applied only when the person allows them
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
