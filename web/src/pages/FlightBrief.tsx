import { Link, useSearchParams } from "react-router-dom";
import { usePoll } from "../api/client";
import type { FleetOverview, HealthInfo, Leg, PlanBundle, TwinView } from "../api/types";
import { Btn, Card, Chip, ErrorBox, Loading, StatusPill } from "../components/ui";
import { hoursText, legsOf, ms, niceComponent, partText } from "../lib/derive";
import { STATUS_ORDER, fmtZ } from "../lib/format";
import { openAgentWorkspace } from "../lib/role";

const BANNER: Record<string, { box: string; head: string }> = {
  ok: { box: "border-slate-300", head: "No open health alerts for this aircraft in the twin." },
  watch: { box: "border-amber-300", head: "A component is on watch." },
  alert: { box: "border-red-300", head: "A health alert is open." },
  in_maintenance: { box: "border-slate-800", head: "The aircraft is in maintenance." },
  aog: { box: "border-slate-500", head: "The aircraft is on the ground (AOG)." },
};

/** Pilot / flight crew: one aircraft, in plain language. Advisory only. */
export default function FlightBrief() {
  const [params, setParams] = useSearchParams();
  const fleet = usePoll<FleetOverview>("/fleet", 5000);
  const health = usePoll<HealthInfo>("/health", 5000);
  const plan = usePoll<PlanBundle>("/plan", 5000);
  const ranked = fleet.data ? [...fleet.data.aircraft].sort((a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9) || a.tail.localeCompare(b.tail)) : [];
  const tail = params.get("tail") ?? ranked[0]?.tail ?? null;
  const twin = usePoll<TwinView>(tail ? `/aircraft/${tail}` : null, 4000);
  const legsAll = usePoll<Leg[]>(tail ? `/legs?tail=${tail}` : null, 15000);
  if (!fleet.data || !tail) return fleet.error ? <ErrorBox error={fleet.error} /> : <Loading what="the fleet" />;

  const summary = fleet.data.aircraft.find((a) => a.tail === tail);
  const t = twin.data;
  const now = health.data?.sim_now;
  const next = now && legsAll.data ? legsOf(legsAll.data, tail).filter((l) => ms(l.sched_dep) >= ms(now)).slice(0, 8) : [];
  const jobs = (plan.data?.current.assignments ?? []).filter((a) => a.tail === tail).sort((a, b) => ms(a.start) - ms(b.start));
  const firstJob = jobs[0];
  const before = firstJob ? next.filter((l) => ms(l.sched_dep) < ms(firstJob.start)).length : 0;
  const comps = t ? [...t.components].sort((a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9) || (a.rul_cycles?.p10 ?? 1e9) - (b.rul_cycles?.p10 ?? 1e9)) : [];
  const worst = comps.find((c) => c.status !== "ok");
  const status = summary?.status ?? "ok";
  const banner = BANNER[status] ?? BANNER.ok;

  return (
    <div className="space-y-5">
      <div className="page-heading">
        <div>
          <div className="eyebrow">Readiness brief</div>
          <h1>Readiness brief for {tail}</h1>
          <p>What the twin knows about this aircraft, in plain language. Advisory only.</p>
        </div>
        <label className="flex items-center gap-2 text-xs text-slate-500">
          Aircraft
          <select value={tail} onChange={(e) => setParams({ tail: e.target.value })} className="rounded-md border border-slate-300 bg-transparent px-2 py-1 text-sm text-slate-800">
            {ranked.map((a) => (
              <option key={a.tail} value={a.tail}>
                {a.tail} · {a.type} · {a.status.replace("_", " ")}
              </option>
            ))}
          </select>
        </label>
      </div>
      <ErrorBox error={twin.error ?? legsAll.error} />

      <div className={`rounded-xl border-2 p-4 ${banner.box}`}>
        <div className="flex flex-wrap items-center gap-3">
          <b className="text-lg text-slate-900">{tail}</b>
          <StatusPill status={status} />
          <span className="text-sm text-slate-700">{banner.head}</span>
        </div>
        <div className="mt-2 space-y-1 text-sm text-slate-700">
          {worst && worst.rul_cycles && (
            <p>
              {niceComponent(worst.component_id)}: conservative remaining life is about <b>{Math.round(worst.rul_cycles.p10)} flights</b> (likely {Math.round(worst.rul_cycles.p50)}, at most {Math.round(worst.rul_cycles.p90)}). The planner treats the
              conservative figure as a hard deadline.
            </p>
          )}
          {firstJob ? (
            <p>
              Maintenance is planned at <b>{firstJob.station}</b>, {fmtZ(firstJob.start)} to {fmtZ(firstJob.end)} ({partText(firstJob)}).{" "}
              {before > 0 ? `${before} scheduled ${before === 1 ? "flight departs" : "flights depart"} before it starts.` : "No scheduled flight departs before it starts."}
            </p>
          ) : (
            <p>No maintenance is planned for this aircraft in the current plan.</p>
          )}
          <p className="text-xs text-slate-500">
            {summary ? `${summary.type} · home ${summary.home} · now at ${summary.location}` : ""}
            {t ? ` · ${t.aircraft.cycles.toLocaleString()} flights · ${Math.round(t.aircraft.hours).toLocaleString()} flight hours` : ""}
          </p>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          <Link to={`/aircraft/${tail}`}>
            <Btn tone="primary">Open the 3D twin</Btn>
          </Link>
          <Btn onClick={openAgentWorkspace}>Ask the copilot</Btn>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.2fr_1fr]">
        <Card title="Systems monitored by the twin">
          {!t ? (
            <Loading what="the aircraft" />
          ) : (
            <>
              {comps.length > 0 && comps.every((c) => c.data_quality.status !== "ok") && (
                <p className="mb-3 text-xs text-slate-500">
                  Data quality is {comps[0].data_quality.status} on every system: {comps[0].data_quality.reasons[0] ?? "see each system"}. Estimates stay conservative.
                </p>
              )}
            <div className="grid gap-3 sm:grid-cols-2">
              {comps.map((c) => (
                <div key={c.component_id} className="rounded-lg border border-slate-200 p-3 text-sm">
                  <div className="flex items-center justify-between gap-2">
                    <b className="text-slate-800">{niceComponent(c.component_id)}</b>
                    <StatusPill status={c.status} />
                  </div>
                  <div className="mt-2 h-1.5 overflow-hidden rounded bg-slate-200" title={`Health ${Math.round(c.health * 100)}%`}>
                    <div className={`h-full ${c.status === "ok" ? "bg-slate-800" : c.status === "watch" ? "bg-amber-500" : "bg-red-500"}`} style={{ width: `${Math.round(c.health * 100)}%` }} />
                  </div>
                  <div className="mt-2 text-xs text-slate-500">
                    Health {Math.round(c.health * 100)}% · anomaly score {c.anomaly_score.toFixed(2)}
                    {c.rul_cycles ? ` · life ${Math.round(c.rul_cycles.p10)} / ${Math.round(c.rul_cycles.p50)} / ${Math.round(c.rul_cycles.p90)} flights` : ""}
                  </div>
                  {c.data_quality.status !== "ok" && !comps.every((x) => x.data_quality.status !== "ok") && (
                    <div className="mt-1">
                      <Chip tone="amber" title={c.data_quality.reasons.join("; ")}>
                        data quality: {c.data_quality.status}
                      </Chip>
                    </div>
                  )}
                </div>
              ))}
            </div>
            </>
          )}
        </Card>

        <div className="space-y-4">
          <Card title="Next legs">
            {next.length === 0 ? (
              <p className="text-sm text-slate-500">No upcoming legs in the schedule.</p>
            ) : (
              <ul className="space-y-1.5 text-sm">
                {next.map((l) => {
                  const early = firstJob ? ms(l.sched_dep) < ms(firstJob.start) : false;
                  return (
                    <li key={l.id} className="grid grid-cols-[1fr_auto] items-center gap-x-3 gap-y-0.5">
                      <span className="text-slate-800">
                        <b>
                          {l.dep} to {l.arr}
                        </b>{" "}
                        <span className="text-xs text-slate-500">{l.id}</span>
                        {early && (
                          <span className="ml-2 align-middle">
                            <Chip tone="amber">before maintenance</Chip>
                          </span>
                        )}
                      </span>
                      <span className="text-xs tabular-nums text-slate-600">
                        {fmtZ(l.sched_dep)} to {fmtZ(l.sched_arr)}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </Card>
          <Card title="What changed recently">
            {!t || t.timeline.length === 0 ? (
              <p className="text-sm text-slate-500">No recent changes recorded.</p>
            ) : (
              <ul className="space-y-1.5 text-xs">
                {[...t.timeline].slice(-6).reverse().map((e, i) => (
                  <li key={i} className="flex gap-2">
                    <span className="shrink-0 tabular-nums text-slate-500">{fmtZ(e.ts)}</span>
                    <span className="text-slate-700">{e.summary}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>
          {firstJob && (
            <Card title="Planned maintenance">
              <ul className="space-y-1 text-xs text-slate-600">
                {jobs.map((a) => (
                  <li key={a.task_id}>
                    {a.station} · {fmtZ(a.start)} to {fmtZ(a.end)} · {a.technician_id ?? "technician to be assigned"} · ground window {hoursText(a.window_hours)}
                  </li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      </div>

      <div className="rounded-xl border border-slate-200 p-3 text-xs text-slate-500">
        This is an advisory prototype on simulated data. It is not an airworthiness determination. The aircraft technical log and the minimum equipment list remain the authority, and anything you observe goes to maintenance control.
      </div>
    </div>
  );
}
