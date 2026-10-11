import { useState } from "react";
import { Link } from "react-router-dom";
import { usePoll } from "../api/client";
import type { FleetOverview, HealthInfo, Leg, PlanBundle } from "../api/types";
import { Card, Chip, ErrorBox, Kpi, Loading, StatusPill } from "../components/ui";
import { hoursText, legsOf, ms, overlaps } from "../lib/derive";
import { fmtZ } from "../lib/format";

const HORIZONS = [12, 24, 48, 72];
const PAGE = 60;

/** Crew scheduling and dispatch: what departs, which flights touch a maintenance window, and which aircraft need attention. */
export default function Dispatch() {
  const legs = usePoll<Leg[]>("/legs", 15000);
  const fleet = usePoll<FleetOverview>("/fleet", 5000);
  const plan = usePoll<PlanBundle>("/plan", 5000);
  const health = usePoll<HealthInfo>("/health", 5000);
  const [hours, setHours] = useState(24);
  const [station, setStation] = useState("ALL");
  const [query, setQuery] = useState("");
  const [all, setAll] = useState(false);
  const [onlyFlagged, setOnlyFlagged] = useState(false);
  const now = health.data?.sim_now;
  if (!legs.data || !fleet.data || !plan.data || !now)
    return legs.error || fleet.error || plan.error ? <ErrorBox error={legs.error ?? fleet.error ?? plan.error} /> : <Loading what="departures" />;

  const status = new Map(fleet.data.aircraft.map((a) => [a.tail, a.status]));
  const jobs = plan.data.current.assignments;
  const jobOf = new Map(jobs.map((a) => [a.tail, a]));
  const t0 = ms(now),
    t1 = t0 + hours * 3_600_000;
  const stations = [...new Set(legs.data.map((l) => l.dep))].sort();
  const q = query.trim().toLowerCase();
  const window = legs.data
    .filter((l) => ms(l.sched_dep) >= t0 && ms(l.sched_dep) < t1)
    .filter((l) => station === "ALL" || l.dep === station)
    .filter((l) => !q || `${l.tail} ${l.id} ${l.dep} ${l.arr}`.toLowerCase().includes(q))
    .filter((l) => !onlyFlagged || (status.get(l.tail) ?? "ok") !== "ok" || jobOf.has(l.tail))
    .sort((a, b) => ms(a.sched_dep) - ms(b.sched_dep));
  const flyingAttention = new Set(window.filter((l) => (status.get(l.tail) ?? "ok") !== "ok").map((l) => l.tail));
  const touching = legs.data.filter((l) => jobs.some((a) => overlaps(l, a)));
  const rows = all ? window : window.slice(0, PAGE);

  return (
    <div className="space-y-5">
      <div className="page-heading">
        <div>
          <div className="eyebrow">Departures</div>
          <h1>Departures and maintenance windows</h1>
          <p>Times are UTC, from the simulation clock. Crew duty and rest data are not part of this prototype.</p>
        </div>
      </div>
      <ErrorBox error={legs.error ?? fleet.error ?? plan.error} />

      <div className="facts">
        <Kpi label={`Departures, next ${hours} h`} value={window.length} hint={station === "ALL" ? "All stations" : `From ${station}`} />
        <Kpi label="Aircraft needing attention" value={flyingAttention.size} tone={flyingAttention.size ? "amber" : "emerald"} hint="Flying in this window" />
        <Kpi label="Maintenance windows" value={jobs.length} hint="In the current plan" />
        <Kpi label="Flights touching a window" value={touching.length} tone={touching.length ? "red" : "emerald"} hint="Plan keeps this at 0" />
      </div>

      <Card
        title="Departures"
        right={
          <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
            <label className="flex items-center gap-1">
              Next
              <select value={hours} onChange={(e) => setHours(Number(e.target.value))} className="rounded-md border border-slate-300 bg-transparent px-2 py-1 text-slate-800">
                {HORIZONS.map((h) => (
                  <option key={h} value={h}>
                    {h} h
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-1">
              From
              <select value={station} onChange={(e) => setStation(e.target.value)} className="rounded-md border border-slate-300 bg-transparent px-2 py-1 text-slate-800">
                <option value="ALL">All</option>
                {stations.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex items-center gap-1">
              <input type="checkbox" checked={onlyFlagged} onChange={(e) => setOnlyFlagged(e.target.checked)} />
              Needing attention only
            </label>
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter tail, flight or airport"
              aria-label="Filter departures"
              className="w-48 rounded-md border border-slate-300 bg-transparent px-2 py-1 text-slate-800"
            />
          </div>
        }
      >
        {window.length === 0 ? (
          <p className="text-sm text-slate-500">No departures match in the next {hours} hours.</p>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-xs text-slate-500">
                    <th className="py-1 font-medium">Departs</th>
                    <th className="py-1 font-medium">Route</th>
                    <th className="py-1 font-medium">Arrives</th>
                    <th className="py-1 font-medium">Aircraft</th>
                    <th className="py-1 font-medium">Status</th>
                    <th className="py-1 font-medium">Maintenance</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((l) => {
                    const a = jobOf.get(l.tail);
                    const early = a && ms(l.sched_dep) < ms(a.start);
                    const flagged = (status.get(l.tail) ?? "ok") !== "ok" || !!a;
                    return (
                      <tr key={l.id} className={`border-t border-slate-200 ${flagged ? "bg-amber-50" : ""}`}>
                        <td className="py-1.5 tabular-nums text-slate-800">{fmtZ(l.sched_dep)}</td>
                        <td className="py-1.5 text-slate-800">
                          {l.dep} to {l.arr} <span className="text-xs text-slate-500">{l.id}</span>
                        </td>
                        <td className="py-1.5 tabular-nums text-slate-600">{fmtZ(l.sched_arr)}</td>
                        <td className="py-1.5">
                          <Link className="font-medium text-slate-900 underline hover:underline" to={`/aircraft/${l.tail}`}>
                            {l.tail}
                          </Link>
                        </td>
                        <td className="py-1.5">
                          <StatusPill status={status.get(l.tail) ?? "ok"} />
                        </td>
                        <td className="py-1.5">
                          {a ? (
                            <Chip tone={early ? "amber" : "blue"} title={`${fmtZ(a.start)} to ${fmtZ(a.end)}`}>
                              {a.station} {fmtZ(a.start)}
                              {early ? " (flies before)" : ""}
                            </Chip>
                          ) : (
                            <span className="text-xs text-slate-500">none planned</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {window.length > PAGE && (
              <button onClick={() => setAll(!all)} className="mt-3 text-xs text-slate-900 underline hover:underline">
                {all ? "Show fewer" : `Show all ${window.length} departures`}
              </button>
            )}
          </>
        )}
      </Card>

      <Card title="Maintenance windows against rotations" right={<Link className="text-xs text-slate-900 underline hover:underline" to="/planning">Open the plan</Link>}>
        {jobs.length === 0 ? (
          <p className="text-sm text-slate-500">No maintenance windows are planned, so no rotation is affected.</p>
        ) : (
          <ul className="space-y-3 text-sm">
            {jobs.map((a) => {
              const all = legsOf(legs.data!, a.tail);
              const last = [...all].reverse().find((l) => ms(l.sched_arr) <= ms(a.start));
              const first = all.find((l) => ms(l.sched_dep) >= ms(a.end));
              return (
                <li key={a.task_id} className="rounded-lg border border-slate-200 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span>
                      <Link className="font-semibold text-slate-900 underline hover:underline" to={`/aircraft/${a.tail}`}>
                        {a.tail}
                      </Link>{" "}
                      <span className="text-slate-600">at {a.station}</span>
                    </span>
                    <span className="tabular-nums text-slate-800">
                      {fmtZ(a.start)} to {fmtZ(a.end)} ({hoursText((ms(a.end) - ms(a.start)) / 3_600_000)})
                    </span>
                  </div>
                  <div className="mt-1 grid gap-1 text-xs text-slate-600 sm:grid-cols-2">
                    <div>
                      Last flight before: {last ? `${last.dep} to ${last.arr}, lands ${fmtZ(last.sched_arr)}` : "none before this window"}
                    </div>
                    <div>
                      First flight after: {first ? `${first.dep} to ${first.arr}, departs ${fmtZ(first.sched_dep)}` : "none scheduled"}
                      {first ? ` (${hoursText((ms(first.sched_dep) - ms(a.end)) / 3_600_000)} after the job ends)` : ""}
                    </div>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}
