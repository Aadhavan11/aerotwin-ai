import { useEffect, useRef, useState } from "react";
import { post, usePoll } from "../api/client";
import type { AgentRun, AgentStep, AuditEntry, ChatResponse, EvidenceRef, WorkOrder } from "../api/types";
import { AGENT_STYLE, fmtZ } from "../lib/format";
import { Btn, Chip } from "./ui";

const QUICK = [
  "Which aircraft are most likely to require maintenance in the next 72 hours?",
  "Why is SIM-017 prioritized over SIM-012?",
  "Find the best feasible maintenance windows for tomorrow.",
  "Which parts and technicians are required for these maintenance jobs?",
  "What changes if a maintenance facility becomes unavailable?",
  "Compare the current schedule with the optimized schedule.",
];
type Tab = "chat" | "agents" | "wo" | "audit";
type Msg = { who: "you" | "copilot"; text: string; resp?: ChatResponse };
const short = (o: unknown, n = 150) => { const s = JSON.stringify(o); return s.length > n ? s.slice(0, n) + "…" : s; };

function Evidence({ items }: { items: EvidenceRef[] }) {
  return <div className="mt-1 flex flex-wrap gap-1">{items.map((e, i) => <Chip key={i} tone="blue" title={e.note}>{e.kind}:{e.ref}</Chip>)}</div>;
}

