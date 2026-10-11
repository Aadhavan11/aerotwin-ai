import { useEffect, useRef, useState } from "react";
import { post, usePoll } from "../api/client";
import type { AgentRun, AgentStep, AuditEntry, ChatResponse, EvidenceRef, WorkOrder } from "../api/types";
import { fmtZ } from "../lib/format";
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
  return (
    <div className="dw-chips">
      {items.map((e, i) => (
        <Chip key={i} tone="blue" title={e.note}>
          {e.kind}:{e.ref}
        </Chip>
      ))}
    </div>
  );
}

function WorkOrderCard({ w, user, onDone }: { w: WorkOrder; user: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const decide = async (decision: "approve" | "reject") => {
    setBusy(true);
    setErr("");
    try {
      await post(`/workorders/${w.id}/decision`, { decision, user: user.trim(), note: "" });
      onDone();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const tone = w.status === "approved" ? "ok" : w.status === "rejected" ? "alert" : "watch";
  return (
    <div className="dw-card">
      <div className="dw-row">
        <strong className="mono">{w.id}</strong>
        <span className={`status s-${tone}`}>
          {w.status === "pending_approval" ? "Waiting for approval" : w.status === "approved" ? "Approved" : "Rejected"}
        </span>
      </div>
      <div style={{ margin: "6px 0", lineHeight: 1.5 }}>{w.description}</div>
      <div className="muted mono" style={{ fontSize: 11.5 }}>
        {fmtZ(w.start)} to {fmtZ(w.end)} · plan {w.plan_id}
        {w.decided_by ? ` · ${w.decided_by}` : ""}
      </div>
      <Evidence items={w.evidence} />
      {w.status === "pending_approval" && (
        <div className="dw-row" style={{ justifyContent: "flex-start", marginTop: 10 }}>
          <Btn tone="primary" disabled={busy || !user.trim()} onClick={() => decide("approve")} title={user.trim() ? "" : "Enter your name above first"}>
            Approve
          </Btn>
          <Btn disabled={busy || !user.trim()} onClick={() => decide("reject")}>
            Reject
          </Btn>
        </div>
      )}
      {err && <div className="reasons">{err}</div>}
    </div>
  );
}

export default function CopilotDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("agents");
  const [user, setUser] = useState(() => { try { return localStorage.getItem("aerotwin.user") ?? ""; } catch { return ""; } });
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
      try { const r = await fetch(`${import.meta.env.VITE_API ?? "/api"}/agents/runs/${run.id}`); if (r.ok) setRun((await r.json()) as AgentRun); } catch { /* keep polling */ }
    }, 1000);
    return () => clearInterval(id);
  }, [run?.id, run?.status]);

  const wo = usePoll<WorkOrder[]>(open && (tab === "wo" || tab === "agents") ? "/workorders" : null, 2500);
  const audit = usePoll<AuditEntry[]>(open && tab === "audit" ? "/audit" : null, 3000);

  const tabs: [Tab, string][] = [["agents", "Agents"], ["chat", "Copilot"], ["wo", "Work orders"], ["audit", "Audit"]];
  return (
    <aside className={`drawer ${open ? "" : "hidden"}`} aria-label="Copilot">
      <div className="drawer-head">
        <div className="seg" role="tablist">
          {tabs.map(([k, l]) => (
            <button key={k} role="tab" aria-pressed={tab === k} onClick={() => setTab(k)}>
              {l}
            </button>
          ))}
        </div>
        <button onClick={onClose} className="drawer-close" aria-label="Close">
          ✕
        </button>
      </div>
      <div className="drawer-user">
        Acting as <input value={user} placeholder="your name" onChange={(e) => setUser(e.target.value)} className="name-input" aria-label="Your name" />
        <span>recorded in the audit log</span>
      </div>

      <div className="drawer-body">
        {tab === "agents" && (
          <div className="space-y-3">
            <div className="dw-row" style={{ justifyContent: "flex-start" }}>
              <Btn tone="primary" onClick={start} disabled={run?.status === "running"}>
                {run?.status === "running" ? "Running…" : "Run agents"}
              </Btn>
              <select value={mode} onChange={(e) => setMode(e.target.value as "scripted" | "llm")} className="select">
                <option value="scripted">Scripted (no LLM)</option>
                <option value="llm">LLM mode</option>
              </select>
              {run && (
                <span className="muted mono" style={{ fontSize: 11.5 }}>
                  {run.id} · {run.mode}
                </span>
              )}
            </div>
            {runErr && <div className="reasons">{runErr}</div>}
            {!run && <p className="muted" style={{ margin: 0 }}>Health, Scheduling, Logistics, Operations and Coordinator agents call real tools, then draft work orders for a person to approve.</p>}
            {run?.steps.map((s: AgentStep) => (
              <div key={s.n} className="dw-step">
                <span className={`agent-tag ${s.agent}`}>{s.agent}</span>
                <div style={{ minWidth: 0 }}>
                  {s.kind === "tool_call" && <div className="mono dw-small">→ {s.tool}({short(s.input, 90)})</div>}
                  {s.kind === "tool_result" && <div className="mono dw-small muted" style={{ wordBreak: "break-word" }}>← {s.tool}: {short(s.output)}</div>}
                  {(s.kind === "thought" || s.kind === "summary") && <div style={{ fontWeight: s.kind === "summary" ? 600 : 400, color: s.kind === "summary" ? "var(--ink)" : "var(--ink-3)" }}>{s.text}</div>}
                </div>
              </div>
            ))}
            {run?.status === "running" && <div className="muted dw-small">Working…</div>}
            {run?.status === "done" && (
              <div className="space-y-3" style={{ borderTop: "1px solid var(--rule)", paddingTop: 12 }}>
                <div className="sub" style={{ margin: 0 }}>Recommendation</div>
                <p className="dw-card" style={{ margin: 0, lineHeight: 1.5 }}>{run.explanation}</p>
                {run.missing_info.length > 0 && <div className="reasons">Missing information: {run.missing_info.join("; ")}</div>}
                {run.plan && (
                  <div className="muted dw-small">
                    Plan {run.plan.id}: feasible {String(run.plan.feasible)} · downtime {run.plan.metrics.downtime_h} h · flights affected {run.plan.metrics.flights_affected}
                  </div>
                )}
                <div className="sub" style={{ margin: 0 }}>Work orders (need a person)</div>
                {(wo.data ?? run.work_orders).map((w) => <WorkOrderCard key={w.id} w={w} user={user} onDone={wo.reload} />)}
              </div>
            )}
          </div>
        )}

        {tab === "chat" && (
          <div className="space-y-3">
            <div className="dw-chips" style={{ marginTop: 0 }}>
              {QUICK.map((q) => (
                <button key={q} onClick={() => ask(q)} className="quick">
                  {q}
                </button>
              ))}
            </div>
            {msgs.map((m, i) => (
              <div key={i} style={{ textAlign: m.who === "you" ? "right" : "left" }}>
                <div className={`bubble ${m.who}`}>{m.text}</div>
                {m.resp && (
                  <div style={{ textAlign: "left" }}>
                    {m.resp.evidence.length > 0 && <Evidence items={m.resp.evidence} />}
                    {m.resp.missing_info.length > 0 && <div className="reasons">Missing information: {m.resp.missing_info.join("; ")}</div>}
                    {m.resp.proposed_work_orders.map((w) => (
                      <div key={w.id} style={{ marginTop: 8 }}>
                        <WorkOrderCard w={w} user={user} onDone={wo.reload} />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {thinking && <div className="muted dw-small">Thinking…</div>}
            <div ref={end} />
          </div>
        )}

        {tab === "wo" && (
          <div className="space-y-3">
            {(wo.data ?? []).length === 0 && <p className="muted" style={{ margin: 0 }}>No work orders yet. Run the agents first.</p>}
            {(wo.data ?? []).map((w) => <WorkOrderCard key={w.id} w={w} user={user} onDone={wo.reload} />)}
          </div>
        )}

        {tab === "audit" && (
          <ul className="plain-list">
            {(audit.data ?? []).map((a, i) => (
              <li key={i} style={{ display: "block" }}>
                <div className="muted mono dw-small">{fmtZ(a.ts)}</div>
                <div><strong>{a.actor}</strong> · {a.action} <span className="muted mono">{a.ref}</span></div>
                {a.detail && <div className="muted dw-small">{a.detail}</div>}
              </li>
            ))}
          </ul>
        )}
      </div>

      {tab === "chat" && (
        <form className="drawer-form" onSubmit={(e) => { e.preventDefault(); ask(input); }}>
          <input value={input} onChange={(e) => setInput(e.target.value)} placeholder="Ask about the fleet, plans, parts…" aria-label="Message" />
          <Btn tone="primary" disabled={thinking}>Send</Btn>
        </form>
      )}
    </aside>
  );
}
