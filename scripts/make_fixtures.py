#!/usr/bin/env python3
"""Generate fixtures/*.json deterministically. Run:  python -m scripts.make_fixtures

These are SHAPE EXAMPLES for development and for MOCK=1 mode. They are NOT results: every number here is made up
(synthetic: true). Real numbers come from running the real modules. Everything is validated against app/contracts/models.py.
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.contracts.models import *  # noqa: F401,F403

UTC = timezone.utc
T0 = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)  # SimClock origin
OUT = Path(__file__).resolve().parent.parent / "fixtures"
rng = random.Random(1234)


def iso(d: datetime) -> str:
    return d.astimezone(UTC).isoformat().replace("+00:00", "Z")


def dump(name: str, obj) -> None:
    def conv(o):
        if isinstance(o, BaseModel):
            return o.model_dump(mode="json")
        if isinstance(o, list):
            return [conv(x) for x in o]
        if isinstance(o, dict):
            return {k: conv(v) for k, v in o.items()}
        return o
    OUT.mkdir(exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(conv(obj), indent=1) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ stations
HUBS = {"ATL": (33.64, -84.43, 2), "DFW": (32.90, -97.04, 2), "ORD": (41.98, -87.90, 2), "DEN": (39.86, -104.67, 1),
        "LAX": (33.94, -118.41, 2), "JFK": (40.64, -73.78, 1), "MIA": (25.79, -80.29, 1), "SEA": (47.45, -122.31, 1)}
TECHS = {"ATL": [("ATL-T1", ["engine", "gear"]), ("ATL-T2", ["hydraulics", "avionics"])],
         "DFW": [("DFW-T1", ["gear", "hydraulics"]), ("DFW-T2", ["engine", "avionics"]), ("DFW-T3", ["engine"])],
         "ORD": [("ORD-T1", ["gear", "engine"]), ("ORD-T2", ["hydraulics", "avionics"])],
         "DEN": [("DEN-T1", ["hydraulics", "gear"]), ("DEN-T2", ["engine"])],
         "LAX": [("LAX-T1", ["avionics", "gear"]), ("LAX-T2", ["engine", "hydraulics"])],
         "JFK": [("JFK-T1", ["engine", "gear"])], "MIA": [("MIA-T1", ["gear", "hydraulics"])],
         "SEA": [("SEA-T1", ["avionics", "gear"]), ("SEA-T2", ["engine"])]}
# part -> {station: (qty, eta_hours)}; qty 0 = not stocked, can be shipped from SHIP_FROM
STOCK = {"BORESCOPE-KIT": {"DFW": (2, 8.0), "ORD": (1, 0.0)},
         "BRAKE-ASSY": {"ORD": (2, 0.0), "ATL": (2, 0.0), "DEN": (1, 0.0), "LAX": (1, 0.0)},
         "HYD-PUMP": {"DEN": (1, 0.0), "ATL": (1, 0.0), "DFW": (1, 0.0), "LAX": (1, 0.0)},
         "AVIONICS-LRU": {"LAX": (2, 0.0), "SEA": (1, 0.0), "ATL": (1, 0.0)}}
SHIP_FROM = {"BORESCOPE-KIT": "DFW", "BRAKE-ASSY": "ATL", "HYD-PUMP": "DFW", "AVIONICS-LRU": "LAX"}


def make_stations(kit_delay_h: float = 0.0, closed: tuple = ()) -> list[Station]:
    out = []
    for iata, (lat, lon, slots) in HUBS.items():
        parts = []
        for part, table in STOCK.items():
            qty, eta = table.get(iata, (0, 0.0))
            if part == "BORESCOPE-KIT" and iata == "DFW":
                eta += kit_delay_h
            parts.append(PartStock(part=part, qty=qty, eta_hours=eta, ship_from=SHIP_FROM[part], ship_hours=12.0, ship_cost_usd=800.0))
        techs = [Technician(id=i, skills=s, shift_start=T0 - timedelta(days=1), shift_end=T0 + timedelta(days=8)) for i, s in TECHS[iata]]
        out.append(Station(iata=iata, hangar_slots=0 if iata in closed else slots, technicians=techs, parts=parts, lat=lat, lon=lon))
    return out


def dist_km(a: str, b: str) -> float:
    (la1, lo1, _), (la2, lo2, _) = HUBS[a], HUBS[b]
    p1, p2 = math.radians(la1), math.radians(la2)
    dl = math.radians(lo2 - lo1)
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(x))


# ------------------------------------------------------------------ fleet + legs
PARTNER = {"ATL": "DFW", "DFW": "ORD", "ORD": "DEN", "DEN": "LAX", "LAX": "SEA", "SEA": "DEN", "JFK": "MIA", "MIA": "ATL"}
HUB_LIST = list(HUBS)
GOLDEN_ROUTES = {"SIM-017": ("ATL", "DFW"), "SIM-004": ("ORD", "DEN"), "SIM-011": ("DEN", "LAX"), "SIM-020": ("LAX", "SEA"), "SIM-008": ("ATL", "MIA")}
TAILS = [f"SIM-{i:03d}" for i in range(1, 25)]
ROUTES = {t: GOLDEN_ROUTES.get(t) or (HUB_LIST[(i + 1) % 8], PARTNER[HUB_LIST[(i + 1) % 8]]) for i, t in enumerate(TAILS)}
SLOTS = [(6, 30), (10, 15), (13, 45), (17, 15)]  # 4 legs/day; the day starts AND ends at the partner station, so nights are spent there


def make_legs() -> dict[str, list[Leg]]:
    legs: dict[str, list[Leg]] = {}
    for tail, (home, partner) in ROUTES.items():
        minutes = round(40 + dist_km(home, partner) / 13.33)
        out, n = [], 0
        for d in range(7):
            day = datetime(2026, 10, 12 + d, tzinfo=UTC)
            for k, (hh, mm) in enumerate(SLOTS):
                dep = day.replace(hour=hh, minute=mm)
                if dep < T0:
                    continue
                a, b = (partner, home) if k % 2 == 0 else (home, partner)
                n += 1
                out.append(Leg(id=f"L-{tail[-3:]}-{n:03d}", tail=tail, dep=a, arr=b, sched_dep=dep, sched_arr=dep + timedelta(minutes=minutes)))
        legs[tail] = out
    return legs


def windows(legs: list[Leg]) -> list[GroundWindow]:
    out, prev = [], T0
    for lg in legs:
        if lg.sched_dep > prev:
            out.append(GroundWindow(tail=lg.tail, station=lg.dep, start=prev, end=lg.sched_dep, before_leg=lg.id))
        prev = lg.sched_arr
    return out


# ------------------------------------------------------------------ tasks, plans
def task(tail, comp, n, title, dur, days, skill, part, basis, source="rul") -> Task:
    return Task(id=f"T-{tail}-{comp}-{n:03d}", tail=tail, component_id=f"{tail}/{comp}", title=title, duration_h=dur, earliest=T0,
                deadline=T0 + timedelta(days=days), required_skill=skill, required_part=part, part_qty=1 if part else 0, source=source, basis=basis)


TASKS = [
    task("SIM-017", "engine_2", 1, "Engine 2 borescope inspection", 6.0, 4.5, "engine", "BORESCOPE-KIT",
         "SIM-017/engine_2: RUL p10 = 18 cycles, about 4.5 days at 4 flights/day (conservative)."),
    task("SIM-004", "brakes", 1, "Brake assembly replacement", 3.0, 3.0, "gear", "BRAKE-ASSY", "SIM-004/brakes: wear limit in 12 cycles (p10), about 3 days."),
    task("SIM-011", "hydraulics", 1, "Hydraulic pump replacement", 5.0, 3.5, "hydraulics", "HYD-PUMP", "SIM-011/hydraulics: pressure trend, RUL p10 = 14 cycles."),
    task("SIM-020", "avionics", 1, "Avionics LRU swap", 2.0, 2.0, "avionics", "AVIONICS-LRU", "Scheduled item due 2026-10-14.", source="scheduled"),
    task("SIM-008", "landing_gear", 1, "Landing gear actuator check", 4.0, 4.0, "gear", None, "Scheduled inspection due 2026-10-16.", source="scheduled"),
]
DESIGNATED = {"SIM-017": "DFW", "SIM-004": "DEN", "SIM-011": "LAX", "SIM-020": "SEA", "SIM-008": "MIA"}  # where the aircraft sleeps
LEGS = make_legs()
WINS = {t: windows(l) for t, l in LEGS.items()}


def part_ready(stations: list[Station], iata: str, part: str | None):
    """(hours from T0 until the part can be used at the station, ship_from or None, cost)"""
    if part is None:
        return 0.0, None, 0.0
    st = next(s for s in stations if s.iata == iata)
    ps = next(p for p in st.parts if p.part == part)
    if ps.qty > 0:
        return ps.eta_hours, None, 0.0
    src_ready, _, _ = part_ready(stations, ps.ship_from, part)
    return src_ready + ps.ship_hours, ps.ship_from, ps.ship_cost_usd


def fmt_h(x: float) -> str:
    return f"{x:.1f} h"


def assign(tk: Task, stations: list[Station], iata: str, mode: str) -> Assignment:
    """mode 'opt': earliest feasible window at the station; 'base': last window at the home base before the deadline."""
    home = ROUTES[tk.tail][0]
    wins = [w for w in WINS[tk.tail] if w.station == (iata if mode == "opt" else home)]
    ready_h, src, cost = part_ready(stations, iata if mode == "opt" else home, tk.required_part)
    ready_t = T0 + timedelta(hours=ready_h)
    tech_st = next(s for s in stations if s.iata == (iata if mode == "opt" else home))
    tech = next((t.id for t in tech_st.technicians if tk.required_skill in t.skills), None)
    if mode == "opt":
        for w in wins:
            start = max(w.start, ready_t + timedelta(minutes=30))
            end = start + timedelta(hours=tk.duration_h)
            if end <= w.end and end <= tk.deadline:
                why = (f"Earliest window at {iata} that fits {tk.duration_h:g} h with a {tk.required_skill} technician"
                       + (f" and {tk.required_part} on hand ({'ready ' + fmt_h(ready_h) + ' after now' if ready_h else 'in stock'})" if tk.required_part else "")
                       + f"; 0 h of extension; finishes {fmt_h((tk.deadline - end).total_seconds() / 3600)} before the deadline.")
                return Assignment(task_id=tk.id, tail=tk.tail, station=iata, start=start, end=end, technician_id=tech, part_source=None,
                                  window_hours=round(w.hours, 2), extension_h=0.0, reason=why)
        raise RuntimeError(f"no window for {tk.id}")
    for w in reversed(wins):  # baseline: last home-base window before the deadline that can still finish on time
        start = max(w.start, ready_t)
        end = start + timedelta(hours=tk.duration_h)
        if w.start < tk.deadline and end <= tk.deadline:
            ext = max(0.0, (end - w.end).total_seconds() / 3600)
            return Assignment(task_id=tk.id, tail=tk.tail, station=home, start=start, end=end, technician_id=tech, part_source=src,
                              window_hours=round(w.hours, 2), extension_h=round(ext, 2),
                              reason=f"Static policy: last home-base ({home}) window before the deadline; window is {w.hours:.1f} h"
                                     + (f", job needs {tk.duration_h:g} h, so the next departure is delayed {ext:.1f} h" if ext else "")
                                     + (f"; part shipped from {src}" if src else "") + ".")
    raise RuntimeError(f"no baseline window for {tk.id}")


def metrics(assigns: list[Assignment], changes: int = 0, stations: list[Station] | None = None) -> PlanMetrics:
    stations = stations or make_stations()
    cost = 0.0
    for a in assigns:
        tk = next(t for t in TASKS if t.id == a.task_id)
        if a.part_source and tk.required_part:
            cost += next(p for p in next(s for s in stations if s.iata == a.station).parts if p.part == tk.required_part).ship_cost_usd
    n_tech = sum(len(v) for v in TECHS.values())
    busy = sum((a.end - a.start).total_seconds() / 3600 for a in assigns)
    return PlanMetrics(downtime_h=round(sum(a.extension_h for a in assigns), 2), flights_affected=sum(1 for a in assigns if a.extension_h > 0),
                       logistics_cost_usd=cost, tech_utilization=round(busy / (n_tech * 120), 3), hangar_utilization=round(busy / (14 * 120), 3),
                       schedule_changes=changes, tasks_total=len(TASKS), tasks_scheduled=len(assigns), late_or_unscheduled=0)


def objective(m: PlanMetrics, w: ObjectiveWeights = ObjectiveWeights()) -> float:
    return round(w.downtime * m.downtime_h + w.disruption * m.flights_affected + w.logistics * m.logistics_cost_usd + w.changes * m.schedule_changes, 2)


def make_plan(pid: str, mode: str, assigns: list[Assignment], changes: int, secs: float, stations: list[Station] | None = None, ev: list[EvidenceRef] | None = None) -> Plan:
    m = metrics(assigns, changes, stations)
    return Plan(id=pid, created=T0 + timedelta(minutes=5), mode=mode, feasible=True, assignments=assigns, metrics=m, objective_value=objective(m),
                solve_seconds=secs, evidence=ev or [])


# ------------------------------------------------------------------ twin SIM-017
SENSOR_START = {"T24": 641.9, "T30": 1586.0, "T50": 1402.0, "P30": 554.4, "Nf": 2388.0, "Nc": 9046.0, "Ps30": 47.5, "phi": 521.7, "NRf": 2388.0,
                "NRc": 8138.0, "BPR": 8.42, "htBleed": 391.0, "W31": 39.1, "W32": 23.4}
SENSOR_END = {"T24": 643.6, "T30": 1604.0, "T50": 1431.0, "P30": 551.5, "Nf": 2388.3, "Nc": 9061.0, "Ps30": 47.0, "phi": 519.9, "NRf": 2388.2,
              "NRc": 8155.0, "BPR": 8.47, "htBleed": 396.0, "W31": 38.5, "W32": 23.0}
COMP_KINDS = [("engine_1", "engine"), ("engine_2", "engine"), ("brakes", "brakes"), ("landing_gear", "landing_gear"), ("hydraulics", "hydraulics"), ("avionics", "avionics")]


def make_component(tail: str, cid: str, kind: str, health: float, anomaly: float, rul: tuple | None, status: str, base_cycle: int, n_hist=40, sensors=None, last_maint=None) -> TwinComponent:
    hist = []
    for i in range(n_hist):
        f = i / (n_hist - 1)
        h = min(1.0, health + (1 - f) * min(0.45, 1 - health) * 0.9) if health < 0.9 else health + rng.uniform(-0.01, 0.01)
        a = max(0.0, anomaly * f * f + rng.uniform(0, 0.03))
        hist.append(HistoryPoint(cycle=base_cycle - (n_hist - 1 - i), ts=T0 - timedelta(hours=6 * (n_hist - 1 - i)), health=round(max(0, min(1, h)), 3),
                                 anomaly_score=round(min(1, a), 3), rul_p50=round(rul[1] + (n_hist - 1 - i) * 1.0, 1) if rul else None))
    pred = []
    if rul:
        n = int(min(rul[2] + 5, 40))
        for j in range(1, n + 1):
            pred.append(PredictedPoint(cycle=base_cycle + j, health_p50=round(max(0, health * (1 - j / rul[1])), 3),
                                       health_lo=round(max(0, health * (1 - j / max(rul[0], 1))), 3), health_hi=round(max(0, health * (1 - j / rul[2])), 3)))
    return TwinComponent(tail=tail, component_id=f"{tail}/{cid}", kind=kind, cycles=base_cycle, hours=round(base_cycle * 1.77, 1), health=health, anomaly_score=anomaly,
                         rul_cycles=RulEstimate(p10=rul[0], p50=rul[1], p90=rul[2]) if rul else None, data_quality=DataQuality(status="ok"),
                         last_maintenance=last_maint, status=status, updated=T0, history=hist, predicted=pred, sensors_last=sensors or {})


def make_twin(tail: str, alert: bool) -> TwinView:
    home, partner = ROUTES[tail]
    comps = []
    for cid, kind in COMP_KINDS:
        if tail == "SIM-017" and cid == "engine_2":
            if alert:
                comps.append(make_component(tail, cid, kind, 0.18, 0.91, (18, 22, 25), "alert", 231, sensors=SENSOR_END))
            else:
                comps.append(make_component(tail, cid, kind, 0.74, 0.12, (55, 62, 70), "ok", 231, sensors={k: round(v + (SENSOR_END[k] - v) * 0.1, 2) for k, v in SENSOR_START.items()}))
        elif kind == "engine":
            comps.append(make_component(tail, cid, kind, 0.8, 0.08, (88, 96, 108), "ok", 231 if cid == "engine_1" else 229, sensors=SENSOR_START))
        else:
            comps.append(make_component(tail, cid, kind, round(rng.uniform(0.72, 0.93), 2), 0.07, (60, 75, 90), "ok", 700 + rng.randint(0, 300)))
    status = "alert" if (tail == "SIM-017" and alert) else "ok"
    tl = [TwinEvent(ts=T0 - timedelta(days=2, hours=3), kind="flight", summary=f"{partner} to {home}: normal"),
          TwinEvent(ts=T0 - timedelta(days=1), kind="maintenance", summary="Scheduled brake inspection completed", ref="WO-OLD-17")]
    if tail == "SIM-017" and alert:
        tl.append(TwinEvent(ts=T0, kind="anomaly", summary="engine_2 abnormal T50 trend; RUL p10 18 cycles", ref="E-1"))
    return TwinView(aircraft=Aircraft(tail=tail, type="B738-class" if int(tail[-3:]) % 3 == 0 else "A320-class", home=home, location=LEGS[tail][0].dep, status=status,
                                      cycles=8123 if tail == "SIM-017" else 5000 + int(tail[-3:]) * 97, hours=14210.5 if tail == "SIM-017" else 9000.0 + int(tail[-3:]) * 213.1),
                    components=comps, timeline=sorted(tl, key=lambda e: e.ts, reverse=True))


# ------------------------------------------------------------------ main
def main() -> None:
    stations = make_stations()
    dump("stations", stations)
    dump("legs_golden", [lg for t in TAILS for lg in LEGS[t]])
    dump("tasks_golden", TASKS)

    # fleet overview (initial state = BEFORE the anomaly is injected)
    watch = {"SIM-003": ("brakes", 44.0), "SIM-009": ("hydraulics", 51.0), "SIM-014": ("landing_gear", 47.0), "SIM-021": ("avionics", 58.0), "SIM-011": ("hydraulics", 14.0),
             "SIM-004": ("brakes", 12.0)}
    summaries = []
    for t in TAILS:
        w = watch.get(t)
        status = "in_maintenance" if t == "SIM-006" else ("watch" if w else "ok")
        summaries.append(AircraftSummary(tail=t, type="B738-class" if int(t[-3:]) % 3 == 0 else "A320-class", home=ROUTES[t][0], location="ATL" if t == "SIM-006" else LEGS[t][0].dep,
                                         status=status, worst_component=f"{t}/{w[0]}" if w else None, rul_p10_min=w[1] if w else round(rng.uniform(70, 130), 0)))
    n_attn = sum(1 for s in summaries if s.status in ("watch", "alert"))
    dump("fleet", FleetOverview(kpis=FleetKpis(aircraft=24, needing_attention=n_attn, predicted_maintenance_72h=sum(1 for tk in TASKS if tk.deadline <= T0 + timedelta(hours=72)),
                                                in_maintenance=1, readiness_pct=round(100 * 23 / 24, 1)), aircraft=summaries, stations=stations))

    dump("twin_SIM-017", make_twin("SIM-017", alert=True))
    dump("twin_SIM-017_pre", make_twin("SIM-017", alert=False))

    scores = []
    for t in TAILS[:10]:
        for cid, kind in COMP_KINDS[:3]:
            bad = t == "SIM-017" and cid == "engine_2"
            scores.append(HealthScore(tail=t, component_id=f"{t}/{cid}", health=0.18 if bad else round(rng.uniform(0.55, 0.95), 2), anomaly_score=0.91 if bad else round(rng.uniform(0.02, 0.2), 2),
                                      is_anomaly=bad, rul_cycles=RulEstimate(p10=18, p50=22, p90=25) if bad else RulEstimate(p10=70, p50=85, p90=100), data_quality=DataQuality(status="ok"),
                                      model_version="fixture-v0", top_signals=["T50", "Nc", "Ps30"] if bad else []))
    dump("health_scores", scores)

    # plans
    opt = [assign(tk, stations, DESIGNATED[tk.tail], "opt") for tk in TASKS]
    base = [assign(tk, stations, DESIGNATED[tk.tail], "base") for tk in TASKS]
    ev = [EvidenceRef(kind="health", ref="SIM-017/engine_2", note="RUL p10 18 cycles, anomaly 0.91"), EvidenceRef(kind="tool_call", ref="TC-3", note="solve_schedule(cpsat)")]
    p_opt = make_plan("P-2", "cpsat", opt, 0, 0.8, ev=ev)
    p_base = make_plan("P-1", "static_baseline", base, 0, 0.1)
    dump("plan_current", p_opt)
    dump("plan_baseline", p_base)

    # replan: 12 h delay of the DFW borescope kit shipment breaks the Oct 12-13 night window
    st2 = make_stations(kit_delay_h=12.0)
    opt2 = [assign(tk, st2, DESIGNATED[tk.tail], "opt") for tk in TASKS]
    moved = []
    for a, b in zip(opt, opt2):
        if (a.station, a.start) != (b.station, b.start):
            moved.append({"task_id": a.task_id, "from": {"station": a.station, "start": iso(a.start)}, "to": {"station": b.station, "start": iso(b.start)},
                          "why": "The DFW kit now arrives 20 h from now, after the first night window closes; next DFW night with the kit on hand, 0 h extension."})
    p_after = make_plan("P-3", "cpsat", opt2, len(moved), 1.1, stations=st2, ev=ev)
    event = ScenarioEvent(id="E-2", type="part_delay", at=T0 + timedelta(hours=2), station="DFW", part="BORESCOPE-KIT", delay_hours=12.0, note="Supplier delay")
    dump("replan_result", ReplanResult(event=event, before=p_opt, after=p_after, diff=PlanDiff(moved=moved, added=[], dropped=[]), solve_seconds=1.1))

    # work orders (pending) from the optimized plan
    wos = []
    for i, a in enumerate(opt, 1):
        tk = next(t for t in TASKS if t.id == a.task_id)
        wos.append(WorkOrder(id=f"WO-{i}", tail=a.tail, task_id=a.task_id, station=a.station, start=a.start, end=a.end, description=f"{tk.title} ({a.tail}) at {a.station}",
                             plan_id="P-2", evidence=[EvidenceRef(kind="plan", ref="P-2", note=a.reason[:80]), EvidenceRef(kind="kb", ref="TC-ENG-001" if "engine" in tk.component_id else "TC-GEN-001", note="synthetic task card")]))
    dump("workorders", wos)

    # agent run (golden)
    steps, n = [], 0

    def step(agent, kind, text="", tool=None, inp=None, out=None, dt=0):
        nonlocal n
        n += 1
        steps.append(AgentStep(n=n, agent=agent, kind=kind, tool=tool, input=inp, output=out, text=text, ts=T0 + timedelta(seconds=dt + n * 3)))
    e = opt[0]
    step("health", "thought", "Scan the fleet for components with low RUL or anomalies.")
    step("health", "tool_call", tool="list_at_risk", inp={"horizon_cycles": 60})
    step("health", "tool_result", tool="list_at_risk", out={"count": 3, "top": [{"component_id": "SIM-017/engine_2", "health": 0.18, "rul_p10": 18, "is_anomaly": True}]})
    step("health", "tool_call", tool="get_sensor_trend", inp={"component_id": "SIM-017/engine_2", "last": 30})
    step("health", "tool_result", tool="get_sensor_trend", out={"T50_change": "+29", "Nc_change": "+15", "note": "rising LPT outlet temperature (T50)"})
    step("health", "summary", "SIM-017 engine 2 shows an abnormal T50 (LPT outlet temperature) trend. RUL p10/p50/p90 = 18/22/25 cycles; deadline is set from p10.")
    step("scheduling", "tool_call", tool="derive_tasks", inp={})
    step("scheduling", "tool_result", tool="derive_tasks", out={"tasks": len(TASKS), "first": TASKS[0].id, "deadline": iso(TASKS[0].deadline)})
    step("scheduling", "tool_call", tool="find_ground_windows", inp={"tail": "SIM-017", "horizon_h": 108})
    wsel = [w for w in WINS["SIM-017"] if w.hours > 6][:2]
    step("scheduling", "tool_result", tool="find_ground_windows", out={"windows": [{"station": w.station, "start": iso(w.start), "hours": round(w.hours, 1)} for w in wsel]})
    step("scheduling", "tool_call", tool="solve_schedule", inp={"mode": "cpsat"})
    step("scheduling", "tool_result", tool="solve_schedule", out={"plan_id": "P-2", "feasible": True, "solve_seconds": 0.8})
    step("scheduling", "tool_call", tool="solve_schedule", inp={"mode": "static_baseline"})
    step("scheduling", "tool_result", tool="solve_schedule", out={"plan_id": "P-1", "feasible": True, "downtime_h": p_base.metrics.downtime_h})
    step("scheduling", "summary", f"Best window for the engine job: {e.station} {iso(e.start)}; baseline would delay {p_base.metrics.flights_affected} flights.")
    step("logistics", "tool_call", tool="check_parts", inp={"station": "DFW", "part": "BORESCOPE-KIT"})
    step("logistics", "tool_result", tool="check_parts", out={"on_hand": 2, "eta_hours": 8.0, "ship_from": None})
    step("logistics", "tool_call", tool="match_technicians", inp={"station": "DFW", "skill": "engine"})
    step("logistics", "tool_result", tool="match_technicians", out={"technicians": ["DFW-T2", "DFW-T3"]})
    step("logistics", "summary", "DFW has 2 borescope kits arriving in 8 h and two engine technicians; ATL has none on hand (ship from DFW, $800).")
    step("operations", "tool_call", tool="simulate_plan", inp={"plan_id": "P-2"})
    step("operations", "tool_result", tool="simulate_plan", out=p_opt.metrics.model_dump(mode="json"))
    step("operations", "summary", f"Plan P-2: downtime {p_opt.metrics.downtime_h} h, flights affected {p_opt.metrics.flights_affected}, logistics ${p_opt.metrics.logistics_cost_usd:.0f}.")
    step("coordinator", "tool_call", tool="search_kb", inp={"query": "engine borescope inspection"})
    step("coordinator", "tool_result", tool="search_kb", out={"results": [{"doc_id": "TC-ENG-001", "title": "Engine borescope inspection (synthetic task card)"}]})
    step("coordinator", "tool_call", tool="draft_work_orders", inp={"plan_id": "P-2"})
    step("coordinator", "tool_result", tool="draft_work_orders", out={"work_orders": [w.id for w in wos], "status": "pending_approval"})
    expl = (f"Recommend engine 2 borescope inspection on SIM-017 at DFW, {e.start:%b %d %H:%MZ} to {e.end:%H:%MZ}: the first window with an engine technician and the kit on hand, "
            f"{e.window_hours:g} h ground time, no flight delay. Waiting for the static home-base policy would delay {p_base.metrics.flights_affected} flights and cost ${p_base.metrics.logistics_cost_usd:.0f} in shipping. "
            "Work orders are drafted for human approval.")
    step("coordinator", "summary", expl)
    dump("agent_run_golden", AgentRun(id="R-1", goal="Plan maintenance for the aircraft that need attention", mode="scripted", status="done", steps=steps, plan=p_opt, work_orders=wos,
                                      explanation=expl, missing_info=[]))

    # chat examples
    ev1 = [EvidenceRef(kind="health", ref="SIM-017/engine_2", note="RUL p10 18"), EvidenceRef(kind="plan", ref="P-2")]
    chats = [
        {"match": ["72", "likely", "next"], "response": ChatResponse(answer="In the next 72 hours the components closest to their deadlines are SIM-017 engine 2 (RUL p10 18 cycles) and SIM-004 brakes (12 cycles).", evidence=ev1)},
        {"match": ["why", "prioritized", "priority"], "response": ChatResponse(answer="SIM-017 engine 2 ranks first: anomaly score 0.91 and RUL p10 of 18 cycles, versus 12 cycles for SIM-004 brakes but a cheaper, shorter job.", evidence=ev1)},
        {"match": ["window", "tomorrow"], "response": ChatResponse(answer=f"Best feasible window for SIM-017: {e.station} {iso(e.start)} to {iso(e.end)}.", evidence=ev1, proposed_work_orders=[wos[0]])},
        {"match": ["parts", "technician"], "response": ChatResponse(answer="Engine job needs 1 engine technician (DFW-T2) and 1 BORESCOPE-KIT (2 arriving at DFW in 8 h).", evidence=[EvidenceRef(kind="tool_call", ref="TC-15")])},
        {"match": ["unavailable", "closed", "facility"], "response": ChatResponse(answer="If DFW's hangar closes, the engine job moves to the next window with a free hangar; see the Simulation Lab for the exact plan.", evidence=ev1,
                                                                                  missing_info=["No alternative hangar slot data beyond the 5-day horizon."])},
        {"match": ["compare", "optimized", "schedule"], "response": ChatResponse(answer=f"Optimized vs static baseline: downtime {p_opt.metrics.downtime_h} h vs {p_base.metrics.downtime_h} h, flights affected {p_opt.metrics.flights_affected} vs {p_base.metrics.flights_affected}.", evidence=ev1)},
    ]
    dump("chat_examples", chats)

    dump("audit", [AuditEntry(ts=T0, actor="system", action="scenario_reset", ref="golden", detail="fixtures loaded")])
    dump("metrics", MetricsBundle(ml={"status": "MOCK: not computed", "data_source": "fixture", "rul_mae": None, "rul_rmse": None, "anomaly_precision": None, "anomaly_recall": None, "anomaly_fpr": None},
                                  plan={"baseline": p_base.metrics, "optimized": p_opt.metrics, "replan_seconds": 1.1, "note": "fixture numbers, not results"}))
    print(f"fixtures written to {OUT}")
    print(f"  optimized: downtime {p_opt.metrics.downtime_h} h, flights {p_opt.metrics.flights_affected}, cost ${p_opt.metrics.logistics_cost_usd:.0f}")
    print(f"  baseline : downtime {p_base.metrics.downtime_h} h, flights {p_base.metrics.flights_affected}, cost ${p_base.metrics.logistics_cost_usd:.0f}")
    print(f"  replan   : {len(moved)} task(s) moved; engine job {opt[0].start:%b %d %H:%M} -> {opt2[0].start:%b %d %H:%M} at {opt2[0].station}")


if __name__ == "__main__":
    main()
