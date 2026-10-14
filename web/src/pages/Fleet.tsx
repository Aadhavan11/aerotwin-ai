import { useState, type CSSProperties } from "react";
import { Link, useNavigate } from "react-router-dom";
import { usePoll } from "../api/client";
import type { FleetOverview, HealthInfo, Leg, PlanBundle } from "../api/types";
import { Card, ErrorBox, Kpi, Loading, StatusPill } from "../components/ui";
import Icon from "../components/Icon";
import USMap from "../components/USMap";
import StationsTable from "../components/StationsTable";
import { Answer } from "../components/AskPanel";
import { looksLikeQuestion } from "../lib/ask";
import { STATUS_STYLE, STATUS_ORDER } from "../lib/format";
import { delaysByTail } from "../lib/insights";
import type { InsightsDisruptions } from "../api/insights";

const COMPONENT_NAMES: Record<string, string> = {
  apu: "APU",
  tires: "Tires",
  ecs: "Air conditioning (ECS)",
  electrical: "Electrical / battery",
};
const compName = (id?: string | null) => {
  const part = id?.split("/")[1];
  return part ? (COMPONENT_NAMES[part] ?? part) : undefined;
};

export default function Fleet() {
  const navigate = useNavigate();
  const fleet = usePoll<FleetOverview>("/fleet", 3000);
  const legs = usePoll<Leg[]>("/legs", 15000);
  const health = usePoll<HealthInfo>("/health", 4000);
  const plan = usePoll<PlanBundle>("/plan", 6000);
  // Optional: a missing or forbidden endpoint just means no delay chips.
  const disruptions = usePoll<InsightsDisruptions>("/insights/disruptions", 30000);
  const delays = delaysByTail(disruptions.data?.by_tail);
  const [query, setQuery] = useState("");
  const [asked, setAsked] = useState<string | null>(null);
  const data = fleet.data;
  if (!data)
    return fleet.error ? (
      <ErrorBox error={fleet.error} />
    ) : (
      <Loading what="fleet telemetry" />
    );
  const k = data.kpis;
  const all = [...data.aircraft].sort(
    (a, b) =>
      (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9) ||
      (a.rul_p10_min ?? 999) - (b.rul_p10_min ?? 999),
  );
  const rows = all.filter((a) =>
    [a.tail, a.location, a.type, a.status]
      .join(" ")
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  const counts = data.aircraft.reduce<Record<string, number>>((m, a) => {
    m[a.status] = (m[a.status] ?? 0) + 1;
    return m;
  }, {});
  const total = Math.max(1, data.aircraft.length);
  // Two different measures, labeled as such: healthy = no open watch or alert; available = not in maintenance and not AOG.
  const healthy = Math.round((100 * (counts.ok ?? 0)) / total);
  const available = data.aircraft.length - (counts.in_maintenance ?? 0) - (counts.aog ?? 0);
  // The priority card is only for an aircraft that needs something: an OK aircraft is not a "priority inspection".
  const worst = all[0];
  const focus = worst && worst.status !== "ok" ? worst : null;
  const shortest = all.filter((a) => a.rul_p10_min != null).sort((a, b) => (a.rul_p10_min ?? 0) - (b.rul_p10_min ?? 0))[0];
  const flights = (v: number | null | undefined) => (v == null ? "Unknown" : `${Math.round(v)} flights`);
  return (
    <div className="space-y-5">
      <div className="page-heading">
        <div>
          <div className="eyebrow">Fleet overview</div>
          <h1>{k.aircraft} aircraft{k.needing_attention ? `, ${k.needing_attention} ${k.needing_attention === 1 ? "needs" : "need"} attention` : ", all healthy"}</h1>
          <p>Remaining life is estimated from sensor trends. Open an aircraft to see the evidence behind its status.</p>
        </div>
      </div>
      <ErrorBox error={fleet.error} />
      <div className="facts">
        <Kpi
          label="Fleet aircraft"
          value={k.aircraft}
          hint={`${data.stations.length} maintenance stations`}
        />
        <Kpi
          label="On watch or alert"
          value={k.needing_attention}
          tone={k.needing_attention ? "amber" : "emerald"}
          hint={k.needing_attention === 1 ? "Aircraft with an open item" : "Aircraft with an open item"}
        />
        <Kpi
          label="Tasks due in 72 h"
          value={k.predicted_maintenance_72h}
          hint="Hard deadline inside 72 h"
        />
        <Kpi
          label="In maintenance"
          value={k.in_maintenance}
          tone="blue"
          hint="Active service status"
        />
        <Kpi
          label="Available to fly"
          value={`${available} of ${data.aircraft.length}`}
          tone={available === data.aircraft.length ? "emerald" : "amber"}
          hint="Not in maintenance or AOG"
        />
      </div>
      <div className="grid gap-4 xl:grid-cols-[1.7fr_1fr]">
        <Card
          title="Fleet network"
          right={
            <span className="font-mono text-[9px] text-slate-500">
              SCHEMATIC MAP / UTC
            </span>
          }
        >
          <USMap
            stations={data.stations}
            aircraft={data.aircraft}
            legs={legs.data}
            now={health.data?.sim_now}
          />
          <ErrorBox error={legs.error} />
          <div className="fleet-summary">
            <span>SELECT AN AIRCRAFT MARKER TO INSPECT</span>
            <span>SCHEMATIC · SIMULATED</span>
          </div>
        </Card>
        <div className="space-y-4">
          <Card title="Fleet health distribution">
            <div className="fleet-readiness">
              <div
                className="readiness-dial"
                style={
                  {
                    "--readiness": `${Math.max(0, Math.min(100, healthy))}%`,
                  } as CSSProperties
                }
              >
                <div>
                  {healthy}%<small>HEALTHY</small>
                </div>
              </div>
              <div className="fleet-distribution">
                {["ok", "watch", "alert", "in_maintenance", "aog"].map(
                  (status) => (
                    <div key={status}>
                      <i style={{ background: STATUS_STYLE[status].dot }} />
                      <span>{STATUS_STYLE[status].label}</span>
                      <strong>{counts[status] ?? 0}</strong>
                    </div>
                  ),
                )}
              </div>
            </div>
            <div className="distribution-bar">
              {["ok", "watch", "alert", "in_maintenance", "aog"]
                .filter((status) => (counts[status] ?? 0) > 0)
                .map((status) => (
                  <span
                    key={status}
                    title={`${STATUS_STYLE[status].label}: ${counts[status]}`}
                    style={{
                      width: `${((counts[status] ?? 0) / total) * 100}%`,
                      background: STATUS_STYLE[status]?.dot,
                    }}
                  />
                ))}
            </div>
            <p className="text-[10px] text-slate-500">
              Healthy means no open watch or alert. Available means not in
              maintenance and not AOG. Neither is an airworthiness
              determination.
            </p>
          </Card>
          <Card
            title="Priority inspection"
            right={<Icon name="pulse" size={17} />}
          >
            {focus ? (
              <>
                <div className="flex items-center justify-between">
                  <span className="font-mono text-2xl tracking-tight">
                    {focus.tail}
                  </span>
                  <StatusPill status={focus.status} />
                </div>
                <p className="mt-3 text-xs text-slate-500">
                  {focus.type} /{" "}
                  {focus.location === "AIR" ? "In flight" : focus.location} /
                  home {focus.home}
                </p>
                <div className="mt-4 flex items-end justify-between border-t border-slate-200 pt-3">
                  <div className="text-[9px] uppercase tracking-wider text-slate-500">
                    Conservative remaining life (p10)
                    <strong className="mt-1 block font-mono text-xl text-slate-800">
                      {flights(focus.rul_p10_min)}
                    </strong>
                  </div>
                  <span className="text-[10px] text-slate-500">
                    {compName(focus.worst_component) ?? "Awaiting telemetry"}
                  </span>
                </div>
                <Link to={`/aircraft/${focus.tail}`} className="fleet-link">
                  Open aircraft digital twin
                  <Icon name="arrow" size={16} />
                </Link>
              </>
            ) : (
              <div className="space-y-3 text-sm text-slate-500">
                <p>
                  <b className="text-slate-800">Nothing needs inspection.</b> Every aircraft is healthy.
                </p>
                {shortest && (
                  <p className="text-xs">
                    Shortest remaining life:{" "}
                    <Link className="text-blue-600" to={`/aircraft/${shortest.tail}`}>
                      {shortest.tail}
                    </Link>
                    , {compName(shortest.worst_component)?.replace("_", " ")}, {flights(shortest.rul_p10_min)}.
                  </p>
                )}
              </div>
            )}
          </Card>
        </div>
      </div>
      <Card
        title="Aircraft registry"
        right={
          <span className="font-mono text-[9px] text-slate-500">
            {rows.length} / {all.length} AIRCRAFT · SORTED BY RISK
          </span>
        }
      >
        <div className="fleet-search">
          <Icon name="search" size={15} />
          <input
            aria-label="Search aircraft registry or ask a question"
            placeholder="Search a tail, station or status, or ask a question…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && looksLikeQuestion(query)) {
                setAsked(query.trim());
                setQuery("");
              }
            }}
          />
        </div>
        {looksLikeQuestion(query) && (
          <button
            className="ask-inline"
            onClick={() => {
              setAsked(query.trim());
              setQuery("");
            }}
          >
            <Icon name="spark" size={14} />
            <span>Ask the AI: {query.trim()}</span>
            <kbd>Enter</kbd>
          </button>
        )}
        {asked && (
          <div className="mb-4">
            <Answer question={asked} onClear={() => setAsked(null)} />
          </div>
        )}
        <div className="max-h-[380px] overflow-auto">
          <table className="w-full whitespace-nowrap text-left text-xs">
            <thead className="sticky top-0 bg-white">
              <tr>
                <th className="py-2">AIRCRAFT ID</th>
                <th>AIRFRAME</th>
                <th>LOCATION</th>
                <th>STATUS</th>
                <th>PRIORITY COMPONENT</th>
                <th className="text-right">LIFE P10 (FLIGHTS)</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {rows.map((a) => (
                <tr
                  key={a.tail}
                  onClick={() => navigate(`/aircraft/${a.tail}`)}
                  className="risk-row cursor-pointer border-t border-slate-100"
                >
                  <td className="py-3 risk-tail">
                    <Link to={`/aircraft/${a.tail}`} className="text-blue-600">
                      {a.tail}
                    </Link>
                  </td>
                  <td className="text-slate-500">{a.type}</td>
                  <td className="font-mono text-slate-600">{a.location}</td>
                  <td>
                    <StatusPill status={a.status} />
                    {(delays.get(a.tail) ?? 0) > 0 && (
                      <span
                        className="ml-2 inline-block rounded-full border border-amber-400/40 px-2 py-0.5 font-mono text-[10px] text-amber-300"
                        title="Weather delay on scheduled legs"
                      >
                        +{Math.round(delays.get(a.tail)!)} min delay
                      </span>
                    )}
                  </td>
                  <td className="font-mono text-[10px] text-slate-500">
                    {compName(a.worst_component) ?? "—"}
                  </td>
                  <td className="text-right font-mono">
                    {a.rul_p10_min == null ? "—" : Math.round(a.rul_p10_min)}
                  </td>
                  <td className="pl-4 text-slate-400">
                    <Icon name="arrow" size={13} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && (
            <p className="p-6 text-center text-sm text-slate-500">
              No aircraft match your search.
            </p>
          )}
        </div>
      </Card>
      <Card
        title="Maintenance stations"
        right={<span className="font-mono text-[9px] text-slate-500">AS OF SIM TIME</span>}
      >
        <StationsTable stations={data.stations} aircraft={data.aircraft} assignments={plan.data?.current.assignments ?? []} now={health.data?.sim_now} />
        <p className="mt-3 text-xs text-slate-500">
          Bays free = hangar slots minus jobs running now and aircraft already in maintenance there. Technicians are counted on shift at the simulation clock;
          re-supply lists parts that are out of stock (with where they ship from, how long it takes and what it costs) or already on their way.
        </p>
      </Card>
    </div>
  );
}
