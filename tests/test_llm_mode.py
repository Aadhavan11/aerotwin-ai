"""LLM mode of the agent workflow, on Pardhu's registry and run store (piece 12 plugged into piece 11).

A fake client returns the SDK's block shapes; no network. The world is the scheduler's golden stand-in
(tests/conftest.py sets MOCK=1), so results are deterministic.
"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import copilot
from app.agents import llm_mode, runner
from app.agents.runs import RUNS
from app.agents.tools import TOOLS
from app.copilot import audit, llm, persist, workorders
from app.copilot.router import router as copilot_router
from app.solver.store import STORE


def block(type_, **kw):
    return SimpleNamespace(type=type_, **kw)


def text(s):
    return SimpleNamespace(content=[block("text", text=s)], stop_reason="end_turn")


def use(name, inp=None, id_="tu"):
    return SimpleNamespace(content=[block("tool_use", id=id_, name=name, input=inp or {})], stop_reason="tool_use")


class FakeClient:
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return self.script.pop(0)


def good_script(coordinator_drafts=True):
    s = [use("list_at_risk"), text("Health agent reviewed the components that need attention."),
         use("derive_tasks", id_="a"), use("solve_schedule", {"mode": "cpsat"}, "b"), use("solve_schedule", {"mode": "static_baseline"}, "c"),
         text("Scheduling agent obtained the optimized plan and the baseline."),
         use("shortage_report"), text("Logistics agent checked parts and technicians."),
         use("simulate_plan"), text("Operations agent evaluated the plan.")]
    s += [use("draft_work_orders"), text("Coordinator drafted work orders that wait for human approval.")] if coordinator_drafts \
        else [text("Coordinator wrote the explanation.")]
    return s


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    persist.set_persistence(False)
    copilot.reset_all()
    RUNS.reset()
    STORE.reset()
    monkeypatch.setenv("AGENT_CACHE_PATH", str(tmp_path / "cache.json"))
    monkeypatch.delenv("AGENT_CACHE_REPLAY", raising=False)
    yield
    persist.set_persistence(None)
    copilot.reset_all()


def new_run(goal="Plan maintenance", mode="llm"):
    return RUNS.create(goal, mode)


def test_llm_run_produces_the_same_shape_as_the_scripted_run():
    run = new_run()
    out = llm_mode.run_llm(run.id, "Plan maintenance", {}, client=FakeClient(good_script()), replay_delay_s=0)
    assert out.status == "done" and out.mode == "llm"
    assert [s.agent for s in out.steps if s.kind == "summary"] == ["health", "scheduling", "logistics", "operations", "coordinator"]
    assert [s.n for s in out.steps] == list(range(1, len(out.steps) + 1))
    assert {s.kind for s in out.steps} <= {"thought", "tool_call", "tool_result", "summary"}
    assert out.plan is not None and out.plan.mode == "cpsat" and out.plan.feasible
    assert out.work_orders and all(w.status == "pending_approval" for w in out.work_orders)
    assert out.explanation.startswith("Coordinator drafted")
    # tool results carry the registry's own evidence and are tagged with the registry tool name
    tr = next(s for s in out.steps if s.kind == "tool_result" and s.tool == "solve_schedule")
    assert tr.output and "evidence" in tr.output and tr.output["data"]["mode"] in ("cpsat", "static_baseline")


def test_agent_drafted_orders_are_in_the_shared_store_and_can_be_approved_over_http():
    run = new_run()
    out = llm_mode.run_llm(run.id, "Plan maintenance", {}, client=FakeClient(good_script()), replay_delay_s=0)
    ids = [w.id for w in out.work_orders]
    assert ids and [w.id for w in workorders.list_orders()] == ids
    app = FastAPI()
    app.include_router(copilot_router)
    c = TestClient(app)
    assert [w["id"] for w in c.get("/workorders").json()] == ids
    r = c.post(f"/workorders/{ids[0]}/decision", json={"decision": "approve", "user": "Aashrith", "note": "ok"})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert c.get("/audit").json()[0]["action"] == "work_order_approved"
    assert c.get("/workorders").json()[0]["status"] == "approved"


def test_the_model_cannot_skip_drafting_work_orders():
    run = new_run()
    out = llm_mode.run_llm(run.id, "Plan maintenance", {}, client=FakeClient(good_script(coordinator_drafts=False)), replay_delay_s=0)
    assert out.work_orders


def test_a_number_no_tool_returned_fails_the_run():
    script = good_script()
    script[1] = text("Health agent found a component with RUL p10 9999 cycles.")
    run = new_run()
    with pytest.raises(llm_mode.LlmModeError, match="9999"):
        llm_mode.run_llm(run.id, "Plan maintenance", {}, client=FakeClient(script), replay_delay_s=0)


def test_runner_falls_back_to_scripted_on_the_same_run_and_says_why(monkeypatch):
    script = good_script()
    script[1] = text("Health agent found RUL p10 9999 cycles.")
    monkeypatch.setattr(llm_mode, "llm_ready", lambda: True)
    monkeypatch.setattr(llm, "make_client", lambda *a, **k: FakeClient(script))
    out = runner.run_workflow("Plan maintenance", {}, "llm")
    assert out.status == "done" and out.mode == "scripted"
    assert any("LLM mode did not finish" in m for m in out.missing_info)
    assert out.steps[0].n == 1 and out.steps[0].agent == "health"          # steps were cleared, then the scripted run
    assert out.work_orders and all(w.status == "pending_approval" for w in out.work_orders)
    assert [w.id for w in workorders.list_orders()] == [w.id for w in out.work_orders]


def test_runner_uses_llm_mode_when_available(monkeypatch):
    monkeypatch.setattr(llm_mode, "llm_ready", lambda: True)
    monkeypatch.setattr(llm, "make_client", lambda *a, **k: FakeClient(good_script()))
    out = runner.run_workflow("Plan maintenance", {}, "llm")
    assert out.mode == "llm" and out.status == "done"
    assert not any("LLM mode" in m for m in out.missing_info)


def test_without_a_key_the_run_is_scripted_with_a_note(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    out = runner.run_workflow("Plan maintenance", {}, "llm")
    assert out.mode == "scripted" and out.status == "done"
    assert runner.LLM_NOTE in out.missing_info


def test_scripted_mode_is_untouched():
    out = runner.run_workflow("Plan maintenance", {}, "scripted")
    assert out.mode == "scripted" and not out.missing_info[:1] == [runner.LLM_NOTE]
    assert out.work_orders


def test_finished_runs_are_cached_and_replayed_without_calling_the_model():
    run = new_run()
    llm_mode.run_llm(run.id, "Plan maintenance", {}, client=FakeClient(good_script()), replay_delay_s=0)
    again = new_run()
    silent = FakeClient([])                                     # any model call would fail with IndexError
    out = llm_mode.run_llm(again.id, "Plan maintenance", {"replay": True}, client=silent, replay_delay_s=0)
    assert silent.calls == [] and out.status == "done" and out.mode == "llm"
    assert "replayed from the run cache" in out.missing_info
    assert len(out.steps) == len(RUNS.get(run.id).steps)


def test_request_shape_for_the_model_and_tool_specs():
    fc = FakeClient(good_script())
    run = new_run()
    llm_mode.run_llm(run.id, "Plan maintenance", {}, client=fc, replay_delay_s=0)
    kw = fc.calls[0]
    assert kw["model"] == "claude-sonnet-5-5" and "tool_choice" not in kw and "thinking" not in kw
    names = {t["name"] for t in kw["tools"]}
    assert names == {n for n, s in TOOLS.items() if s.agent == "health"} | {"report_missing_info"}
    assert "Never state a number that a tool did not return" in kw["system"]
