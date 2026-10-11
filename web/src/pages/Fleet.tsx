import { useState, type CSSProperties } from "react";
import { Link, useNavigate } from "react-router-dom";
import { usePoll } from "../api/client";
import type { FleetOverview, HealthInfo, Leg } from "../api/types";
import { Card, ErrorBox, Kpi, Loading, StatusPill } from "../components/ui";
import Icon from "../components/Icon";
import USMap from "../components/USMap";
import { STATUS_STYLE, STATUS_ORDER } from "../lib/format";

export default function Fleet() {
  const navigate = useNavigate();
  const fleet = usePoll<FleetOverview>("/fleet", 3000);
  const legs = usePoll<Leg[]>("/legs", 15000);
  const health = usePoll<HealthInfo>("/health", 4000);
  const [query, setQuery] = useState("");
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
  const focus = all[0];
  return (
    <div className="space-y-5">
      <div className="page-heading">
        <div>
          <div className="eyebrow">FLEET INTELLIGENCE / LIVE OVERVIEW</div>
          <h1>Every aircraft. One operational picture.</h1>
          <p>
            Predict component health, coordinate maintenance, and adapt to
            disruption.
          </p>
        </div>
        <div className="heading-note">
          <span className="live-dot" />
          {fleet.source === "fixture" ? "FIXTURE STREAM" : "SIMULATION STREAM"}
        </div>
      </div>
      <ErrorBox error={fleet.error} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Kpi
          label="Fleet aircraft"
          value={k.aircraft}
          hint={`${data.stations.length} maintenance stations`}
        />
        <Kpi
          label="Requires attention"
          value={k.needing_attention}
          tone={k.needing_attention ? "amber" : "emerald"}
          hint="Watch & alert components"
        />
        <Kpi
          label="Maintenance / 72 h"
          value={k.predicted_maintenance_72h}
          hint="Conservative deadlines"
        />
        <Kpi
          label="In maintenance"
          value={k.in_maintenance}
          tone="blue"
          hint="Active service status"
        />
        <Kpi
          label="Operational readiness"
          value={`${k.readiness_pct}%`}
          tone="emerald"
          hint="Available aircraft / total"
        />
      </div>
      <div className="grid gap-4 xl:grid-cols-[1.7fr_1fr]">
        <Card
          title="Fleet network"
          right={
            <span className="font-mono text-[9px] text-slate-500">
              NORTH AMERICA / UTC
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
                    "--readiness": `${Math.max(0, Math.min(100, k.readiness_pct))}%`,
                  } as CSSProperties
                }
              >
                <div>
                  {k.readiness_pct}%<small>READY</small>
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
              {Object.entries(counts).map(([status, count]) => (
                <span
                  key={status}
                  style={{
                    width: `${(count / data.aircraft.length) * 100}%`,
                    background: STATUS_STYLE[status]?.dot,
                  }}
                />
              ))}
            </div>
            <p className="text-[10px] text-slate-500">
              Derived from the current aircraft state. Readiness is
              availability, not an airworthiness determination.
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
                    Lowest known RUL p10
                    <strong className="mt-1 block font-mono text-xl text-slate-800">
                      {focus.rul_p10_min == null
                        ? "Unknown"
                        : `${focus.rul_p10_min} cycles`}
                    </strong>
                  </div>
                  <span className="text-[10px] text-slate-500">
                    {focus.worst_component?.split("/")[1] ??
                      "Awaiting telemetry"}
                  </span>
                </div>
                <Link to={`/aircraft/${focus.tail}`} className="fleet-link">
                  Open aircraft digital twin
                  <Icon name="arrow" size={16} />
                </Link>
              </>
            ) : (
              <p className="text-sm text-slate-500">No aircraft registered.</p>
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
            aria-label="Search aircraft registry"
            placeholder="Search tail number, station, aircraft type or status…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div className="max-h-[380px] overflow-auto">
          <table className="w-full whitespace-nowrap text-left text-xs">
            <thead className="sticky top-0 bg-white">
              <tr>
                <th className="py-2">AIRCRAFT ID</th>
                <th>AIRFRAME</th>
                <th>LOCATION</th>
                <th>STATUS</th>
                <th>PRIORITY COMPONENT</th>
                <th className="text-right">RUL P10 / CYCLES</th>
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
                  </td>
                  <td className="font-mono text-[10px] text-slate-500">
                    {a.worst_component?.split("/")[1] ?? "—"}
                  </td>
                  <td className="text-right font-mono">
                    {a.rul_p10_min ?? "—"}
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
    </div>
  );
}
