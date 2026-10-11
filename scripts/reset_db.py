#!/usr/bin/env python3
"""Rebuild the database from scratch:  python -m scripts.reset_db --scenario golden

Seeding order: if app.sim.fleet.generate_fleet(seed) exists (Aadhavan's simulator) it is used; otherwise the database is seeded from fixtures/
so GET /fleet, /legs, /stations work in database mode from day one. Takes well under 10 seconds.
"""
from __future__ import annotations

import argparse
import json

from app import db, fixtures, settings
from app.api.mockstate import generic_twin
from app.contracts.models import *  # noqa: F401,F403


def seed_from_fixtures() -> dict:
    fleet = fixtures.load("fleet", FleetOverview)
    twin17 = fixtures.load("twin_SIM-017_pre", TwinView)
    n_comp = 0
    for s in fleet.aircraft:
        tv = twin17 if s.tail == "SIM-017" else generic_twin(s)
        a = tv.aircraft.model_copy(update={"status": s.status, "location": s.location})
        db.put("aircraft", "tail", a.tail, a, type=a.type, home=a.home, location=a.location, status=a.status, cycles=a.cycles, hours=a.hours)
        with db.conn() as c:
            for comp in tv.components:
                state = ComponentState.model_validate(comp.model_dump())  # drops history/predicted
                c.execute("INSERT OR REPLACE INTO components(tail,component_id,kind,state_json) VALUES(?,?,?,?)", (a.tail, comp.component_id, comp.kind, state.model_dump_json()))
                n_comp += 1
    for st in fixtures.load("stations", list[Station]):
        db.put("stations", "iata", st.iata, st)
    legs = fixtures.load("legs_golden", list[Leg])
    with db.conn() as c:
        c.executemany("INSERT OR REPLACE INTO legs(id,tail,dep,arr,sched_dep,sched_arr) VALUES(?,?,?,?,?,?)",
                      [(lg.id, lg.tail, lg.dep, lg.arr, lg.sched_dep.isoformat(), lg.sched_arr.isoformat()) for lg in legs])
    for tk in fixtures.load("tasks_golden", list[Task]):
        db.put("tasks", "id", tk.id, tk, plan_id=None)
    for name in ("plan_current", "plan_baseline"):
        p = fixtures.load(name, Plan)
        db.put("plans", "id", p.id, p, mode=p.mode, feasible=int(p.feasible), created=p.created.isoformat())
        db.kv_set("current_plan_id" if name == "plan_current" else "baseline_plan_id", p.id)
    return {"aircraft": len(fleet.aircraft), "components": n_comp, "legs": len(legs)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="golden")
    args = ap.parse_args()
    db.init_db(drop=True)
    db.kv_set("scenario", args.scenario)
    db.kv_set("sim_now", settings.SIM_START)
    try:
        from app.sim.service import Simulator  # Aadhavan's simulator (work/OWNERSHIP.md pieces 9-10): it seeds the database itself

        sim = Simulator(str(settings.DB_PATH), settings.SEED)
        info = {"aircraft": len(sim.state.aircraft), "components": len(sim.state.components), "legs": len(sim.state.legs)}
        source = "app.sim.service.Simulator"
    except ModuleNotFoundError as e:
        if not (e.name or "").startswith("app.sim"):
            raise
        info = seed_from_fixtures()
        source = "fixtures (app.sim not built yet)"
    db.audit("system", "scenario_reset", args.scenario, f"seeded from {source}")
    print(json.dumps({"db": str(settings.DB_PATH), "seeded_from": source, "scenario": args.scenario, "seed": settings.SEED, **(info if isinstance(info, dict) else {})}, indent=1))


if __name__ == "__main__":
    main()
