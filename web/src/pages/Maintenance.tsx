import { useState } from "react";
import { Link } from "react-router-dom";
import { usePoll } from "../api/client";
import type { PlanBundle, Station, WorkOrder } from "../api/types";
import { Card, Chip, ErrorBox, Kpi, Loading } from "../components/ui";
import WorkOrderCard from "../components/WorkOrderCard";
import { hoursText, ms, partText } from "../lib/derive";
import { fmtZ } from "../lib/format";
import { useRole } from "../lib/role";

type Tab = "pending_approval" | "approved" | "rejected" | "all";
const TABS: [Tab, string][] = [
  ["pending_approval", "Pending"],
  ["approved", "Approved"],
  ["rejected", "Rejected"],
  ["all", "All"],
];

/** Maintenance planner: work orders to decide, the jobs in the plan, and the technicians, parts and hangar slots at every station. */
export default function Maintenance() {
  const { role } = useRole();
  const orders = usePoll<WorkOrder[]>("/workorders", 2500);
  const stations = usePoll<Station[]>("/stations", 6000);
  const plan = usePoll<PlanBundle>("/plan", 5000);
  const [tab, setTab] = useState<Tab>("pending_approval");
  const [sel, setSel] = useState<string>("ALL");
  if (!orders.data || !stations.data || !plan.data)
    return orders.error || stations.error || plan.error ? <ErrorBox error={orders.error ?? stations.error ?? plan.error} /> : <Loading what="work orders and resources" />;

  const all = orders.data;
  const count = (s: Tab) => (s === "all" ? all.length : all.filter((w) => w.status === s).length);
  const shown = (tab === "all" ? all : all.filter((w) => w.status === tab)).slice().sort((a, b) => ms(a.start) - ms(b.start));
  const st = stations.data;
  const techTotal = st.reduce((n, s) => n + s.technicians.length, 0);
  const techFree = st.reduce((n, s) => n + s.technicians.filter((t) => t.available).length, 0);
  const closed = st.filter((s) => s.hangar_slots === 0).length;
  const outOfStock = st.reduce((n, s) => n + s.parts.filter((x) => x.qty === 0).length, 0);
  const jobs = plan.data.current.assignments.slice().sort((a, b) => ms(a.start) - ms(b.start));
  const one = sel === "ALL" ? null : (st.find((s) => s.iata === sel) ?? null);

  return (
    <div className="space-y-5">
      <div className="page-heading">
        <div>
          <div className="eyebrow">Work orders</div>
          <h1>Work orders, parts and people</h1>
          <p>{role.canDecide ? "You can approve or reject work orders; the audit log records who and when." : "Read-only: approval is limited to operations control and maintenance planners."}</p>
        </div>
      </div>
      <ErrorBox error={orders.error ?? stations.error ?? plan.error} />

      <div className="facts">
        <Kpi label="Pending approval" value={count("pending_approval")} tone={count("pending_approval") ? "amber" : "emerald"} hint="Work orders" />
        <Kpi label="Approved" value={count("approved")} tone="emerald" hint="Ready to schedule" />
        <Kpi label="Technicians available" value={`${techFree} of ${techTotal}`} hint="Across all stations" />
        <Kpi label="Hangars closed" value={closed} tone={closed ? "red" : "emerald"} hint="Stations with 0 slots" />
        <Kpi label="Parts out of stock" value={outOfStock} tone={outOfStock ? "amber" : "emerald"} hint="Stock lines at 0" />
      </div>

      <Card
        title="Work orders"
        right={
          <div className="flex gap-1" role="tablist">
            {TABS.map(([k, l]) => (
              <button
                key={k}
                role="tab"
                aria-selected={tab === k}
                onClick={() => setTab(k)}
                className={`rounded-md px-2.5 py-1 text-xs font-medium ${tab === k ? "bg-blue-600 text-white" : "text-slate-600 hover:bg-slate-100"}`}
              >
                {l} ({count(k)})
              </button>
            ))}
          </div>
        }
      >
        {shown.length === 0 ? (
          <p className="text-sm text-slate-500">
            {all.length === 0
              ? "No work orders yet. Run the agents from the agent workspace, or inject a fault in the simulation lab."
              : `No ${tab === "all" ? "" : tab.replace("_", " ")} work orders.`}
          </p>
        ) : (
          <div className="grid gap-3 lg:grid-cols-2">
            {shown.map((w) => (
              <WorkOrderCard key={w.id} w={w} onDone={() => orders.reload()} />
            ))}
          </div>
        )}
      </Card>

      <Card
        title="Stations: technicians, parts, hangar slots"
        right={
          <label className="flex items-center gap-2 text-xs text-slate-500">
            Station
            <select value={sel} onChange={(e) => setSel(e.target.value)} className="rounded-md border border-slate-300 bg-transparent px-2 py-1 text-xs text-slate-800">
              <option value="ALL">All stations</option>
              {st.map((s) => (
                <option key={s.iata} value={s.iata}>
                  {s.iata}
                </option>
              ))}
            </select>
          </label>
        }
      >
        {one === null ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {st.map((s) => {
              const free = s.technicians.filter((t) => t.available).length;
              const none = s.parts.filter((x) => x.qty === 0);
              const here = jobs.filter((a) => a.station === s.iata).length;
              return (
                <button key={s.iata} onClick={() => setSel(s.iata)} className="rounded-lg border border-slate-200 p-3 text-left hover:border-blue-500">
                  <div className="flex items-center justify-between">
                    <b className="text-slate-800">{s.iata}</b>
                    {s.hangar_slots === 0 ? <Chip tone="amber">hangar closed</Chip> : <span className="text-xs text-slate-500">{s.hangar_slots} hangar slots</span>}
                  </div>
                  <div className="mt-2 space-y-0.5 text-xs text-slate-500">
                    <div>
                      Technicians: {free} of {s.technicians.length} available
                    </div>
                    <div>
                      Parts: {s.parts.length} lines{none.length ? `, ${none.length} out of stock` : ""}
                    </div>
                    <div>Planned jobs: {here}</div>
                  </div>
                </button>
              );
            })}
          </div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            <div>
              <div className="mb-2 text-sm text-slate-700">
                <b>{one.iata}</b> · {one.hangar_slots === 0 ? "hangar closed" : `${one.hangar_slots} hangar slots`}
              </div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500">
                    <th className="py-1 font-medium">Technician</th>
                    <th className="py-1 font-medium">Skills</th>
                    <th className="py-1 font-medium">Shift</th>
                    <th className="py-1 font-medium">Now</th>
                  </tr>
                </thead>
                <tbody>
                  {one.technicians.map((t) => (
                    <tr key={t.id} className="border-t border-slate-200">
                      <td className="py-1.5 text-slate-800">{t.id}</td>
                      <td className="py-1.5 text-slate-600">{t.skills.join(", ")}</td>
                      <td className="py-1.5 text-xs tabular-nums text-slate-500">
                        {fmtZ(t.shift_start)} to {fmtZ(t.shift_end)}
                      </td>
                      <td className="py-1.5">{t.available ? <Chip tone="blue">available</Chip> : <Chip tone="amber">unavailable</Chip>}</td>
                    </tr>
                  ))}
                  {one.technicians.length === 0 && (
                    <tr>
                      <td className="py-2 text-slate-500" colSpan={4}>
                        No technicians based here.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <div>
              <div className="mb-2 text-sm text-slate-700">Parts</div>
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500">
                    <th className="py-1 font-medium">Part</th>
                    <th className="py-1 font-medium">Qty</th>
                    <th className="py-1 font-medium">If missing</th>
                  </tr>
                </thead>
                <tbody>
                  {one.parts.map((x) => (
                    <tr key={x.part} className="border-t border-slate-200">
                      <td className="py-1.5 text-slate-800">{x.part}</td>
                      <td className={`py-1.5 tabular-nums ${x.qty === 0 ? "text-amber-600" : "text-slate-800"}`}>
                        {x.qty}
                        {x.eta_hours > 0 ? ` (arrives in ${hoursText(x.eta_hours)})` : ""}
                      </td>
                      <td className="py-1.5 text-xs text-slate-500">{x.ship_from ? `ship from ${x.ship_from}, ${hoursText(x.ship_hours)}` : "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </Card>

      <Card title={one ? `Jobs planned at ${one.iata}` : "Jobs in the plan"} right={<Link className="text-xs text-blue-600 hover:underline" to="/planning">Open the plan</Link>}>
        {(one ? jobs.filter((a) => a.station === one.iata) : jobs).length === 0 ? (
          <p className="text-sm text-slate-500">No jobs planned{one ? " at this station" : ""}.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-slate-500">
                <th className="py-1 font-medium">Aircraft</th>
                <th className="py-1 font-medium">Station</th>
                <th className="py-1 font-medium">Window</th>
                <th className="py-1 font-medium">Technician</th>
                <th className="py-1 font-medium">Part</th>
              </tr>
            </thead>
            <tbody>
              {(one ? jobs.filter((a) => a.station === one.iata) : jobs).map((a) => (
                <tr key={a.task_id} className="border-t border-slate-200">
                  <td className="py-1.5">
                    <Link className="font-medium text-blue-600 hover:underline" to={`/aircraft/${a.tail}`}>
                      {a.tail}
                    </Link>
                  </td>
                  <td className="py-1.5 text-slate-700">{a.station}</td>
                  <td className="py-1.5 text-xs tabular-nums text-slate-600">
                    {fmtZ(a.start)} to {fmtZ(a.end)}
                  </td>
                  <td className="py-1.5 text-slate-700">{a.technician_id ?? "-"}</td>
                  <td className="py-1.5 text-xs text-slate-500">{partText(a)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
