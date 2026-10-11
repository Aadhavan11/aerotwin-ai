"""Run the scripted agent workflow end to end and print what each agent did.

    python scripts/agent_demo.py            # prints the steps and the explanation (every number comes from a tool)
    python scripts/agent_demo.py --export   # also writes fixtures/agent_run_golden.json (the Agent Activity screen's fixture)
    python scripts/agent_demo.py --replan   # then applies the golden 12 h DFW kit delay through the replan tool and runs again
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.agents.runner import run_workflow  # noqa: E402
from app.agents.tools import call_tool  # noqa: E402
from app.solver.store import STORE  # noqa: E402

GOAL = "Plan maintenance for aircraft needing attention"


def show(run) -> None:
    print(f"\n== run {run.id}: status={run.status} mode={run.mode} steps={len(run.steps)} work orders={len(run.work_orders)} ==")
    for s in run.steps:
        if s.kind in ("tool_result", "summary"):
            print(f"  {s.n:3d} {s.agent:11s} {s.kind:11s} {s.text[:150]}")
    print("\nEXPLANATION:", run.explanation)
    for o in run.work_orders:
        print(f"  {o.id} [{o.status}] {o.tail} {o.station} {o.start:%b %d %H:%MZ}-{o.end:%H:%MZ}  evidence: {', '.join(e.kind + ':' + e.ref for e in o.evidence[:4])}")
    if run.missing_info:
        print("MISSING INFO:", *run.missing_info, sep="\n  - ")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--replan", action="store_true")
    args = ap.parse_args()
    STORE.reset()
    t0 = time.perf_counter()
    run = run_workflow(GOAL, {}, "scripted")
    print(f"scripted run took {time.perf_counter() - t0:.2f} s (target < 5 s). SIMULATED data; advisory only.")
    show(run)
    if args.export:
        out = Path(__file__).resolve().parent.parent / "fixtures" / "agent_run_golden.json"
        out.parent.mkdir(exist_ok=True)
        out.write_text(json.dumps(json.loads(run.model_dump_json()), indent=2) + "\n")
        print(f"\nwrote {out}")
    if args.replan:
        res = call_tool("replan_with_event", {"event": {"type": "part_delay", "at": "2026-10-12T14:00:00Z", "station": "DFW",
                                                        "part": "BORESCOPE-KIT", "delay_hours": 12}})
        print(f"\nreplan tool: moved {[m['task_id'] for m in res['data']['diff']['moved']]} in {res['data']['solve_seconds']} s")
        show(run_workflow(GOAL, {}, "scripted"))


if __name__ == "__main__":
    main()
