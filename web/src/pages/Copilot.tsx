import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { usePoll } from "../api/client";
import type { ChatResponse, FleetOverview, WorkOrder } from "../api/types";
import AgentRunCard from "../components/AgentRunCard";
import { AnswerView } from "../components/AskPanel";
import AuditTrail from "../components/AuditTrail";
import Icon from "../components/Icon";
import { Card } from "../components/ui";
import WorkOrderCard from "../components/WorkOrderCard";
import { useAsk } from "../lib/ask";
import { useAutoScroll } from "../lib/autoscroll";
import { STATUS_ORDER } from "../lib/format";

type Tab = "ask" | "agents" | "orders" | "audit";
type Turn = { q: string; about: string | null; a?: ChatResponse; err?: string };

const STARTERS = [
  "Which aircraft are most likely to require maintenance in the next 72 hours?",
  "Why is SIM-017 prioritized over SIM-012?",
  "Find the best feasible maintenance windows for tomorrow.",
  "Which parts and technicians are required for these maintenance jobs?",
  "What changes if a maintenance facility becomes unavailable?",
  "Compare the current schedule with the optimized schedule.",
];

// The conversation survives moving between pages and tabs (until the browser tab is closed or reloaded).
let saved: Turn[] = [];

/**
 * The AI workspace: ask anything about the fleet, watch the five agents work, decide on the work orders they draft, and read the audit trail.
 * The copilot sees the screen and everything the signed-in role can see; point it at one aircraft to ask about "it".
 */
export default function Copilot() {
  const [params, setParams] = useSearchParams();
  const tab = (["ask", "agents", "orders", "audit"].includes(params.get("tab") ?? "") ? params.get("tab") : "ask") as Tab;
  const go = (t: Tab) => setParams(t === "ask" ? {} : { tab: t }, { replace: true });
  const orders = usePoll<WorkOrder[]>("/workorders", 3000);
  const pending = (orders.data ?? []).filter((w) => w.status === "pending_approval").length;
  const tabs: [Tab, string, string?][] = [
    ["ask", "Ask"],
    ["agents", "Agents"],
    ["orders", "Work orders", pending ? String(pending) : undefined],
    ["audit", "Audit trail"],
  ];
  return (
    <div className="space-y-5">
      <div className="page-heading">
        <div>
          <div className="eyebrow">Copilot</div>
          <h1>Ask, delegate, decide</h1>
          <p>The copilot reads the same data you do and the screen you are on. The agents plan and draft; a person approves every work order.</p>
        </div>
      </div>
      <div className="seg" role="tablist" aria-label="Copilot sections">
        {tabs.map(([k, label, badge]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => go(k)}>
            {label}
            {badge && <i>{badge}</i>}
          </button>
        ))}
      </div>
      {tab === "ask" && <AskTab />}
      {tab === "agents" && (
        <Card title="Agent activity">
          <AgentRunCard autoStart={params.get("run") === "1"} />
        </Card>
      )}
      {tab === "orders" && <OrdersTab orders={orders.data ?? []} reload={orders.reload} error={orders.error} />}
      {tab === "audit" && (
        <Card title="Audit trail">
          <AuditTrail maxHeight="60vh" />
        </Card>
      )}
    </div>
  );
}

