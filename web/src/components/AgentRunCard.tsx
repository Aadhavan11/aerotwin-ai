import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { get, post, usePoll } from "../api/client";
import type { AgentRun, AgentStep, HealthInfo } from "../api/types";
import { useAutoScroll } from "../lib/autoscroll";
import { useRole } from "../lib/role";
import { collectScreen } from "../lib/screen";
import { AGENT_STYLE, fmtH } from "../lib/format";
import { useLocation } from "react-router-dom";
import { Btn } from "./ui";

const short = (o: unknown, n = 110) => {
  const s = JSON.stringify(o);
  return s.length > n ? s.slice(0, n) + "…" : s;
};

/**
 * Five agents (health, scheduling, logistics, operations, coordinator) call the planner's tools and draft work orders for a person to decide.
 * Thoughts and summaries read as a timeline; the raw tool calls are one click away.
 */
export default function AgentRunCard({ autoStart = false }: { autoStart?: boolean }) {
  const loc = useLocation();
  const { role } = useRole();
  const health = usePoll<HealthInfo>("/health", 15000);
  const [mode, setMode] = useState<"scripted" | "llm">("scripted");
  const [run, setRun] = useState<AgentRun | null>(null);
  const [err, setErr] = useState("");
  const [tools, setTools] = useState(false);
  const llm = !!health.data?.llm_key_present;

  const start = async () => {
    setErr("");
    setRun(null);
    try {
      const { data } = await post<AgentRun>("/agents/run", { goal: "Plan maintenance for the aircraft that need attention", scope: {}, mode, screen: collectScreen(loc.pathname, loc.search, role.label) });
      setRun(data);
    } catch (e) {
      setErr(String((e as Error).message));
    }
  };
  useEffect(() => {
    if (autoStart) start();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoStart]);
  useEffect(() => {
    if (!run || run.status !== "running") return;
    const id = setInterval(async () => {
      try {
        const { data } = await get<AgentRun>(`/agents/runs/${run.id}`);
        setRun(data);
      } catch {
        /* keep polling */
      }
    }, 1000);
    return () => clearInterval(id);
  }, [run?.id, run?.status]);

  const steps = run?.steps ?? [];
  const [timeline] = useAutoScroll<HTMLDivElement>(`${run?.id}:${steps.length}:${run?.status}:${tools}`);
  const calls = steps.filter((s) => s.kind === "tool_call" || s.kind === "tool_result");
  const story = steps.filter((s) => s.kind === "thought" || s.kind === "summary");
  const shown = tools ? steps : story;
  return (
    <div>
      <div className="flex flex-wrap items-center gap-3">
        <Btn tone="primary" onClick={start} disabled={run?.status === "running"}>
          {run?.status === "running" ? "Running…" : run ? "Run again" : "Run the agents"}
        </Btn>
        <label className="flex items-center gap-2 text-xs text-slate-500">
          Mode
          <select value={mode} onChange={(e) => setMode(e.target.value as "scripted" | "llm")} className="rounded-md border border-slate-300 bg-transparent px-2 py-1 text-xs text-slate-800">
            <option value="scripted">Built-in (no language model)</option>
            <option value="llm" disabled={!llm}>
              Language model{llm ? "" : " (no key set)"}
            </option>
          </select>
        </label>
        {run && (
          <span className="text-xs text-slate-500">
            {run.id} · {run.mode} · {steps.length} steps
          </span>
        )}
      </div>
      {err && <p className="mt-3 text-sm text-red-600">{err}</p>}
      {!run && !err && (
        <p className="mt-4 max-w-2xl text-sm text-slate-500">
          The health, scheduling, logistics, operations and coordinator agents look at the fleet, solve the schedule, check parts and technicians, and draft work orders. Nothing is approved until a person does it.
        </p>
      )}
      {run && (
        <div className="mt-5">
          <div className="agent-scroll" ref={timeline}>
          <ol className="agent-timeline">
            {shown.map((s: AgentStep) => (
              <li key={s.n}>
                <span className={`agent-tag ${AGENT_STYLE[s.agent] ?? "bg-slate-100"}`}>{s.agent}</span>
                <div>
                  {s.kind === "tool_call" && (
                    <code>
                      → {s.tool}({short(s.input, 80)})
                    </code>
                  )}
                  {s.kind === "tool_result" && (
                    <code className="muted">
                      ← {s.tool}: {short(s.output)}
                    </code>
                  )}
                  {(s.kind === "thought" || s.kind === "summary") && <p className={s.kind === "summary" ? "summary" : "thought"}>{s.text}</p>}
                </div>
              </li>
            ))}
          </ol>
          </div>
          {run.status === "running" && <p className="mt-2 text-xs text-slate-500">Working…</p>}
          {calls.length > 0 && (
            <button className="mt-3 text-xs text-blue-600 hover:underline" onClick={() => setTools(!tools)}>
              {tools ? "Hide the tool calls" : `Show the ${calls.length / 2 | 0} tool calls and their results`}
            </button>
          )}
          {run.status === "done" && (
            <div className="mt-5 rounded-xl border border-slate-200 p-4">
              <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Recommendation</div>
              <p className="mt-2 text-sm text-slate-800">{run.explanation}</p>
              {run.plan && (
                <p className="mt-2 text-xs text-slate-500">
                  Plan {run.plan.id}: {run.plan.feasible ? "feasible" : "not feasible"} · downtime {fmtH(run.plan.metrics.downtime_h)} · {run.plan.metrics.flights_affected} flights affected
                </p>
              )}
              {run.missing_info.length > 0 && <p className="mt-2 text-xs text-amber-600">Missing information: {run.missing_info.join("; ")}</p>}
              <p className="mt-3 text-sm text-slate-700">
                {run.work_orders.length} work {run.work_orders.length === 1 ? "order is" : "orders are"} waiting for a person.{" "}
                <Link className="text-blue-600 hover:underline" to="/copilot?tab=orders">
                  Review them
                </Link>
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
