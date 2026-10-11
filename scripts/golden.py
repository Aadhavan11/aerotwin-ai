#!/usr/bin/env python3
"""The golden path, end to end (make demo-check):  python -m scripts.golden [--base http://localhost:8000]

reset -> advance time -> inject the SIM-017 engine anomaly -> twin shows ALERT -> agents run (poll) -> work orders pending -> human approves -> audit entry
-> delay the DFW kit -> replan -> baseline vs optimized. Prints a PASS/FAIL table; exit code 1 on any failure. Works in MOCK and real mode.
Numbers printed come from the API responses, never from this script.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

from app.contracts.models import *  # noqa: F401,F403


class Http:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def req(self, method: str, path: str, body=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(r, timeout=60) as resp:
                return resp.status, json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, {"error": e.read().decode()[:300]}

    def get(self, p):
        return self.req("GET", p)

    def post(self, p, b=None):
        return self.req("POST", p, b if b is not None else {})


def run(h, poll_timeout: float = 90.0, verbose: bool = True) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        results.append((name, bool(ok), detail))
        return bool(ok)

    s, health = h.get("/health")
    check("GET /health", s == 200 and health.get("ok"), f"modules: {health.get('modules')}" if s == 200 else str(health))
    s, r = h.post("/scenario/reset", {"name": "golden"})
    check("POST /scenario/reset", s == 200, str(r)[:80])
    s, r = h.post("/sim/step", {"hours": 6})
    check("POST /sim/step", s == 200, str(r)[:80])

    s, fleet = h.get("/fleet")
    ok = s == 200
    if ok:
        FleetOverview.model_validate(fleet)
        before = next(a for a in fleet["aircraft"] if a["tail"] == "SIM-017")["status"]
        check("GET /fleet (24 aircraft)", len(fleet["aircraft"]) >= 20, f"{len(fleet['aircraft'])} aircraft; SIM-017 is '{before}'")
    else:
        check("GET /fleet", False, str(fleet)[:100])

    def sim_now() -> str:  # events must be stamped with the current SIMULATION time (the real simulator enforces this)
        return h.get("/health")[1]["sim_now"]

    ev = {"type": "anomaly", "at": sim_now(), "tail": "SIM-017", "component_id": "SIM-017/engine_2", "severity": 0.9}
    s, r = h.post("/scenario/inject", ev)
    check("POST /scenario/inject (anomaly)", s == 200 and "event_id" in r, str(r)[:100])

    s, tw = h.get("/aircraft/SIM-017")
    if s == 200:
        tv = TwinView.model_validate(tw)
        eng = next(c for c in tv.components if c.component_id == "SIM-017/engine_2")
        check("Twin: engine_2 is alert with an RUL estimate", eng.status == "alert" and eng.rul_cycles is not None,
              f"status {eng.status}, RUL p10/p50/p90 = {eng.rul_cycles.p10:g}/{eng.rul_cycles.p50:g}/{eng.rul_cycles.p90:g}" if eng.rul_cycles else f"status {eng.status}, no RUL")
    else:
        check("GET /aircraft/SIM-017", False, str(tw)[:100])

    s, run0 = h.post("/agents/run", {"goal": "Plan maintenance for the aircraft that need attention", "mode": "scripted"})
    run = None
    if s == 200:
        rid, t0 = run0["id"], time.time()
        while time.time() - t0 < poll_timeout:
            s, run = h.get(f"/agents/runs/{rid}")
            if s != 200 or run["status"] != "running":
                break
            time.sleep(0.5)
    if run and s == 200:
        ar = AgentRun.model_validate(run)
        agents_used = {st.agent for st in ar.steps if st.kind == "tool_call"}
        check("Agents: run finished", ar.status == "done", f"status {ar.status}, {len(ar.steps)} steps, mode {ar.mode}")
        check("Agents: >= 3 agents called real tools", len(agents_used) >= 3, ", ".join(sorted(agents_used)))
        check("Agents: plan feasible", ar.plan is not None and ar.plan.feasible, f"plan {ar.plan.id if ar.plan else None}")
        check("Agents: >= 1 work order, all pending, all with evidence", len(ar.work_orders) >= 1 and all(w.status == "pending_approval" and w.evidence for w in ar.work_orders),
              f"{len(ar.work_orders)} work orders")
        wid = ar.work_orders[0].id if ar.work_orders else None
    else:
        check("Agents: run", False, str(run0)[:100])
        wid = None

    if wid:
        s, w = h.post(f"/workorders/{wid}/decision", {"decision": "approve", "user": "demo-check", "note": "golden path"})
        check("Work order approved by a human", s == 200 and w.get("status") == "approved", f"{wid}: {w.get('status') if s == 200 else w}")
        s, audit = h.get("/audit")
        check("Audit log records the approval", s == 200 and any(a["action"].endswith("approved") and a["ref"] == wid for a in audit), f"{len(audit) if s == 200 else '?'} entries")

    s, rep = h.post("/plan/replan", {"event": {"type": "part_delay", "at": sim_now(), "station": "DFW", "part": "BORESCOPE-KIT", "delay_hours": 36}})
    if s == 200:
        rr = ReplanResult.model_validate(rep)
        changed = len(rr.diff.moved) + len(rr.diff.added) + len(rr.diff.dropped) > 0
        check("Replan after a 36 h kit delay: plan changed", changed, f"{len(rr.diff.moved)} moved, solve {rr.solve_seconds:.2f}s")
        check("Replan: new plan feasible (or explains why not)", rr.after.feasible or bool(rr.after.infeasible_reasons), f"feasible={rr.after.feasible} {rr.after.infeasible_reasons[:1]}")
    else:
        check("POST /plan/replan", False, str(rep)[:100])

    s, pl = h.get("/plan")
    if s == 200:
        cur, base = Plan.model_validate(pl["current"]).metrics, Plan.model_validate(pl["baseline"]).metrics
        check("Optimized is no worse than the baseline (downtime, flights affected)", cur.downtime_h <= base.downtime_h and cur.flights_affected <= base.flights_affected,
              f"downtime {cur.downtime_h:g} vs {base.downtime_h:g} h; flights affected {cur.flights_affected} vs {base.flights_affected}; logistics ${cur.logistics_cost_usd:.0f} vs ${base.logistics_cost_usd:.0f}")
    else:
        check("GET /plan", False, str(pl)[:100])

    s, m = h.get("/metrics")
    if s == 200:
        MetricsBundle.model_validate(m)
        check("GET /metrics validates", True, f"ml: {str(m['ml'].get('status', m['ml']))[:60]}")
    else:
        check("GET /metrics", False, str(m)[:100])

    if verbose:
        w = max(len(n) for n, _, _ in results)
        print(f"\n{'CHECK'.ljust(w)}  RESULT  DETAIL")
        for n, ok, d in results:
            print(f"{n.ljust(w)}  {'PASS' if ok else 'FAIL'}    {d}")
        print(f"\n{sum(ok for _, ok, _ in results)}/{len(results)} passed")
        mods = (health.get("modules") or {}) if isinstance(health, dict) else {}
        mocked = [g for g, v in mods.items() if str(v).startswith("mock")]
        if mocked:
            print(f"NOTE: these groups are still MOCK ({', '.join(mocked)}): a pass proves the contract and the UI flow, NOT the real module. GET /health shows why.")
        elif mods:
            print("All module groups are REAL.")
    return results


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    a = ap.parse_args()
    res = run(Http(a.base))
    sys.exit(0 if all(ok for _, ok, _ in res) else 1)
