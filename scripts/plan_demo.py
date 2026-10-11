"""Pardhu's part end to end, printed: tasks -> baseline vs greedy vs CP-SAT -> disruption replan -> what-if compare -> simulation.

    python scripts/plan_demo.py                 # print the tables (every number comes from this run)
    python scripts/plan_demo.py --export        # also write fixtures/plan_current.json, plan_baseline.json, replan_result.json

Uses the golden stand-in state (app/solver/golden.py) unless app.twin.state.snapshot() exists. SIMULATED data throughout.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.solver import derive_tasks, solve  # noqa: E402
from app.solver.common import fmt  # noqa: E402
from app.solver.exposure import exposure  # noqa: E402
from app.solver.golden import golden_part_delay  # noqa: E402
from app.solver.plan_metrics import validate_plan  # noqa: E402
from app.solver.service import get_state  # noqa: E402
from app.solver.store import STORE  # noqa: E402
from app.whatif import compare as cmp  # noqa: E402
from app.whatif.events import apply_event_to_state  # noqa: E402
from app.whatif.replan import replan  # noqa: E402
from app.whatif.simulate import simulate_detailed  # noqa: E402

CRITICAL = "T-SIM-017-engine_2-001"


def line(title: str = "") -> None:
    print(f"\n{'=' * 6} {title} " + "=" * max(0, 74 - len(title)))


def table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    fmt_row = lambda r: "  ".join(str(c).ljust(w) for c, w in zip(r, widths))  # noqa: E731
    print(fmt_row(headers))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print(fmt_row(r))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", action="store_true", help="write the plan fixtures for the UI team")
    args = ap.parse_args()

    STORE.reset()
    state, source = get_state()
    tasks = derive_tasks(state)
    print(f"AeroTwin AI: scheduler + what-if demo   (SIMULATED data; state source: {source}; sim time {fmt(state.now)})")

    line("1. Tasks derived from RUL p10 (hard deadlines)")
    table(["task", "source", "skill", "part", "h", "deadline"],
          [[t.id, t.source, t.required_skill, t.required_part or "-", f"{t.duration_h:g}", fmt(t.deadline)] for t in tasks])
    print("\n  basis of the critical task:", next(t for t in tasks if t.id == CRITICAL).basis)

    line("2. Baseline vs greedy vs optimized (same snapshot)")
    base = solve(state, tasks, mode="static_baseline")      # P-1
    opt = solve(state, tasks, mode="cpsat")                 # P-2  (matches the id in CONTRACTS.md examples)
    greedy = solve(state, tasks, mode="greedy")
    plans = {"static baseline": base, "greedy": greedy, "CP-SAT optimized": opt}
    def row(label, f):
        return [label] + [f(p) for p in plans.values()]
    table(["metric", *plans.keys()], [
        row("feasible", lambda p: str(p.feasible)),
        row("tasks scheduled", lambda p: f"{p.metrics.tasks_scheduled}/{p.metrics.tasks_total}"),
        row("late or unscheduled", lambda p: str(p.metrics.late_or_unscheduled)),
        row("downtime (h beyond ground time)", lambda p: f"{p.metrics.downtime_h:g}"),
        row("flights affected", lambda p: str(p.metrics.flights_affected)),
        row("logistics cost (USD)", lambda p: f"{p.metrics.logistics_cost_usd:,.0f}"),
        row("technician utilization", lambda p: f"{p.metrics.tech_utilization:.2%}"),
        row("hangar utilization", lambda p: f"{p.metrics.hangar_utilization:.2%}"),
        row("objective (lower is better)", lambda p: f"{p.objective_value:.2f}"),
        row("solve time (s)", lambda p: f"{p.solve_seconds:.3f}"),
    ])
    print("\n  RISK VIEW (cost can tie while risk does not): time until each condition-driven fault is fixed")
    eb, eo = exposure(state, tasks, base), exposure(state, tasks, opt)
    table(["task", "plan", "hours to fix", "flights flown with fault open", "margin before hard deadline (h)"],
          [[b["task_id"], "baseline", f"{b.get('hours_to_fix', '-')}", f"{b.get('flights_before_fix', '-')}", f"{b.get('slack_to_deadline_h', '-')}"] for b in eb]
          + [[o["task_id"], "optimized", f"{o.get('hours_to_fix', '-')}", f"{o.get('flights_before_fix', '-')}", f"{o.get('slack_to_deadline_h', '-')}"] for o in eo])
    a_b = next(a for a in base.assignments if a.task_id == CRITICAL)
    a_o = next(a for a in opt.assignments if a.task_id == CRITICAL)
    gain_h = (a_b.end - a_o.end).total_seconds() / 3600
    print(f"\n  Critical task {CRITICAL} (SIM-017 engine_2):")
    print(f"    baseline : {a_b.station} {fmt(a_b.start)} -> {fmt(a_b.end)}  (kit shipped from {a_b.part_source}, "
          f"${next(p.ship_cost_usd for s in state.stations if s.iata == a_b.station for p in s.parts if p.part == 'BORESCOPE-KIT'):,.0f})")
    print(f"    optimized: {a_o.station} {fmt(a_o.start)} -> {fmt(a_o.end)}  (kit {'on hand' if not a_o.part_source else 'shipped'})"
          f"  => done {gain_h:.1f} h sooner")
    print(f"    why      : {a_o.reason}")
    print("\n  BASELINE DEFINITION: each task at the aircraft's HOME BASE, in the LAST ground window that fits before its deadline.")
    print("  Savings are SIMULATED relative to that baseline on synthetic data; not verified airline results.")

    line("3. Disruption: DFW BORESCOPE-KIT delayed 12 h, automatic replan")
    event = golden_part_delay(12)
    r = replan(state, opt, event)
    print(f"  replan time: {r.solve_seconds:.3f} s (target < 5 s)    new plan feasible: {r.after.feasible}    mode: {r.after.mode}")
    for e in r.after.evidence:
        if e.kind == "event":
            print("  effect :", e.note)
    for m in r.diff.moved:
        print(f"  MOVED  : {m['task_id']}  {m['from']['station']} {m['from']['start'][:16]}Z  ->  {m['to']['station']} {m['to']['start'][:16]}Z")
        print(f"           why: {m['why']}")
    print(f"  added: {r.diff.added or '-'}   dropped: {r.diff.dropped or '-'}   schedule_changes: {r.after.metrics.schedule_changes}")
    st2 = apply_event_to_state(state, event)
    stale = validate_plan(st2, derive_tasks(st2), opt.assignments, 3.0)
    print(f"  if the old plan were left unchanged, {len(stale)} hard-constraint violation(s) would appear: {stale[:1] or 'none'}")
    table(["", "before", "after"], [
        ["downtime h", f"{r.before.metrics.downtime_h:g}", f"{r.after.metrics.downtime_h:g}"],
        ["flights affected", str(r.before.metrics.flights_affected), str(r.after.metrics.flights_affected)],
        ["logistics USD", f"{r.before.metrics.logistics_cost_usd:,.0f}", f"{r.after.metrics.logistics_cost_usd:,.0f}"],
    ])

    line("4. What-if scenarios (each solved independently from the same snapshot)")
    scenarios = cmp.default_scenarios(state, opt, tasks)
    comp = cmp.compare(state, cmp.build_scenario_plans(state, scenarios, base_plan=opt))
    table(["scenario", "feasible", "sched", "downtime h", "flights", "logistics $", "changes", "s"], [
        [x["name"], str(x["feasible"]), f"{x['metrics']['tasks_scheduled']}/{x['metrics']['tasks_total']}",
         f"{x['metrics']['downtime_h']:g}", str(x["metrics"]["flights_affected"]), f"{x['metrics']['logistics_cost_usd']:,.0f}",
         str(x["metrics"]["schedule_changes"]), f"{x['solve_seconds']:.2f}"] for x in comp.rows])
    for x in comp.rows:
        if not x["feasible"]:
            print(f"  NOT FEASIBLE: {x['name']}: {x['infeasible_reasons'][0]}")

    line("5. Discrete-event simulation (SimPy, 20 runs, durations +-15%, seed 0)")
    sims = {"static baseline": simulate_detailed(base, state, runs=20, seed=0, tasks=tasks),
            "CP-SAT optimized": simulate_detailed(opt, state, runs=20, seed=0, tasks=tasks)}
    table(["", "downtime mean h", "downtime p90 h", "flights affected (mean)", "runs with a deadline overrun"],
          [[k, f"{v.downtime_mean_h:g}", f"{v.downtime_p90_h:g}", f"{v.flights_mean:g}", f"{v.deadline_overrun_runs}/{v.runs}"]
           for k, v in sims.items()])
    print("  (jitter can push a job planned to end near its deadline past it; that is reported, not hidden)")

    if args.export:
        out = Path(__file__).resolve().parent.parent / "fixtures"
        out.mkdir(exist_ok=True)
        for name, model in (("plan_current.json", opt), ("plan_baseline.json", base), ("replan_result.json", r)):
            (out / name).write_text(json.dumps(json.loads(model.model_dump_json()), indent=2) + "\n")
        print(f"\nwrote {out}/plan_current.json, plan_baseline.json, replan_result.json")


if __name__ == "__main__":
    main()
