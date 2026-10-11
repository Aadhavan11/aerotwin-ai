import { Link } from "react-router-dom";
import { usePoll } from "../api/client";
import type { AuditEntry, FleetOverview, PlanBundle, WorkOrder } from "../api/types";
import { Btn, Card, Chip, ErrorBox, Kpi, Loading, StatusPill } from "../components/ui";
import WorkOrderCard from "../components/WorkOrderCard";
import { hoursText, ms, niceComponent, partText } from "../lib/derive";
import { STATUS_ORDER, fmtZ } from "../lib/format";
import { openAgentWorkspace } from "../lib/role";

/** Operations control: what needs a decision right now, which aircraft to watch, what the plan does, and what waiting would cost. */
export default function Control() {
  const fleet = usePoll<FleetOverview>("/fleet", 3000);
  const plan = usePoll<PlanBundle>("/plan", 4000);
  const orders = usePoll<WorkOrder[]>("/workorders", 2500);
  const audit = usePoll<AuditEntry[]>("/audit", 4000);
  const f = fleet.data,
    p = plan.data;
  if (!f || !p) return fleet.error || plan.error ? <ErrorBox error={fleet.error ?? plan.error} /> : <Loading what="the control room" />;

  const pending = (orders.data ?? []).filter((w) => w.status === "pending_approval");
  const watch = f.aircraft
    .filter((a) => a.status !== "ok")
    .sort((a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9) || (a.rul_p10_min ?? 1e9) - (b.rul_p10_min ?? 1e9));
  const jobs = [...p.current.assignments].sort((a, b) => ms(a.start) - ms(b.start));
  const m = p.current.metrics;
  const risk = p.exposure?.optimized ?? [];
  const riskBase = new Map((p.exposure?.baseline ?? []).map((e) => [e.task_id, e]));
  const recent = [...(audit.data ?? [])].slice(-8).reverse();

  return (
    <div className="space-y-5">
      <div className="page-heading">
        <div>
          <div className="eyebrow">Control room</div>
          <h1>{pending.length === 0 ? "Nothing is waiting for you" : `${pending.length} work ${pending.length === 1 ? "order is" : "orders are"} waiting for you`}</h1>
          <p>{f.kpis.needing_attention === 0 ? "Every aircraft is healthy" : `${f.kpis.needing_attention} ${f.kpis.needing_attention === 1 ? "aircraft needs" : "aircraft need"} attention`}, and the plan is {p.current.feasible ? "feasible" : "not feasible"}. The agents propose; a person decides.</p>
        </div>
      </div>
      <ErrorBox error={fleet.error ?? plan.error ?? orders.error} />

      <div className="facts">
        <Kpi label="Decisions waiting" value={pending.length} tone={pending.length ? "amber" : "emerald"} hint="Work orders pending approval" />
        <Kpi label="Need attention" value={f.kpis.needing_attention} tone={f.kpis.needing_attention ? "amber" : "emerald"} hint="Watch or alert aircraft" />
        <Kpi label="Due within 72 h" value={f.kpis.predicted_maintenance_72h} hint="Conservative deadlines" />
        <Kpi
          label="Plan"
          value={p.current.feasible ? "Feasible" : "Not feasible"}
          tone={p.current.feasible ? "emerald" : "red"}
          hint={`${m.tasks_scheduled} of ${m.tasks_total} tasks scheduled`}
        />
        <Kpi label="Flights affected" value={m.flights_affected} tone={m.flights_affected ? "amber" : "emerald"} hint={`${hoursText(m.downtime_h)} downtime`} />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.25fr_1fr]">
        <Card title="Decisions waiting" right={<Chip tone={pending.length ? "amber" : "slate"}>{pending.length} pending</Chip>}>
          {pending.length === 0 ? (
            <div className="space-y-3 text-sm text-slate-500">
              <p>Nothing is waiting for approval. Work orders appear here when the agents draft them for an aircraft that needs service.</p>
              <div className="flex flex-wrap gap-2">
                <Btn tone="primary" onClick={openAgentWorkspace}>
                  Open the agent workspace
                </Btn>
                <Link to="/lab">
                  <Btn>Inject a fault in the lab</Btn>
                </Link>
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              {pending.map((w) => (
                <WorkOrderCard key={w.id} w={w} onDone={() => orders.reload()} />
              ))}
            </div>
          )}
        </Card>

        <Card title="Watch list" right={<Link className="text-xs text-slate-900 underline hover:underline" to="/">All {f.aircraft.length} aircraft</Link>}>
          {watch.length === 0 ? (
            <p className="text-sm text-slate-500">Every aircraft is OK.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-500">
                  <th className="py-1 font-medium">Aircraft</th>
                  <th className="py-1 font-medium">Status</th>
                  <th className="py-1 font-medium">Worst part</th>
                  <th className="py-1 text-right font-medium">Life p10</th>
                </tr>
              </thead>
              <tbody>
                {watch.map((a) => (
                  <tr key={a.tail} className="border-t border-slate-200">
                    <td className="py-1.5">
                      <Link className="font-medium text-slate-900 underline hover:underline" to={`/aircraft/${a.tail}`}>
                        {a.tail}
                      </Link>
                      <div className="text-xs text-slate-500">
                        {a.type} · at {a.location}
                      </div>
                    </td>
                    <td className="py-1.5">
                      <StatusPill status={a.status} />
                    </td>
                    <td className="py-1.5 text-slate-700">{a.worst_component ? niceComponent(a.worst_component) : "-"}</td>
                    <td className="py-1.5 text-right tabular-nums text-slate-800">{a.rul_p10_min === null || a.rul_p10_min === undefined ? "-" : `${Math.round(a.rul_p10_min)} flights`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Maintenance in the plan" right={<Link className="text-xs text-slate-900 underline hover:underline" to="/planning">Open the plan</Link>}>
          {jobs.length === 0 ? (
            <p className="text-sm text-slate-500">No maintenance is planned. When a component's conservative remaining life falls inside the planning horizon, the solver schedules it here.</p>
          ) : (
            <ul className="space-y-3 text-sm">
              {jobs.map((a) => (
                <li key={a.task_id} className="rounded-lg border border-slate-200 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span>
                      <Link className="font-semibold text-slate-900 underline hover:underline" to={`/aircraft/${a.tail}`}>
                        {a.tail}
                      </Link>{" "}
                      <span className="text-slate-600">at {a.station}</span>
                    </span>
                    <span className="tabular-nums text-slate-800">
                      {fmtZ(a.start)} to {fmtZ(a.end)}
                    </span>
                  </div>
                  <div className="mt-1 text-xs text-slate-500">
                    {a.technician_id ?? "technician to be assigned"} · {partText(a)} · ground window {hoursText(a.window_hours)}
                    {a.extension_h > 0 ? ` · extends ${hoursText(a.extension_h)} past the planned ground time` : ""}
                  </div>
                  <div className="mt-1 text-xs text-slate-600">{a.reason}</div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <div className="space-y-4">
          <Card title="Risk of waiting (optimized vs baseline)">
            {risk.length === 0 ? (
              <p className="text-sm text-slate-500">No condition-driven task to compare yet.</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500">
                    <th className="py-1 font-medium">Task</th>
                    <th className="py-1 font-medium">Fixed after</th>
                    <th className="py-1 font-medium">Flights first</th>
                    <th className="py-1 font-medium">Margin</th>
                  </tr>
                </thead>
                <tbody>
                  {risk.map((e) => {
                    const b = riskBase.get(e.task_id);
                    return (
                      <tr key={e.task_id} className="border-t border-slate-200 align-top">
                        <td className="py-1.5">
                          {e.tail}
                          <div className="text-xs text-slate-500">{niceComponent(e.component_id)}</div>
                        </td>
                        <td className="py-1.5 tabular-nums">
                          <b className="text-slate-900">{hoursText(e.hours_to_fix)}</b>
                          <div className="text-xs text-slate-500">{hoursText(b?.hours_to_fix)} baseline</div>
                        </td>
                        <td className="py-1.5 tabular-nums">
                          <b className="text-slate-900">{e.flights_before_fix ?? "n/a"}</b>
                          <div className="text-xs text-slate-500">{b?.flights_before_fix ?? "n/a"} baseline</div>
                        </td>
                        <td className="py-1.5 tabular-nums">
                          <b className="text-slate-900">{hoursText(e.slack_to_deadline_h)}</b>
                          <div className="text-xs text-slate-500">{hoursText(b?.slack_to_deadline_h)} baseline</div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </Card>

          <Card title="Recent activity" right={<Link className="text-xs text-slate-900 underline hover:underline" to="/admin">Full audit trail</Link>}>
            {recent.length === 0 ? (
              <p className="text-sm text-slate-500">No activity recorded yet.</p>
            ) : (
              <ul className="space-y-1.5 text-xs">
                {recent.map((e, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="shrink-0 tabular-nums text-slate-500">{fmtZ(e.ts)}</span>
                    <span className="text-slate-700">
                      <b>{e.actor}</b> {e.action.replace(/[._]/g, " ")} <span className="text-slate-500">{e.ref}</span>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
