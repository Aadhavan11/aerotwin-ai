"""LLM tool loop, prompts, the number check and the run cache (INTEGRATION.md section 5).

Model: ``LLM_MODEL`` (default ``claude-sonnet-5-5``); credentials from the
environment (``ANTHROPIC_API_KEY``), never in code or logs.

Rules enforced here, not left to the prompt:

* Numbers come from tools. ``unsupported_numbers`` finds numerals in a model
  answer that no tool output (or the user's own message) contains; the caller
  then replaces the answer with a template built from the tool data.
* The loop is bounded: ``max_steps`` model calls, a per-call timeout, one retry.
* Any API failure, refusal or truncation returns ``LoopResult.error`` so the
  caller falls back to scripted mode. The demo must work without the LLM.

Sonnet 5.5 notes (current API): ``tool_choice`` is left at ``auto`` (forced
tool use is rejected), ``thinking`` is not sent (adaptive by default), no
sampling parameters are set, and assistant content blocks (including thinking)
are echoed back unchanged between tool turns.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from app.contracts.models import AgentStep, FleetState

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_STEPS = 8
MAX_TOOL_OUTPUT_CHARS = 12000


def model_name() -> str:
    return os.environ.get("LLM_MODEL") or DEFAULT_MODEL


def llm_available() -> bool:
    """True if credentials look present and the SDK imports. Does not call the API."""
    if (os.environ.get("COPILOT_MODE") or os.environ.get("AGENT_MODE") or "").lower() == "scripted":
        return False
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return False
    try:
        import anthropic  # noqa: F401
    except Exception:
        return False
    return True


def make_client(timeout_s: Optional[float] = None):
    import anthropic

    t = timeout_s if timeout_s is not None else float(os.environ.get("LLM_TIMEOUT_S", "30"))
    return anthropic.Anthropic(timeout=t, max_retries=1)


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------
SAFETY = (
    "You are the {role} in an aircraft maintenance planning system. This is a simulated prototype with synthetic data. "
    "You are advisory only; a human approves every action. "
    "Use ONLY the provided tools for facts and numbers. Never state a number that a tool did not return, and quote numbers exactly as returned "
    "(do not round, convert or compute new ones). Never propose maintenance later than a task deadline. "
    "If information is missing, say so and call report_missing_info instead of guessing. "
    "Do not invent part numbers, limits or intervals; use search_kb. Write plain sentences; no marketing language."
)

AGENT_PROMPTS = {
    "health": "Find aircraft needing attention, explain which sensors drive the anomaly, and report RUL with its uncertainty (p10 to p90).",
    "scheduling": "Find feasible windows and report the schedule with the planner's reasons; compare with the static baseline.",
    "logistics": "Check parts and technicians for the proposed assignments and report shortages.",
    "operations": "Simulate the plan and report flights affected and downtime.",
    "coordinator": "Merge the findings, flag conflicts, write a short explanation, and draft work orders for human approval.",
}

COPILOT_PROMPT = (
    "Answer the planner's question in at most 120 words. Name aircraft, components and stations as the tools return them. "
    "If a question cannot be answered from tool data, say what is missing. When asked why one aircraft comes before another, "
    "explain with conservative RUL (p10) and the planner's stated reason, and draft its work order. Work orders always stay pending approval."
)


# --------------------------------------------------------------------------
# Number check
# --------------------------------------------------------------------------
_LIST_MARK = re.compile(r"(?m)^\s*\d+[.)]\s+")
_NUM = re.compile(r"\d+(?:\.\d+)?")


def _norm(tok: str) -> str:
    if "." in tok:
        tok = tok.rstrip("0").rstrip(".")
    return tok.lstrip("0") or "0"


def extract_numbers(text: str) -> list[str]:
    """Numerals in a sentence, normalised ('017' -> '17', '45.90' -> '45.9'). List markers are ignored."""
    return [_norm(m) for m in _NUM.findall(_LIST_MARK.sub("", text))]


def unsupported_numbers(answer: str, sources: Iterable[str]) -> list[str]:
    """Numerals in ``answer`` that appear in none of ``sources`` (tool outputs, inputs, the user's message)."""
    pool: set[str] = set()
    for s in sources:
        pool.update(extract_numbers(s))
    return sorted({n for n in extract_numbers(answer) if n not in pool})


# --------------------------------------------------------------------------
# Tool loop
# --------------------------------------------------------------------------
@dataclass
class ToolCall:
    n: int
    tool: str
    input: dict
    output: Any
    ok: bool
    agent: str = "coordinator"


@dataclass
class LoopResult:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    thoughts: list[str] = field(default_factory=list)
    stop_reason: str = ""
    model_calls: int = 0
    error: Optional[str] = None

    def sources(self, extra: Iterable[str] = ()) -> list[str]:
        out = [json.dumps(c.output, default=str) for c in self.calls]
        out += [json.dumps(c.input, default=str) for c in self.calls]
        out += list(extra)
        return out


def _run_tool(tool, args: dict) -> tuple[Any, bool]:
    try:
        return tool.fn(**(args or {})), True
    except TypeError as exc:
        return {"error": f"bad arguments: {exc}"}, False
    except Exception as exc:  # a tool failing must not end the conversation
        log.warning("tool %s failed: %s", tool.name, exc)
        return {"error": f"{type(exc).__name__}: {exc}"}, False


def run_tool_loop(
    client: Any, *, system: str, messages: list[dict], tools: dict, model: Optional[str] = None,
    max_steps: int = MAX_STEPS, max_tokens: int = 4096, effort: Optional[str] = None,
    on_call: Optional[Callable[[ToolCall], None]] = None,
    on_thought: Optional[Callable[[str], None]] = None,
) -> LoopResult:
    """Manual tool loop: call, execute ``tool_use`` blocks, return ``tool_result`` blocks, repeat."""
    res = LoopResult()
    msgs = list(messages)
    specs = [t.spec() for t in tools.values()]
    effort = effort or os.environ.get("LLM_EFFORT", "medium")
    n = 0
    for _ in range(max_steps):
        try:
            kwargs: dict[str, Any] = dict(model=model or model_name(), max_tokens=max_tokens, system=system, messages=msgs)
            if specs:
                kwargs["tools"] = specs
            if effort:
                kwargs["output_config"] = {"effort": effort}
            resp = client.messages.create(**kwargs)
        except Exception as exc:  # timeouts, rate limits, auth, bad request: fall back to scripted
            res.error = f"{type(exc).__name__}: {exc}"
            return res
        res.model_calls += 1
        res.stop_reason = str(getattr(resp, "stop_reason", ""))
        blocks = list(getattr(resp, "content", []) or [])
        texts = [b.text for b in blocks if getattr(b, "type", "") == "text"]
        uses = [b for b in blocks if getattr(b, "type", "") == "tool_use"]
        if res.stop_reason in ("refusal", "max_tokens"):
            res.error = f"model stopped: {res.stop_reason}"
            return res
        if not uses:
            res.text = "\n".join(t.strip() for t in texts if t.strip())
            return res
        for t in texts:
            if t.strip():
                res.thoughts.append(t.strip())
                if on_thought:
                    on_thought(t.strip())
        msgs.append({"role": "assistant", "content": blocks})
        results = []
        for u in uses:
            n += 1
            tool = tools.get(u.name)
            if tool is None:
                out, ok = {"error": f"unknown tool {u.name!r}"}, False
            else:
                out, ok = _run_tool(tool, dict(u.input or {}))
            call = ToolCall(n=n, tool=u.name, input=dict(u.input or {}), output=out, ok=ok,
                            agent=getattr(tool, "agent", "coordinator"))
            res.calls.append(call)
            if on_call:
                on_call(call)
            body = json.dumps(out, default=str)
            if len(body) > MAX_TOOL_OUTPUT_CHARS:
                body = body[:MAX_TOOL_OUTPUT_CHARS] + '..."(truncated)"'
            r = {"type": "tool_result", "tool_use_id": u.id, "content": body}
            if not ok:
                r["is_error"] = True
            results.append(r)
        msgs.append({"role": "user", "content": results})
    res.error = f"no final answer after {max_steps} steps"
    return res


# --------------------------------------------------------------------------
# One agent (Health, Scheduling, Logistics, Operations, Coordinator)
# --------------------------------------------------------------------------
@dataclass
class AgentResult:
    agent: str
    steps: list[AgentStep]
    text: str
    numbers_ok: bool
    unsupported: list[str]
    error: Optional[str] = None
    model_calls: int = 0


def run_agent(
    agent: str, goal: str, context: Any, tools: dict, *, client: Any = None, ts_fn: Callable[[], datetime],
    start_n: int = 1, template: Optional[str] = None,
) -> AgentResult:
    """Run one agent's tool loop and return ``AgentStep`` records plus its text.

    ``template`` is the deterministic summary built from structured data. If the
    model's text contains a number no tool returned (or the call failed), the
    text is replaced by the template and ``numbers_ok`` is False. The caller
    switches the whole run to scripted mode on ``error``.
    """
    client = client or make_client()
    system = SAFETY.format(role=f"{agent} agent") + " " + AGENT_PROMPTS.get(agent, "")
    user = f"Goal: {goal}\nContext: {json.dumps(context, default=str)}"
    steps: list[AgentStep] = []
    n = start_n

    def add(kind: str, tool: Optional[str] = None, inp: Optional[dict] = None, out: Optional[dict] = None, text: str = "") -> None:
        nonlocal n
        steps.append(AgentStep(n=n, agent=agent, kind=kind, tool=tool, input=inp, output=out, text=text, ts=ts_fn()))
        n += 1

    def on_call(c: ToolCall) -> None:
        add("tool_call", c.tool, c.input)
        add("tool_result", c.tool, None, c.output if isinstance(c.output, dict) else {"result": c.output})

    loop = run_tool_loop(client, system=system, messages=[{"role": "user", "content": user}], tools=tools,
                         on_call=on_call, on_thought=lambda t: add("thought", text=t))
    if loop.error:
        return AgentResult(agent, steps, template or "", False, [], loop.error, loop.model_calls)
    bad = unsupported_numbers(loop.text, loop.sources([user]))
    text = loop.text
    if bad and template:
        log.warning("%s: numbers not in tool output %s; using template", agent, bad)
        text = template
    add("summary", text=text)
    return AgentResult(agent, steps, text, not bad, bad, None, loop.model_calls)


# --------------------------------------------------------------------------
# Cache of finished runs (a rehearsed run can be replayed instantly)
# --------------------------------------------------------------------------
def state_hash(state: FleetState) -> str:
    comp = [(c.component_id, c.status, c.health, c.rul_cycles.p10 if c.rul_cycles else None) for c in state.components]
    raw = json.dumps({"now": state.now.isoformat(), "components": comp}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class RunCache:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or Path(os.environ.get("AGENT_CACHE_PATH", "data/agent_cache.json"))

    @staticmethod
    def key(goal: str, state_hash_: str, mode: str = "llm") -> str:
        return hashlib.sha256(f"{mode}|{goal}|{state_hash_}".encode()).hexdigest()

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except Exception:
            return {}

    def get(self, key: str) -> Optional[dict]:
        return self._load().get(key)

    def put(self, key: str, value: dict) -> None:
        data = self._load()
        data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, default=str))
        tmp.replace(self.path)
