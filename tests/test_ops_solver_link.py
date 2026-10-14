"""LLP limits and MEL deferrals as solver tasks (flags default OFF), and the completion hooks that close the loop."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import db, settings
from app.contracts.models import WorkOrder
from app.copilot import persist, workorders
from app.ops import llp, mel
from app.solver import derive_tasks, links, solve
from app.solver.golden import GOLDEN_TAIL, golden_state
from app.solver.store import STORE
from app.solver.tasks import derive_tasks_ex
from tests.solver_helpers import hard_violations

SNAPSHOT = Path(__file__).parent / "snapshots" / "golden_tasks.json"
TARGET = "SIM-005"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("MOCK", raising=False)
    monkeypatch.delenv("MOCK_SOLVER", raising=False)
    monkeypatch.setattr(settings, "LLP_TASKS", False)
    monkeypatch.setattr(settings, "MEL_TASKS", False)
    monkeypatch.setattr(settings, "DB_PATH", tmp_path / "link.sqlite")
    monkeypatch.setattr(persist, "_OVERRIDE", True)
    db.init_db()
    db.set_sim_now(golden_state().now)
    STORE.reset()
    llp.clear_replacements()
    yield
    llp.clear_replacements()
    STORE.reset()


@pytest.fixture(scope="module")
def golden():
    return golden_state()


def _near_limit_part(now: datetime, days: float = 4.0) -> dict:
    return {"tail": TARGET, "position": "engine_1:hpt_disk", "part_key": "hpt_disk", "label": "HPT disk", "ata": "72-50",
            "scope": "engine_1", "part_no": "FX-T-1234", "serial": "T000001", "cycle_limit": 20000.0, "csn": 19900.0,
            "remaining_cycles": 100.0, "remaining_days": days, "limit_date": (now + timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit_driver": "cycles", "status": "alert"}


def _only_target(part: dict, monkeypatch) -> None:
    monkeypatch.setattr(llp, "tail_parts", lambda t, as_of: [part] if t.tail == TARGET else [])


def test_flags_off_tasks_are_identical_to_the_head_snapshot(golden):
    now = [json.loads(t.model_dump_json()) for t in derive_tasks(golden)]
    assert now == json.loads(SNAPSHOT.read_text())


def test_llp_flag_creates_task_with_limit_deadline_and_plan_schedules_it_before(golden, monkeypatch):
    part = _near_limit_part(golden.now)
    _only_target(part, monkeypatch)
    monkeypatch.setattr(settings, "LLP_TASKS", True)
    tasks = derive_tasks(golden)
    t = next(x for x in tasks if x.source == "llp")
    assert t.id == f"T-LLP-{TARGET}-engine_1-hpt_disk" == links.llp_task_id(TARGET, "engine_1:hpt_disk")
    assert t.deadline == golden.now + timedelta(days=4) and t.required_skill == "engine" and t.synthetic
    assert links.parse_task_ref(t.id) == {"source": "llp", "tail": TARGET, "position": "engine_1:hpt_disk"}
    assert len(tasks) == 6 and {x.id for x in tasks} - {t.id} == {x["id"] for x in json.loads(SNAPSHOT.read_text())}
    plan = solve(golden, tasks, mode="cpsat")
    assert plan.feasible and plan.metrics.late_or_unscheduled == 0 and hard_violations(golden, tasks, plan) == []
    a = next(x for x in plan.assignments if x.task_id == t.id)
    assert a.end <= t.deadline


def test_llp_watch_outside_horizon_gives_no_task_but_watch_inside_does(golden, monkeypatch):
    far = {**_near_limit_part(golden.now, days=40.0), "status": "watch"}
    _only_target(far, monkeypatch)
    assert derive_tasks_ex(golden, llp_tasks=True).tasks == derive_tasks_ex(golden).tasks
    near = {**_near_limit_part(golden.now, days=3.0), "status": "watch"}
    _only_target(near, monkeypatch)
    assert any(t.source == "llp" for t in derive_tasks_ex(golden, llp_tasks=True).tasks)


def test_mel_flag_creates_rectification_task_with_expiry_deadline(golden, monkeypatch):
    item = mel.create_item(TARGET, "anti-skid", "fault", "Fictional defect", "Planner")
    assert item.category == "C"
    monkeypatch.setattr(settings, "MEL_TASKS", True)
    tasks = derive_tasks(golden)
    t = next(x for x in tasks if x.source == "mel")
    assert t.id == f"T-MEL-{item.id.removeprefix('MEL-')}" and t.deadline == item.expires_at and t.tail == TARGET
    assert t.required_skill == "gear" and t.required_part == "BRAKE-ASSY"   # from the brakes component-kind card
    assert links.parse_task_ref(t.id) == {"source": "mel", "id": item.id}
    plan = solve(golden, tasks, mode="cpsat")
    assert plan.infeasible_reasons == [] and plan.feasible
    assert any(a.task_id == t.id and a.end <= t.deadline for a in plan.assignments)


def test_completing_llp_work_resets_the_part():
    task_id = "T-LLP-SIM-001-engine_1-hpt_disk"
    done = datetime(2026, 10, 13, tzinfo=timezone.utc)
    assert links.on_task_completed(task_id, done, "WO-1")
    rep = llp._replacements[("SIM-001", "engine_1:hpt_disk")]
    assert rep["when"] == done
    assert links.on_task_completed("T-SIM-017-engine_2-001", done) is None          # ordinary restoration: nothing extra
    assert ("SIM-017", "engine_2") not in llp._replacements


def test_completing_mel_work_closes_the_item_via_complete_due():
    item = mel.create_item(TARGET, "anti-skid", "fault", "Fictional defect", "Planner")
    start = golden_state().now
    wo = WorkOrder(id="WO-LINK-1", tail=TARGET, task_id=links.mel_task_id(item.id), station="DFW", start=start, end=start + timedelta(hours=3),
                   description="MEL rectification", status="approved", plan_id="P-X", evidence=[])
    workorders.reset()
    workorders._save(wo)
    done = workorders.complete_due(start + timedelta(hours=4))
    assert [w.id for w in done] == ["WO-LINK-1"]
    got = mel.get_item(item.id)
    assert got.status == "rectified"


def test_hook_is_tolerant_of_unknown_or_closed_items():
    assert links.on_task_completed("T-MEL-9999", datetime(2026, 10, 13, tzinfo=timezone.utc)) is None
    assert links.on_task_completed("T-LLP-SIM-999-engine_1-hpt_disk", datetime(2026, 10, 13, tzinfo=timezone.utc)) is None


def test_both_flags_on_golden_fleet_solves_within_budget_and_keeps_the_golden_assignment(golden, monkeypatch):
    base_tasks = derive_tasks(golden)
    base = solve(golden, base_tasks, mode="cpsat")
    monkeypatch.setattr(settings, "LLP_TASKS", True)
    monkeypatch.setattr(settings, "MEL_TASKS", True)
    tasks = derive_tasks(golden)
    # The seeded golden fleet has LLPs past their limit (nose gear legs): they DO become tasks, so the plan is a superset.
    extra = [t for t in tasks if t.source == "llp"]
    assert extra and all(t.deadline > golden.now for t in extra)
    plan = solve(golden, tasks, mode="cpsat", time_limit_s=5.0)
    assert plan.feasible and plan.solve_seconds < 5.0 and hard_violations(golden, tasks, plan) == []

    def key(p):
        return next((a.station, a.start, a.end, a.technician_id) for a in p.assignments if a.task_id == "T-SIM-017-engine_2-001")

    assert GOLDEN_TAIL == "SIM-017" and key(plan) == key(base)
