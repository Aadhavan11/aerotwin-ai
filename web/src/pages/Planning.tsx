import { useState } from "react";
import { Bar, BarChart, CartesianGrid, LabelList, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { post, usePoll } from "../api/client";
import type { AircraftSummary, Exposure, HealthInfo, Leg, Plan, PlanBundle, PlanMetrics, Station, WorkOrder } from "../api/types";
import AskPanel from "../components/AskPanel";
import MaintenanceCalendar, { NewRequestForm, type CalItem, type Prefill } from "../components/MaintenanceCalendar";
import MaintenanceRequest from "../components/MaintenanceRequest";
import SuggestedActions from "../components/SuggestedActions";
import { Btn, Card, ErrorBox, Loading } from "../components/ui";
import { fmtH, fmtUsd, fmtZ } from "../lib/format";

/** One metric, optimized against the static baseline, with how much better (or worse) the plan is. */
function Metric({ label, opt, base, fmt, lowerIsBetter = true, hint }: { label: string; opt: number; base: number; fmt: (n: number) => string; lowerIsBetter?: boolean; hint?: string }) {
  const diff = opt - base;
  const better = lowerIsBetter ? diff < 0 : diff > 0;
  const pct = base !== 0 ? Math.abs(Math.round((diff / base) * 100)) : 0;
  return (
    <div className="metric">
      <div className="metric-label">{label}</div>
      <div className="metric-pair">
        <div>
          <b className="text-emerald-600">{fmt(opt)}</b>
          <span>optimized</span>
        </div>
        <div>
          <b>{fmt(base)}</b>
          <span>baseline</span>
        </div>
      </div>
      <div className={`metric-delta ${diff === 0 ? "" : better ? "good" : "bad"}`}>
        {diff === 0 ? "same as the baseline" : `${better ? "↓" : "↑"} ${fmt(Math.abs(diff))}${pct ? ` · ${pct}% ${better ? "better" : "worse"}` : ""}`}
      </div>
      {hint && <small>{hint}</small>}
    </div>
  );
}

export default function Planning() {
  const plan = usePoll<PlanBundle>("/plan", 3000);
  const legs = usePoll<Leg[]>("/legs", 0);
  const health = usePoll<HealthInfo>("/health", 5000);
  const orders = usePoll<WorkOrder[]>("/workorders", 3000);
  const fleet = usePoll<{ aircraft: AircraftSummary[] }>("/fleet", 20000);
  const stations = usePoll<Station[]>("/stations", 0);
  const [show, setShow] = useState<"opt" | "base" | "both">("both");
  const [selKey, setSelKey] = useState<string | null>(null);
  const [creating, setCreating] = useState<Prefill | "blank" | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const data = plan.data,
    simNow = health.data?.sim_now;
  if (!data) return plan.error ? <ErrorBox error={plan.error} /> : <Loading what="plan" />;
  const cur: Plan = data.current,
    base: Plan = data.baseline;
  const all = orders.data ?? [];
  const sel = (() => {
    if (!selKey) return null;
    if (selKey.startsWith("wo:")) return all.find((w) => w.id === selKey.slice(3)) ?? null;
    return null;
  })();
  const selectedItem = selKey;
  const solve = async () => {
    setBusy(true);
    try {
      const { data: p } = await post<Plan>("/plan/solve", { mode: "cpsat" });
      setMsg(`Solved: ${p.mode}, feasible=${p.feasible}, ${p.solve_seconds.toFixed(2)} s, ${p.assignments.length} assignments`);
      plan.reload();
    } catch (e) {
      setMsg(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };
  const draft = async () => {
    setBusy(true);
    try {
      await post("/agents/run", { goal: "Plan maintenance for the aircraft that need attention", scope: {}, mode: "scripted" });
      setMsg("Drafting the requests… they appear on the calendar in a moment.");
      setTimeout(() => orders.reload(), 2500);
    } catch (e) {
      setMsg(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };
  const mc = (p: PlanMetrics) => p;
  const o = mc(cur.metrics),
    b = mc(base.metrics);
  const costRows = [
    { name: "Parts", Optimized: o.cost_parts_usd ?? 0, Baseline: b.cost_parts_usd ?? 0 },
    { name: "Labor", Optimized: o.cost_labor_usd ?? 0, Baseline: b.cost_labor_usd ?? 0 },
    { name: "Bay time", Optimized: o.cost_bay_usd ?? 0, Baseline: b.cost_bay_usd ?? 0 },
    { name: "Job overhead", Optimized: o.cost_overhead_usd ?? 0, Baseline: b.cost_overhead_usd ?? 0 },
    { name: "Shipping", Optimized: o.logistics_cost_usd, Baseline: b.logistics_cost_usd },
    { name: "Delay", Optimized: o.cost_delay_usd ?? 0, Baseline: b.cost_delay_usd ?? 0 },
    { name: "Not done in time", Optimized: o.cost_exposure_usd ?? 0, Baseline: b.cost_exposure_usd ?? 0 },
  ];
  const k = (n: number) => (n >= 1000 ? `$${Math.round(n / 1000)}k` : `$${Math.round(n)}`);

  return (
    <div className="space-y-4">
      <div className="page-heading">
        <div>
          <div className="eyebrow">Maintenance plan</div>
          <h1>Where and when the work happens</h1>
          <p>Each job fits around flights, technicians, parts and hangar space, and finishes before its deadline. Open a job to read the whole request, change it, then approve or deny.</p>
        </div>
      </div>
      <ErrorBox error={plan.error} />
      <AskPanel placeholder="Ask why a slot was chosen, or what changes if something closes…" suggestions={["Why was this slot chosen?", "What changes if the DFW hangar closes?", "Compare the current plan with the baseline."]} />
      {!cur.feasible && (
        <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-900">
          <b>No feasible plan.</b> {cur.infeasible_reasons.join(" · ") || "The solver reported infeasibility."}
          <div className="mt-1 text-xs">Hard constraints (deadlines, skills, parts, capacity) are never relaxed automatically. A human decides what to give up.</div>
        </div>
      )}
      <Card
        title="Maintenance calendar"
        right={
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-slate-500">plan {cur.id} · {cur.mode}</span>
            {(["both", "opt", "base"] as const).map((k2) => (
              <Btn key={k2} tone={show === k2 ? "primary" : "default"} onClick={() => setShow(k2)}>
                {k2 === "both" ? "Both" : k2 === "opt" ? "Optimized" : "Baseline"}
              </Btn>
            ))}
            <Btn onClick={() => setCreating("blank")}>New request</Btn>
            <Btn onClick={draft} disabled={busy} tone="ghost">
              Draft requests for the plan
            </Btn>
            <Btn onClick={solve} disabled={busy} tone="ghost">
              {busy ? "Working…" : "Re-solve"}
            </Btn>
          </div>
        }
      >
        {msg && <div className="mb-2 text-xs text-slate-600">{msg}</div>}
        {!legs.data || !simNow || !fleet.data ? (
          <Loading what="calendar" />
        ) : (
          <MaintenanceCalendar
            plan={data}
            legs={legs.data}
            orders={all}
            aircraft={fleet.data.aircraft}
            simNow={simNow}
            show={show}
            selected={selectedItem}
            onSelect={(i: CalItem) => {
              setCreating(null);
              setSelKey(i.wo ? `wo:${i.wo.id}` : `task:${i.taskId}`);
            }}
            onCreate={(p) => {
              setSelKey(null);
              setCreating(p);
            }}
          />
        )}
      </Card>

      {creating && fleet.data && (
        <NewRequestForm
          key={creating === "blank" ? "blank" : `${creating.tail}-${creating.start}`}
          prefill={creating === "blank" ? null : creating}
          aircraft={[...fleet.data.aircraft].sort((a, c) => a.tail.localeCompare(c.tail))}
          stations={(stations.data ?? []).map((s) => s.iata).sort()}
          onClose={() => setCreating(null)}
          onCreated={(w) => {
            setCreating(null);
            setSelKey(`wo:${w.id}`);
            orders.reload();
          }}
        />
      )}

      {sel ? (
        <MaintenanceRequest key={sel.id} w={sel} onDone={() => orders.reload()} />
      ) : (
        !creating && (
          <Card title="Maintenance request">
            <p className="text-sm text-slate-500">
              {selKey?.startsWith("task:")
                ? "The plan proposes this job, but nobody has requested it yet. Use “Draft requests for the plan” above and it becomes a request you can edit, approve or deny."
                : "Click a job on the calendar to read the whole request: what is being looked at, when and where, why it is needed and the part. Then change it, approve it or deny it."}
            </p>
          </Card>
        )
      )}

      <SuggestedActions
        onPlan={(p) => {
          setSelKey(null);
          setCreating(p);
          setTimeout(() => document.querySelector(".newreq")?.scrollIntoView({ behavior: "smooth", block: "start" }), 80);
        }}
      />

      <Card title="Optimized vs baseline">
        <div className="metrics">
          <Metric label="Estimated cost" opt={o.total_cost_usd ?? 0} base={b.total_cost_usd ?? 0} fmt={fmtUsd} hint="Parts, labor, bay time, job overhead, shipping, delay and the cost of jobs not done in time" />
          <Metric label="Downtime" opt={o.downtime_h} base={b.downtime_h} fmt={fmtH} hint="Hours past the planned ground time" />
          <Metric label="Flights affected" opt={o.flights_affected} base={b.flights_affected} fmt={(n) => String(Math.round(n))} />
          <Metric label="Jobs not done in time" opt={o.late_or_unscheduled} base={b.late_or_unscheduled} fmt={(n) => String(Math.round(n))} hint="Late or without a slot before the deadline" />
          <Metric label="Schedule changes" opt={o.schedule_changes} base={b.schedule_changes} fmt={(n) => String(Math.round(n))} />
          <Metric label="Technician use" opt={o.tech_utilization * 100} base={b.tech_utilization * 100} fmt={(n) => `${n.toFixed(1)}%`} lowerIsBetter={false} hint="Share of available technician hours" />
        </div>
        <div className="mt-4 h-56">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={costRows} margin={{ top: 18, right: 8, left: -4, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="name" fontSize={12} />
              <YAxis fontSize={12} tickFormatter={k} />
              <Tooltip formatter={(v) => fmtUsd(Number(v))} />
              <Legend />
              <Bar dataKey="Optimized" fill="#34d399" isAnimationActive={false}>
                <LabelList dataKey="Optimized" position="top" fontSize={11} formatter={(v) => (Number(v) ? k(Number(v)) : "")} />
              </Bar>
              <Bar dataKey="Baseline" fill="#ef4444" isAnimationActive={false}>
                <LabelList dataKey="Baseline" position="top" fontSize={11} formatter={(v) => (Number(v) ? k(Number(v)) : "")} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
        <p className="mt-2 text-xs text-slate-500">
          Baseline = static policy: each task at the aircraft's home base in the last window before its deadline. Costs come from a simulated price list (docs/COST-MODEL.md); two plans that
          do the same work pay the same parts, labor, bay time and overhead, so the difference is shipping, delay and the allowance for any job the plan cannot do before its deadline.
        </p>
      </Card>

      {(data.exposure?.optimized?.length ?? 0) > 0 && <RiskCard optimized={data.exposure!.optimized} baseline={data.exposure!.baseline ?? []} />}
    </div>
  );
}

const num = (v: number | null | undefined, unit = "") =>
  v === null || v === undefined ? "n/a" : `${v}${unit}`;

/** Cost metrics can tie while the risk differs: how long the fault stays open, how many flights fly with it open, how much margin is left. */
function RiskCard({ optimized, baseline }: { optimized: Exposure[]; baseline: Exposure[] }) {
  const base = new Map(baseline.map((e) => [e.task_id, e]));
  return (
    <Card title="Risk while the fault is open">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-slate-500">
              <th className="py-1 pr-3 font-medium">Task</th>
              <th className="py-1 pr-3 font-medium">Hours until fixed</th>
              <th className="py-1 pr-3 font-medium">Flights flown with the fault open</th>
              <th className="py-1 font-medium">Margin before the deadline</th>
            </tr>
          </thead>
          <tbody>
            {optimized.map((o) => {
              const b = base.get(o.task_id);
              return (
                <tr key={o.task_id} className="border-t border-slate-200 align-top">
                  <td className="py-2 pr-3">
                    <div className="font-medium text-slate-700">{o.tail}</div>
                    <div className="text-xs text-slate-500">{o.component_id.split("/").pop()}</div>
                  </td>
                  <td className="py-2 pr-3 tabular-nums">
                    <span className="font-semibold text-emerald-600">{num(o.hours_to_fix, " h")}</span>
                    <span className="text-slate-500"> optimized</span>
                    <div className="text-slate-500">{num(b?.hours_to_fix, " h")} baseline</div>
                  </td>
                  <td className="py-2 pr-3 tabular-nums">
                    <span className="font-semibold text-emerald-600">{num(o.flights_before_fix)}</span>
                    <span className="text-slate-500"> optimized</span>
                    <div className="text-slate-500">{num(b?.flights_before_fix)} baseline</div>
                  </td>
                  <td className="py-2 tabular-nums">
                    <span className="font-semibold text-emerald-600">{num(o.slack_to_deadline_h, " h")}</span>
                    <span className="text-slate-500"> optimized</span>
                    <div className="text-slate-500">{num(b?.slack_to_deadline_h, " h")} baseline</div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-xs text-slate-500">
        Computed from the two plans above (simulated data). Deadlines are hard constraints taken from the conservative p10 remaining life; margin is the time between the end of the job and that deadline.
      </p>
    </Card>
  );
}
