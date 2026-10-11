"""The Maintenance screen's aggregate: tasks, slots, orders, parts, suggestions, history for one aircraft."""
import pytest
from app.contracts.models import DataQuality, HealthScore, RulEstimate, ScenarioEvent
from app.sim.integration import IntegratedSimulator
from app.twin import state as twin
from app.solver.store import STORE
from app.agents.runs import RUNS


@pytest.fixture(autouse=True)
def clean_twin(monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("MOCK_SOLVER", raising=False)
    from app.copilot import reset_all
    reset_all()
    twin.reset()
    STORE.reset()
    RUNS.reset()

    def score(kind, tail, cid, rows):
        fault = rows[-1].values.get("T50", 0) > 1100
        return HealthScore(tail=tail, component_id=cid, health=.3 if fault else .95, anomaly_score=.9 if fault else .02, is_anomaly=fault,
                           rul_cycles=RulEstimate(p10=22 if fault else 120, p50=25 if fault else 150, p90=30 if fault else 180),
                           data_quality=DataQuality(status="ok"), model_version="integration-test", top_signals=["T50"] if kind == "engine" else [])
    monkeypatch.setattr("app.ml.health.score_component", score)
    yield
    twin.reset()
    twin.set_now_provider(None)
    twin.set_ops_provider(None)
    STORE.reset()
    RUNS.reset()


def replay():
    return {1: [{"cycle": i + 1, "values": {"T50": 1000. + i}, "true_rul": 159 - i} for i in range(160)]}


def test_maintenance_view_for_an_aircraft_with_a_fault(tmp_path):
    from app.copilot import maintenance, workorders
    sim = IntegratedSimulator(str(tmp_path / "sim.db"), trajectories=replay())
    sim.step(24)
    sim.inject(ScenarioEvent(type="anomaly", at=sim.state.now, tail="SIM-017", component_id="SIM-017/engine_2"))
    out = maintenance.build("SIM-017")
    assert out["tail"] == "SIM-017" and out["plan_id"]
    item = next(i for i in out["items"] if i["component_id"] == "SIM-017/engine_2")
    assert item["title"] == "Engine borescope inspection" and item["source"] == "rul"
    assert item["card"]["id"] == "TC-ENG-BORESCOPE"
    a = item["assignment"]
    assert a["station"] == "DFW" and a["technician_id"]
    assert item["exposure"]["slack_to_deadline_h"] is not None
    lg = item["logistics"]
    assert lg["part"]["name"] == "BORESCOPE-KIT" and lg["part"]["on_hand"] >= 1 and lg["technicians_covering"] >= 1
    assert item["work_order"] is None
    assert out["ground_windows"] and out["history"]
    # drafting an order from the plan attaches it to the item, and it stays pending
    orders, _ = workorders.draft_from_plan(STORE.current, tails=["SIM-017"])
    again = maintenance.build("SIM-017")
    wo = next(i for i in again["items"] if i["component_id"] == "SIM-017/engine_2")["work_order"]
    assert wo["status"] == "pending_approval" and wo["id"] == orders[0].id


def test_unknown_tail_is_none_and_healthy_aircraft_has_no_open_fault(tmp_path):
    from app.copilot import maintenance
    IntegratedSimulator(str(tmp_path / "sim.db"), trajectories=replay())
    assert maintenance.build("SIM-999") is None
    out = maintenance.build("SIM-003")
    assert out is not None and all(i["source"] != "rul" for i in out["items"])


def test_queue_lists_every_open_task_most_urgent_first(tmp_path):
    from app.copilot import maintenance
    sim = IntegratedSimulator(str(tmp_path / "sim.db"), trajectories=replay())
    sim.step(24)
    sim.inject(ScenarioEvent(type="anomaly", at=sim.state.now, tail="SIM-017", component_id="SIM-017/engine_2"))
    q = maintenance.queue()
    assert q["rows"] and q["plan_id"]
    deadlines = [r["deadline"] for r in q["rows"]]
    assert deadlines == sorted(deadlines)
    row = next(r for r in q["rows"] if r["component_id"] == "SIM-017/engine_2")
    assert row["scheduled"] and row["station"] == "DFW" and row["work_order_status"] is None