function AskTab() {
  const ask = useAsk();
  const [params, setParams] = useSearchParams();
  const fleet = usePoll<FleetOverview>("/fleet", 10000);
  const [turns, setTurns] = useState<Turn[]>(saved);
  const [text, setText] = useState("");
  const [focus, setFocus] = useState("");
  const [busy, setBusy] = useState(false);
  const [thread, pin] = useAutoScroll<HTMLDivElement>(turns);
  const input = useRef<HTMLInputElement>(null);
  const fromUrl = useRef(false);

  useEffect(() => {
    saved = turns;
  }, [turns]);
  useEffect(() => {
    input.current?.focus({ preventScroll: true });
  }, []);

  const send = (raw: string) => {
    const q = raw.trim();
    if (!q || busy) return;
    pin();
    const about = focus || null;
    setTurns((t) => [...t, { q, about }]);
    setText("");
    setBusy(true);
    ask(q, about)
      .then((a) => setTurns((t) => t.map((x, i) => (i === t.length - 1 && x.q === q ? { ...x, a } : x))))
      .catch((e) => setTurns((t) => t.map((x, i) => (i === t.length - 1 && x.q === q ? { ...x, err: String((e as Error).message) } : x))))
      .finally(() => setBusy(false));
  };

  // /copilot?q=… (from a link or the ask bar) asks straight away
  useEffect(() => {
    const q = params.get("q");
    if (q && !fromUrl.current) {
      fromUrl.current = true;
      send(q);
      const next = new URLSearchParams(params);
      next.delete("q");
      setParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const aircraft = [...(fleet.data?.aircraft ?? [])].sort((a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9) || a.tail.localeCompare(b.tail));
  const starters = focus ? [`What is wrong with ${focus}?`, `When does ${focus} have to be fixed?`, `What happens if we delay the maintenance on ${focus}?`, ...STARTERS.slice(0, 2)] : STARTERS;

  return (
    <div className="copilot-chat">
      <div className="chat-bar">
        <label>
          Focus on
          <select value={focus} onChange={(e) => setFocus(e.target.value)} aria-label="Focus the copilot on one aircraft">
            <option value="">The whole fleet</option>
            {aircraft.map((a) => (
              <option key={a.tail} value={a.tail}>
                {a.tail} · {a.status.replace("_", " ")}
              </option>
            ))}
          </select>
        </label>
        {turns.length > 0 && (
          <button className="chat-clear" onClick={() => setTurns([])}>
            Clear the conversation
          </button>
        )}
      </div>
      <div className="chat-thread" ref={thread} aria-live="polite">
        {turns.length === 0 ? (
          <div className="chat-empty">
            <Icon name="spark" size={22} />
            <h2>What do you want to know?</h2>
            <p>Ask about an aircraft, a plan, parts, technicians or what happens if something changes. Answers cite the data they come from.</p>
          </div>
        ) : (
          turns.map((t, i) => (
            <div className="turn" key={i}>
              <div className="turn-q">
                <span>{t.q}</span>
                {t.about && <small>about {t.about}</small>}
              </div>
              {t.a ? <AnswerView resp={t.a} /> : t.err ? <p className="ask-missing">{t.err}</p> : <p className="ask-thinking">Looking at the data…</p>}
            </div>
          ))
        )}
      </div>
      {!busy && (
        <div className="chat-starters">
          {starters.slice(0, turns.length === 0 ? 6 : 3).map((s) => (
            <button key={s} onClick={() => send(s)}>
              {s}
            </button>
          ))}
        </div>
      )}
      <form
        className="chat-composer"
        onSubmit={(e) => {
          e.preventDefault();
          send(text);
          input.current?.focus();
        }}
      >
        <Icon name="spark" size={16} />
        <input
          ref={input}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={focus ? `Ask about ${focus}…` : "Ask about the fleet, plans, parts, technicians…"}
          aria-label="Ask the copilot"
        />
        <button type="submit" disabled={busy || !text.trim()}>
          {busy ? "Thinking…" : "Ask"}
        </button>
      </form>
    </div>
  );
}

function OrdersTab({ orders, reload, error }: { orders: WorkOrder[]; reload: () => void; error?: string }) {
  const [show, setShow] = useState<"pending_approval" | "approved" | "rejected" | "all">("pending_approval");
  const count = (s: string) => orders.filter((w) => w.status === s).length;
  const rows = orders.filter((w) => show === "all" || w.status === show);
  const filters: [typeof show, string][] = [
    ["pending_approval", `Pending ${count("pending_approval")}`],
    ["approved", `Approved ${count("approved")}`],
    ["rejected", `Rejected ${count("rejected")}`],
    ["all", `All ${orders.length}`],
  ];
  return (
    <Card title="Work orders">
      <div className="seg small mb-4" role="tablist">
        {filters.map(([k, label]) => (
          <button key={k} role="tab" aria-selected={show === k} aria-pressed={show === k} className={show === k ? "on" : ""} onClick={() => setShow(k)}>
            {label}
          </button>
        ))}
      </div>
      {error && <p className="mb-3 text-sm text-red-600">{error}</p>}
      {rows.length === 0 ? (
        <p className="text-sm text-slate-500">
          {orders.length === 0 ? "No work orders yet. Run the agents and they draft them here." : "Nothing in this list."}
        </p>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {rows.map((w) => (
            <WorkOrderCard key={w.id} w={w} onDone={reload} />
          ))}
        </div>
      )}
    </Card>
  );
}
