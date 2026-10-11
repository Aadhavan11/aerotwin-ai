import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { post, usePoll } from "../api/client";
import type {
  Assignment,
  HealthInfo,
  Leg,
  Plan,
  PlanBundle,
  Exposure,
  PlanMetrics,
} from "../api/types";
import { Btn, Card, Chip, ErrorBox, Loading } from "../components/ui";
import { STATION_COLORS, fmtH, fmtUsd, fmtZ } from "../lib/format";

const LABEL_W = 104,
  CH_W = 1000,
  HOURS = 132,
  ROW_H = 88;
const STATIONS = ["ATL", "DFW", "ORD", "DEN", "LAX", "JFK", "MIA", "SEA"];
const colorOf = (s: string) =>
  STATION_COLORS[Math.max(0, STATIONS.indexOf(s)) % STATION_COLORS.length];
type Sel = { kind: "opt" | "base"; a: Assignment } | null;

export default function Planning() {
  const plan = usePoll<PlanBundle>("/plan", 3000);
  const legs = usePoll<Leg[]>("/legs", 0);
  const health = usePoll<HealthInfo>("/health", 5000);
  const [show, setShow] = useState<"opt" | "base" | "both">("both");
  const [sel, setSel] = useState<Sel>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const data = plan.data,
    simNow = health.data?.sim_now;
  const g = useMemo(() => {
    if (!data || !legs.data || !simNow) return null;
    const t0 = Math.floor(new Date(simNow).getTime() / 3_600_000) * 3_600_000;
    const x = (iso: string | number) =>
      LABEL_W +
      (((typeof iso === "number" ? iso : new Date(iso).getTime()) - t0) /
        3_600_000) *
        (CH_W / HOURS);
    const tails = [
      ...new Set(
        [...data.current.assignments, ...data.baseline.assignments].map(
          (a) => a.tail,
        ),
      ),
    ].sort();
    const rows = tails.map((tail, i) => {
      // Only legs still to fly: earlier legs would drag the first ground window left over the row label
      const ls = legs
        .data!.filter((l) => l.tail === tail && new Date(l.sched_arr).getTime() > t0)
        .sort((a, b) => a.sched_dep.localeCompare(b.sched_dep));
      const wins: { station: string; s: number; e: number }[] = [];
      let prev = t0;
      for (const l of ls) {
        const d = new Date(l.sched_dep).getTime();
        if (d > prev) wins.push({ station: l.dep, s: prev, e: d });
        prev = Math.max(prev, new Date(l.sched_arr).getTime());
      }
      return { tail, y: 26 + i * ROW_H, ls, wins };
    });
    const days: number[] = [];
    for (
      let d = Math.ceil(t0 / 86_400_000) * 86_400_000;
      d < t0 + HOURS * 3_600_000;
      d += 86_400_000
    )
      days.push(d);
    return { t0, x, rows, days, height: 26 + rows.length * ROW_H + 6 };
  }, [data, legs.data, simNow]);

  if (!data)
    return plan.error ? (
      <ErrorBox error={plan.error} />
    ) : (
      <Loading what="plan" />
    );
  const cur: Plan = data.current,
    base: Plan = data.baseline;
  const m = (p: PlanMetrics) => p;
  const solve = async () => {
    setBusy(true);
    try {
      const { data: p } = await post<Plan>("/plan/solve", { mode: "cpsat" });
      setMsg(
        `Solved: ${p.mode}, feasible=${p.feasible}, ${p.solve_seconds.toFixed(2)} s, ${p.assignments.length} assignments`,
      );
      plan.reload();
    } catch (e) {
      setMsg(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };
  const chart = [
    {
      name: "Downtime (h)",
      Optimized: m(cur.metrics).downtime_h,
      Baseline: m(base.metrics).downtime_h,
    },
    {
      name: "Flights affected",
      Optimized: cur.metrics.flights_affected,
      Baseline: base.metrics.flights_affected,
    },
    {
      name: "Schedule changes",
      Optimized: cur.metrics.schedule_changes,
      Baseline: base.metrics.schedule_changes,
    },
  ];
  const taskLabel = (id: string) => id.replace(/^T-/, "").replace(/-\d+$/, "");

  return (
    <div className="space-y-4">
      <div className="page-heading">
        <div>
          <div className="eyebrow">Maintenance plan</div>
          <h1>Where and when the work happens</h1>
          <p>Each job fits around flights, technicians, parts and hangar space, and finishes before its deadline.</p>
        </div>
      </div>
      <ErrorBox error={plan.error} />
      {!cur.feasible && (
        <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-900">
          <b>No feasible plan.</b>{" "}
          {cur.infeasible_reasons.join(" · ") ||
            "The solver reported infeasibility."}
          <div className="mt-1 text-xs">
            Hard constraints (deadlines, skills, parts, capacity) are never
            relaxed automatically. A human decides what to give up.
          </div>
        </div>
      )}
      <div className="facts">
        {[
          [
            "Downtime",
            fmtH(cur.metrics.downtime_h),
            fmtH(base.metrics.downtime_h),
          ],
          [
            "Flights affected",
            String(cur.metrics.flights_affected),
            String(base.metrics.flights_affected),
          ],
          [
            "Logistics cost",
            fmtUsd(cur.metrics.logistics_cost_usd),
            fmtUsd(base.metrics.logistics_cost_usd),
          ],
          [
            "Tasks scheduled",
            `${cur.metrics.tasks_scheduled}/${cur.metrics.tasks_total}`,
            `${base.metrics.tasks_scheduled}/${base.metrics.tasks_total}`,
          ],
          [
            "Solve time",
            `${cur.solve_seconds.toFixed(2)} s`,
            `${base.solve_seconds.toFixed(2)} s`,
          ],
        ].map(([label, a, b]) => (
          <div key={label} className="fact">
            <div className="fact-label">{label}</div>
            <div className="fact-value text-emerald-700">{a}</div>
            <div className="fact-hint">
              optimized · {b} baseline
            </div>
          </div>
        ))}
      </div>

      <Card
        title={`Maintenance timeline (next ${(HOURS / 24) | 0}.5 days, UTC)`}
        right={
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="text-slate-500">
              plan {cur.id} · {cur.mode}
            </span>
            {(["both", "opt", "base"] as const).map((k) => (
              <Btn
                key={k}
                tone={show === k ? "primary" : "default"}
                onClick={() => setShow(k)}
              >
                {k === "both" ? "Both" : k === "opt" ? "Optimized" : "Baseline"}
              </Btn>
            ))}
            <Btn onClick={solve} disabled={busy}>
              {busy ? "Solving…" : "Re-solve (cpsat)"}
            </Btn>
          </div>
        }
      >
        {msg && <div className="mb-2 text-xs text-slate-600">{msg}</div>}
        {!g ? (
          <Loading what="timeline" />
        ) : (
          <div className="overflow-x-auto">
            <svg
              viewBox={`0 0 ${LABEL_W + CH_W + 8} ${g.height}`}
              className="min-w-[860px] w-full"
              role="img"
              aria-label="Maintenance Gantt chart"
            >
              <defs>
                <pattern
                  id="hatch"
                  width="6"
                  height="6"
                  patternUnits="userSpaceOnUse"
                  patternTransform="rotate(45)"
                >
                  <rect width="6" height="6" fill="#fee2e2" />
                  <line
                    x1="0"
                    y1="0"
                    x2="0"
                    y2="6"
                    stroke="#ef4444"
                    strokeWidth="2"
                  />
                </pattern>
              </defs>
              {g.days.map((d) => (
                <g key={d}>
                  <line
                    x1={g.x(d)}
                    x2={g.x(d)}
                    y1={18}
                    y2={g.height}
                    stroke="#34343f"
                    strokeDasharray="3 4"
                  />
                  <text x={g.x(d) + 3} y={13} fontSize={11} fill="#8d8d9b">
                    {fmtZ(new Date(d).toISOString()).slice(0, 6)}
                  </text>
                </g>
              ))}
              {g.rows.map((r) => (
                <g key={r.tail}>
                  <text
                    x={4}
                    y={r.y + 40}
                    fontSize={13}
                    fontWeight={600}
                    fill="#e3e3ec"
                  >
                    {r.tail}
                  </text>
                  {r.wins.map((w, i) => (
                    <g key={i}>
                      <rect
                        x={g.x(w.s)}
                        y={r.y}
                        width={Math.max(0, g.x(w.e) - g.x(w.s))}
                        height={ROW_H - 12}
                        fill="#15151e"
                        stroke="#2a2a35"
                      />
                      {g.x(w.e) - g.x(w.s) > 44 && (
                        <text
                          x={g.x(w.s) + 3}
                          y={r.y + 12}
                          fontSize={10}
                          fill="#9d9daa"
                        >
                          {w.station} {((w.e - w.s) / 3.6e6).toFixed(1)}h
                        </text>
                      )}
                    </g>
                  ))}
                  {r.ls.map((l) => (
                    <rect
                      key={l.id}
                      x={g.x(l.sched_dep)}
                      y={r.y + ROW_H - 10}
                      width={Math.max(1, g.x(l.sched_arr) - g.x(l.sched_dep))}
                      height={6}
                      fill="#7a7a88"
                    >
                      <title>{`${l.dep}→${l.arr} ${fmtZ(l.sched_dep)}`}</title>
                    </rect>
                  ))}
                  {show !== "base" &&
                    cur.assignments
                      .filter((a) => a.tail === r.tail)
                      .map((a) => (
                        <g
                          key={a.task_id}
                          className="cursor-pointer"
                          onClick={() => setSel({ kind: "opt", a })}
                        >
                          <rect
                            x={g.x(a.start)}
                            y={r.y + 18}
                            width={Math.max(2, g.x(a.end) - g.x(a.start))}
                            height={20}
                            rx={3}
                            fill={colorOf(a.station)}
                            stroke={
                              sel?.a.task_id === a.task_id && sel.kind === "opt"
                                ? "#f4f4f8"
                                : "none"
                            }
                            strokeWidth={2}
                          />
                          <text
                            x={g.x(a.end) + 5}
                            y={r.y + 32}
                            fontSize={11}
                            fontWeight={600}
                            fill="#e3e3ec"
                          >
                            {a.station} ·{" "}
                            {taskLabel(a.task_id).split("-").slice(-1)[0]}
                          </text>
                        </g>
                      ))}
                  {show !== "opt" &&
                    base.assignments
                      .filter((a) => a.tail === r.tail)
                      .map((a) => (
                        <g
                          key={a.task_id + "b"}
                          className="cursor-pointer"
                          onClick={() => setSel({ kind: "base", a })}
                        >
                          <rect
                            x={g.x(a.start)}
                            y={r.y + 44}
                            width={Math.max(2, g.x(a.end) - g.x(a.start))}
                            height={20}
                            rx={3}
                            fill="url(#hatch)"
                            stroke="#ef4444"
                            strokeDasharray="4 2"
                          />
                          <text
                            x={g.x(a.end) + 5}
                            y={r.y + 58}
                            fontSize={11}
                            fill="#fca5a5"
                          >
                            {a.station}
                            {a.extension_h > 0
                              ? ` +${a.extension_h.toFixed(1)}h late`
                              : ""}
                          </text>
                        </g>
                      ))}
                </g>
              ))}
            </svg>
            <div className="mt-2 flex flex-wrap gap-3 text-xs text-slate-600">
              <span>
                <span className="mr-1 inline-block h-2.5 w-4 rounded-sm bg-blue-600 align-middle" />
                optimized job (color = station)
              </span>
              <span>
                <span className="mr-1 inline-block h-2.5 w-4 rounded-sm border border-red-500 bg-red-100 align-middle" />
                static baseline job
              </span>
              <span>
                <span className="mr-1 inline-block h-2.5 w-4 rounded-sm bg-sky-100 align-middle" />
                ground window
              </span>
              <span>
                <span className="mr-1 inline-block h-1.5 w-4 rounded-sm bg-slate-400 align-middle" />
                flight
              </span>
            </div>
          </div>
        )}
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Why this slot">
          {!sel ? (
            <p className="text-sm text-slate-500">
              Click a job in the timeline to see the station, technician, window
              and the reason the optimizer chose it.
            </p>
          ) : (
            <div className="space-y-1.5 text-sm">
              <div className="flex items-center gap-2">
                <b>{taskLabel(sel.a.task_id)}</b>
                <Chip tone={sel.kind === "opt" ? "blue" : "amber"}>
                  {sel.kind === "opt" ? "optimized" : "static baseline"}
                </Chip>
              </div>
              <div>
                Station <b>{sel.a.station}</b> · technician{" "}
                {sel.a.technician_id ?? "n/a"} · {fmtZ(sel.a.start)} to{" "}
                {fmtZ(sel.a.end)}
              </div>
              <div>
                Ground window {sel.a.window_hours.toFixed(1)} h · extension{" "}
                <b
                  className={
                    sel.a.extension_h > 0 ? "text-red-600" : "text-emerald-700"
                  }
                >
                  {sel.a.extension_h.toFixed(1)} h
                </b>
                {sel.a.part_source && (
                  <>
                    {" "}
                    · part shipped from <b>{sel.a.part_source}</b>
                  </>
                )}
              </div>
              <p className="rounded-lg bg-slate-50 p-2 text-slate-700">
                {sel.a.reason}
              </p>
              {sel.kind === "opt" && cur.evidence.length > 0 && (
                <div className="flex flex-wrap gap-1 pt-1">
                  {cur.evidence.map((e, i) => (
                    <Chip key={i} tone="blue" title={e.note}>
                      {e.kind}:{e.ref}
                    </Chip>
                  ))}
                </div>
              )}
            </div>
          )}
        </Card>
        <Card title="Optimized vs baseline">
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={chart}
                margin={{ top: 8, right: 8, left: -12, bottom: 0 }}
              >
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="name" fontSize={12} />
                <YAxis fontSize={12} allowDecimals={false} />
                <Tooltip />
                <Legend />
                <Bar
                  dataKey="Optimized"
                  fill="#34d399"
                  isAnimationActive={false}
                >
                  <LabelList dataKey="Optimized" position="top" fontSize={12} />
                </Bar>
                <Bar
                  dataKey="Baseline"
                  fill="#ef4444"
                  isAnimationActive={false}
                >
                  <LabelList dataKey="Baseline" position="top" fontSize={12} />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
          <p className="mt-2 text-xs text-slate-500">
            Baseline = static policy: each task at the aircraft's home base in
            the last window before its deadline. Numbers come from the plan the
            API returned (simulated data).
          </p>
        </Card>
      </div>

      {(data.exposure?.optimized?.length ?? 0) > 0 && (
        <RiskCard
          optimized={data.exposure!.optimized}
          baseline={data.exposure!.baseline ?? []}
        />
      )}
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