function WorkOrderCard({ w, user, onDone }: { w: WorkOrder; user: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const decide = async (decision: "approve" | "reject") => { setBusy(true); try { await post(`/workorders/${w.id}/decision`, { decision, user, note: "" }); onDone(); } finally { setBusy(false); } };
  const tone = w.status === "approved" ? "text-emerald-700" : w.status === "rejected" ? "text-red-700" : "text-amber-700";
  return (
    <div className="rounded-lg border border-slate-200 p-2.5 text-sm">
      <div className="flex items-center justify-between gap-2"><b>{w.id}</b><span className={`text-xs font-semibold ${tone}`}>{w.status.replace("_", " ")}</span></div>
      <div>{w.description}</div>
      <div className="text-xs text-slate-500">{fmtZ(w.start)} to {fmtZ(w.end)} · plan {w.plan_id}{w.decided_by ? ` · ${w.decided_by}` : ""}</div>
      <Evidence items={w.evidence} />
      {w.status === "pending_approval" && <div className="mt-2 flex gap-2"><Btn tone="primary" disabled={busy} onClick={() => decide("approve")}>Approve</Btn><Btn disabled={busy} onClick={() => decide("reject")}>Reject</Btn></div>}
    </div>
  );
}

export default function CopilotDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("agents");
  const [user, setUser] = useState(() => { try { return localStorage.getItem("aerotwin.user") || "Shamit"; } catch { return "Shamit"; } });
  useEffect(() => { try { localStorage.setItem("aerotwin.user", user); } catch { /* ignore */ } }, [user]);

  // chat
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const ask = async (text: string) => {
    if (!text.trim()) return;
    setMsgs((m) => [...m, { who: "you", text }]); setInput(""); setThinking(true);
    try { const { data } = await post<ChatResponse>("/copilot/chat", { session_id: "web", message: text }); setMsgs((m) => [...m, { who: "copilot", text: data.answer, resp: data }]); }
    catch (e) { setMsgs((m) => [...m, { who: "copilot", text: `Error: ${(e as Error).message}` }]); } finally { setThinking(false); }
  };
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs, thinking]);

  // agents
  const [mode, setMode] = useState<"scripted" | "llm">("scripted");
  const [run, setRun] = useState<AgentRun | null>(null);
  const [runErr, setRunErr] = useState("");
  const start = async () => { setRunErr(""); setRun(null); try { const { data } = await post<AgentRun>("/agents/run", { goal: "Plan maintenance for the aircraft that need attention", scope: {}, mode }); setRun(data); } catch (e) { setRunErr(String((e as Error).message)); } };
  useEffect(() => {
    if (!run || run.status !== "running") return;
    const id = setInterval(async () => {
      try { const r = await fetch(`${import.meta.env.VITE_API ?? `${import.meta.env.BASE_URL}api`}/agents/runs/${run.id}`); if (r.ok) setRun((await r.json()) as AgentRun); } catch { /* keep polling */ }
    }, 1000);
    return () => clearInterval(id);
  }, [run?.id, run?.status]);

  const wo = usePoll<WorkOrder[]>(open && (tab === "wo" || tab === "agents") ? "/workorders" : null, 2500);
  const audit = usePoll<AuditEntry[]>(open && tab === "audit" ? "/audit" : null, 3000);

  const tabs: [Tab, string][] = [["agents", "Agent activity"], ["chat", "Copilot"], ["wo", "Work orders"], ["audit", "Audit"]];
  return (
    <aside className={`fixed bottom-0 right-0 top-[49px] z-10 flex w-full flex-col border-l border-slate-200 bg-white shadow-xl sm:w-[26rem] ${open ? "" : "hidden"}`}>
      <div className="flex items-center gap-1 border-b border-slate-200 px-2 pt-2">
        {tabs.map(([k, l]) => <button key={k} onClick={() => setTab(k)} className={`rounded-t-lg px-3 py-1.5 text-sm font-medium ${tab === k ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}>{l}</button>)}
        <button onClick={onClose} className="ml-auto px-2 text-slate-400 hover:text-slate-700" aria-label="Close">✕</button>
      </div>
      <div className="flex items-center gap-2 border-b border-slate-100 px-3 py-1.5 text-xs text-slate-500">Acting as <input value={user} onChange={(e) => setUser(e.target.value)} className="w-28 rounded border border-slate-300 px-1.5 py-0.5 text-slate-800" aria-label="Your name" /> <span>(recorded in the audit log)</span></div>

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {tab === "agents" && (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Btn tone="primary" onClick={start} disabled={run?.status === "running"}>{run?.status === "running" ? "Running…" : "Run agents"}</Btn>
              <select value={mode} onChange={(e) => setMode(e.target.value as "scripted" | "llm")} className="rounded border border-slate-300 px-2 py-1 text-sm"><option value="scripted">scripted (no LLM)</option><option value="llm">LLM mode</option></select>
              {run && <span className="text-xs text-slate-500">{run.id} · {run.mode}</span>}
            </div>
            {runErr && <div className="rounded bg-red-50 p-2 text-xs text-red-800">{runErr}</div>}
            {!run && <p className="text-sm text-slate-500">Health, Scheduling, Logistics, Operations and Coordinator agents call real tools, then draft work orders for a human to approve.</p>}
            {run?.steps.map((s: AgentStep) => (
              <div key={s.n} className="flex gap-2 text-sm">
                <span className={`mt-0.5 h-fit shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${AGENT_STYLE[s.agent] ?? "bg-slate-100"}`}>{s.agent}</span>
                <div className="min-w-0">
                  {s.kind === "tool_call" && <div className="font-mono text-xs text-slate-700">→ {s.tool}({short(s.input, 90)})</div>}
                  {s.kind === "tool_result" && <div className="break-words font-mono text-xs text-slate-500">← {s.tool}: {short(s.output)}</div>}
                  {(s.kind === "thought" || s.kind === "summary") && <div className={s.kind === "summary" ? "font-medium text-slate-900" : "italic text-slate-600"}>{s.text}</div>}
                </div>
              </div>))}
            {run?.status === "running" && <div className="text-xs text-slate-400">working…</div>}
            {run?.status === "done" && (
              <div className="space-y-2 border-t border-slate-200 pt-3">
                <div className="text-sm font-semibold">Recommendation</div>
                <p className="rounded-lg bg-emerald-50 p-2.5 text-sm text-emerald-950">{run.explanation}</p>
                {run.missing_info.length > 0 && <div className="rounded bg-amber-50 p-2 text-xs text-amber-900">Missing information: {run.missing_info.join("; ")}</div>}
                {run.plan && <div className="text-xs text-slate-600">Plan {run.plan.id}: feasible {String(run.plan.feasible)} · downtime {run.plan.metrics.downtime_h} h · flights affected {run.plan.metrics.flights_affected}</div>}
                <div className="text-sm font-semibold">Work orders (need a human)</div>
                {(wo.data ?? run.work_orders).map((w) => <WorkOrderCard key={w.id} w={w} user={user} onDone={wo.reload} />)}
              </div>)}
          </div>)}

        {tab === "chat" && (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-1.5">{QUICK.map((q) => <button key={q} onClick={() => ask(q)} className="rounded-full border border-slate-300 px-2.5 py-1 text-left text-xs text-slate-700 hover:bg-slate-100">{q}</button>)}</div>
            {msgs.map((m, i) => (
              <div key={i} className={m.who === "you" ? "text-right" : ""}>
                <div className={`inline-block max-w-[92%] rounded-xl px-3 py-2 text-left text-sm ${m.who === "you" ? "bg-slate-900 text-white" : "bg-slate-100 text-slate-900"}`}>{m.text}</div>
                {m.resp && <div className="mt-1 text-left">
                  {m.resp.evidence.length > 0 && <Evidence items={m.resp.evidence} />}
                  {m.resp.missing_info.length > 0 && <div className="mt-1 rounded bg-amber-50 p-1.5 text-xs text-amber-900">Missing information: {m.resp.missing_info.join("; ")}</div>}
                  {m.resp.proposed_work_orders.map((w) => <div key={w.id} className="mt-1.5"><WorkOrderCard w={w} user={user} onDone={wo.reload} /></div>)}
                </div>}
              </div>))}
            {thinking && <div className="text-xs text-slate-400">thinking…</div>}
            <div ref={end} />
          </div>)}

        {tab === "wo" && (<div className="space-y-2">{(wo.data ?? []).length === 0 && <p className="text-sm text-slate-500">No work orders yet. Run the agents first.</p>}{(wo.data ?? []).map((w) => <WorkOrderCard key={w.id} w={w} user={user} onDone={wo.reload} />)}</div>)}

        {tab === "audit" && (<ul className="space-y-1.5 text-sm">{(audit.data ?? []).map((a, i) => <li key={i} className="rounded border border-slate-100 p-2"><span className="text-xs text-slate-500">{fmtZ(a.ts)}</span> · <b>{a.actor}</b> · {a.action} <span className="text-slate-500">{a.ref}</span>{a.detail && <div className="text-xs text-slate-500">{a.detail}</div>}</li>)}</ul>)}
      </div>

      {tab === "chat" && (
        <form className="flex gap-2 border-t border-slate-200 p-3" onSubmit={(e) => { e.preventDefault(); ask(input); }}>
          <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask about the fleet, plans, parts…" className="min-w-0 flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm" />
          <Btn tone="primary" disabled={thinking}>Send</Btn>
        </form>)}
    </aside>
  );
}
