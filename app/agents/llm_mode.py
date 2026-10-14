"""LLM mode for the agent workflow (work/OWNERSHIP.md piece 12; work/01 section D).

Same five agents, same tools (``app.agents.tools.TOOLS``), same run store and
``AgentStep`` records as the scripted workflow. Claude only chooses which tools
to call and writes each agent's short summary; every number comes from a tool.

Safety rules, enforced in code:

* A summary that contains a numeral no tool returned makes the whole run fail
  with ``LlmModeError``; the runner then re-runs the scripted workflow on the
  same run id and records ``mode="scripted"``. A wrong number never ships.
* The loop is bounded (``llm.MAX_STEPS`` model calls per agent), with per-call
  timeouts, and any API error, refusal or truncation also raises.
* Work orders are drafted by the ``draft_work_orders`` tool and stay
  ``pending_approval``. Nothing here approves anything.
* Step times are simulated time (the snapshot's ``now``), never the wall clock.

A finished run is cached by (goal, scope, state hash) in ``data/agent_cache.json``
so a rehearsed run can be replayed instantly (``scope["replay"]`` true or
``AGENT_CACHE_REPLAY=1``).
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Optional

from app.agents.runs import RUNS, RunStore
from app.agents.tools import TOOLS, call_tool
from app.contracts.models import AgentRun, AgentStep, Plan, WorkOrder
from app.copilot import llm
from app.copilot.tools import Tool
from app.solver import service

log = logging.getLogger(__name__)

ROLES = ["health", "scheduling", "logistics", "operations", "coordinator"]
GOAL_HINT = {
    "health": "Identify which components need attention and why.",
    "scheduling": "Derive the tasks and obtain the optimized schedule and the static baseline; compare them.",
    "logistics": "Check parts and technicians for the planned jobs and report shortages.",
    "operations": "Evaluate the optimized plan: downtime and flights affected.",
    "coordinator": "Call draft_work_orders for the current plan, then write the explanation in at most 120 words.",
}


class LlmModeError(RuntimeError):
    """The LLM run cannot be trusted or could not finish; the caller falls back to scripted mode."""


def llm_ready() -> bool:
    return llm.llm_available()


def _tools_for(role: str, missing: list[str]) -> dict[str, Tool]:
    """The registry's tools for one agent role, wrapped so a call returns the registry's own result dict."""
    out: dict[str, Tool] = {}
    for name, spec in TOOLS.items():
        if spec.agent != role:
            continue

        def make(tool_name: str):
            return lambda **kw: call_tool(tool_name, kw)

        out[name] = Tool(name, spec.description, spec.input_schema, make(name), role)

    def report(items: list[str]) -> dict:
        missing.extend(str(i) for i in items if str(i) not in missing)
        return {"data": {"recorded": len(items)}, "evidence": {"kind": "tool_call", "ref": "report_missing_info"}, "missing_info": []}

    out["report_missing_info"] = Tool(
        "report_missing_info", "Call this when the data cannot answer something instead of guessing.",
        {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "string"}}},
         "required": ["items"], "additionalProperties": False}, report, role)
    return out


def _summary_of(res: dict) -> str:
    if res.get("error"):
        return f"{res.get('tool', 'tool')} failed: {res['error']}"
    d = res.get("data")
    if isinstance(d, list):
        return f"{len(d)} item(s)"
    return "ok"


def _cache_key(goal: str, scope: dict, state) -> str:
    scope_part = json.dumps({k: v for k, v in scope.items() if k not in ("replay", "screen")}, sort_keys=True, default=str)  # the screen must not break a rehearsed replay
    return llm.RunCache.key(f"{goal}|{scope_part}", llm.state_hash(state))


def _restate(client: Any, system: str, user: str, text: str, bad: list[str]) -> str:
    """One follow-up without tools: the same summary minus the figures nobody returned. Empty string if that did not work."""
    try:
        resp = client.messages.create(
            model=llm.model_name(), max_tokens=1024, system=system,
            messages=[{"role": "user", "content": user}, {"role": "assistant", "content": text},
                      {"role": "user", "content": (f"That summary quotes figures that no tool returned ({', '.join(bad)}). "
                                                  "Rewrite it without them, keeping every figure a tool did return. Reply with the corrected summary only.")}],
        )
        return "\n".join(b.text.strip() for b in resp.content if getattr(b, "type", "") == "text" and b.text.strip())
    except Exception:  # noqa: BLE001  fall back to the scripted workflow, as before
        return ""


def run_llm(run_id: str, goal: str, scope: Optional[dict] = None, store: RunStore = RUNS, client: Any = None,
            cache: Optional[llm.RunCache] = None, replay_delay_s: float = 0.5) -> AgentRun:
    """Run the five agents with Claude. Raises ``LlmModeError`` (or any exception) on failure; never returns a partial run."""
    scope = scope or {}
    state, source = service.get_state()
    now = state.now
    cache = cache or llm.RunCache()
    key = _cache_key(goal, scope, state)
    if scope.get("replay") or os.environ.get("AGENT_CACHE_REPLAY") == "1":
        hit = cache.get(key)
        if hit:
            return _replay(run_id, hit, store, replay_delay_s)

    client = client or llm.make_client()
    missing: list[str] = []
    n = 0
    refs: dict[str, int] = {}
    summaries: dict[str, str] = {}
    seen_tools: dict[str, list[dict]] = {}

    def step(agent: str, kind: str, text: str = "", tool: Optional[str] = None, inp: Optional[dict] = None,
             out: Optional[dict] = None) -> int:
        nonlocal n
        n += 1
        store.append(run_id, AgentStep(n=n, agent=agent, kind=kind, tool=tool, input=inp, output=out, text=text, ts=now))  # type: ignore[arg-type]
        return n

    for role in ROLES:
        tools = _tools_for(role, missing)

        def on_call(c: llm.ToolCall, role=role) -> None:
            step(role, "tool_call", f"call {c.tool}", c.tool, c.input)
            out = c.output if isinstance(c.output, dict) else {"data": c.output}
            for m in out.get("missing_info", []) or []:
                if m not in missing:
                    missing.append(m)
            text = f"{c.tool} failed: {out.get('error')}" if (out.get("error") or not c.ok) else _summary_of({**out, "tool": c.tool})
            refs[c.tool] = step(role, "tool_result", text, c.tool, c.input, out)
            seen_tools.setdefault(c.tool, []).append(out)

        context = {"goal": goal, "scope": scope, "simulated_now": now.isoformat(), "earlier_agents": dict(summaries)}
        system = llm.SAFETY.format(role=f"{role} agent") + " " + llm.AGENT_PROMPTS.get(role, "") + " " + GOAL_HINT[role]
        user = f"Goal: {goal}\nContext: {json.dumps(context, default=str)}"
        loop = llm.run_tool_loop(client, system=system, messages=[{"role": "user", "content": user}], tools=tools,
                                 on_call=on_call, on_thought=lambda t, role=role: step(role, "thought", t))
        if loop.error:
            raise LlmModeError(f"{role}: {loop.error}")
        if role == "coordinator" and "draft_work_orders" not in seen_tools:
            # the model must not be able to skip the human-approval step: draft deterministically
            on_call(llm.ToolCall(n=0, tool="draft_work_orders", input={}, output=call_tool("draft_work_orders", {}), ok=True,
                                 agent="coordinator"))
        bad = llm.unsupported_numbers(loop.text, loop.sources([user]))
        if bad:
            # The model quoted a figure no tool returned. Ask once for a corrected summary rather than throw the whole run away.
            fixed = _restate(client, system, user, loop.text, bad)
            if fixed and not llm.unsupported_numbers(fixed, loop.sources([user])):
                loop.text = fixed
            else:
                raise LlmModeError(f"{role}: numbers not returned by any tool: {bad}")
        summaries[role] = loop.text
        step(role, "summary", loop.text)

    # ---- assemble the run from what the tools actually returned
    plan = None
    for out in reversed(seen_tools.get("solve_schedule", [])):
        d = out.get("data")
        if isinstance(d, dict) and d.get("mode") == "cpsat":
            plan = Plan.model_validate(d)
            break
    if plan is None:
        plan = service.ensure_plans(state, source)[0]
    orders: list[WorkOrder] = []
    for out in seen_tools.get("draft_work_orders", [])[-1:]:
        orders = [WorkOrder.model_validate(x) for x in (out.get("data") or [])]
    store.add_work_orders(run_id, orders)
    final = store.update(run_id, mode="llm", plan=plan, explanation=summaries["coordinator"],
                         missing_info=list(missing), status="done")
    cache.put(key, json.loads(final.model_dump_json()))
    return final


def _replay(run_id: str, hit: dict, store: RunStore, delay_s: float) -> AgentRun:
    """Re-emit a cached finished run step by step (for a rehearsed demo)."""
    cached = AgentRun.model_validate(hit)
    for s in cached.steps:
        if delay_s:
            time.sleep(delay_s)
        store.append(run_id, s)
    store.add_work_orders(run_id, cached.work_orders)
    return store.update(run_id, mode="llm", plan=cached.plan, explanation=cached.explanation,
                        missing_info=list(cached.missing_info) + ["replayed from the run cache"], status="done")
