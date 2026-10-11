"""Every fixture parses into its contract model. If this fails, fixtures and models.py disagree."""
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from app.contracts.models import *  # noqa: F401,F403

F = Path(__file__).resolve().parent.parent / "fixtures"
CASES = {
    "fleet": FleetOverview, "stations": list[Station], "legs_golden": list[Leg], "tasks_golden": list[Task], "health_scores": list[HealthScore],
    "twin_SIM-017": TwinView, "twin_SIM-017_pre": TwinView, "plan_current": Plan, "plan_baseline": Plan, "replan_result": ReplanResult,
    "agent_run_golden": AgentRun, "workorders": list[WorkOrder], "audit": list[AuditEntry], "metrics": MetricsBundle,
}


@pytest.mark.parametrize("name,model", CASES.items())
def test_fixture_matches_contract(name, model):
    TypeAdapter(model).validate_python(json.loads((F / f"{name}.json").read_text()))


def test_chat_examples_match_contract():
    for ex in json.loads((F / "chat_examples.json").read_text()):
        ChatResponse.model_validate(ex["response"])


def test_golden_constraints_in_fixtures():
    """The golden scenario depends on these facts (work/OWNERSHIP.md piece 9)."""
    legs = TypeAdapter(list[Leg]).validate_python(json.loads((F / "legs_golden.json").read_text()))
    st = {s.iata: s for s in TypeAdapter(list[Station]).validate_python(json.loads((F / "stations.json").read_text()))}
    mine = sorted([lg for lg in legs if lg.tail == "SIM-017"], key=lambda x: x.sched_dep)
    long_dfw = [(a.sched_arr, b.sched_dep) for a, b in zip(mine, mine[1:]) if a.arr == "DFW" and (b.sched_dep - a.sched_arr).total_seconds() >= 6 * 3600]
    assert long_dfw, "SIM-017 needs a >= 6 h ground window at DFW"
    kit = {p.part: p for p in st["ATL"].parts}["BORESCOPE-KIT"]
    assert st["ATL"].hangar_slots > 0 and kit.qty == 0, "ATL has a hangar but no BORESCOPE-KIT on hand"
    dfw_kit = {p.part: p for p in st["DFW"].parts}["BORESCOPE-KIT"]
    assert dfw_kit.qty >= 1 and any("engine" in t.skills for t in st["DFW"].technicians)
