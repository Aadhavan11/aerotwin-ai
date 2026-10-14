import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { usePoll } from "../api/client";
import type { FleetOverview, HealthInfo, Leg } from "../api/types";
import type { InsightsDisruptions } from "../api/insights";
import { Chip, ErrorBox, Loading, StatusPill } from "../components/ui";
import { delaysByTail } from "../lib/insights";
import USMap from "../components/USMap";
import { profileFor } from "../components/aircraftModels";
import { fmtZ, STATUS_ORDER } from "../lib/format";

const COMPONENT: Record<string, string> = {
  engine_1: "Engine 1",
  engine_2: "Engine 2",
  brakes: "Brakes",
  landing_gear: "Landing gear",
  hydraulics: "Hydraulics",
  avionics: "Avionics",
  apu: "APU",
  tires: "Tires",
  ecs: "Air conditioning (ECS)",
  electrical: "Electrical / battery",
};

export default function Fleet() {
  const navigate = useNavigate();
  const fleet = usePoll<FleetOverview>("/fleet", 3000);
  const legs = usePoll<Leg[]>("/legs", 15000);
  const health = usePoll<HealthInfo>("/health", 4000);
  const disruptions = usePoll<InsightsDisruptions>("/insights/disruptions", 0);
  const delays = delaysByTail(disruptions.data?.by_tail);
  const [query, setQuery] = useState("");
  const data = fleet.data;
  if (!data) return fleet.error ? <ErrorBox error={fleet.error} /> : <Loading what="the fleet" />;
  const k = data.kpis;
  const all = [...data.aircraft].sort(
    (a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9) || (a.rul_p10_min ?? 999) - (b.rul_p10_min ?? 999),
  );
  const q = query.trim().toLowerCase();
  const rows = q ? all.filter((a) => [a.tail, a.location, a.type, a.status].join(" ").toLowerCase().includes(q)) : all;
  const types = data.aircraft.reduce<Record<string, number>>((m, a) => {
    const label = profileFor(a.type)?.label ?? a.type;
    m[label] = (m[label] ?? 0) + 1;
    return m;
  }, {});
  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Fleet</h1>
          <p>
            {Object.entries(types)
              .map(([t, n]) => `${n} ${t}`)
              .join(", ")}{" "}
            across {data.stations.length} stations. Simulation time {fmtZ(health.data?.sim_now)}.
          </p>
        </div>
      </div>
      <ErrorBox error={fleet.error} />
      <dl className="figures">
        <div>
          <dt>Aircraft</dt>
          <dd>{k.aircraft}</dd>
        </div>
        <div>
          <dt>Need attention</dt>
          <dd className={k.needing_attention ? "tone-watch" : ""}>
            {k.needing_attention}
            <small>watch or alert</small>
          </dd>
        </div>
        <div>
          <dt>Maintenance due within 72 h</dt>
          <dd>{k.predicted_maintenance_72h}</dd>
        </div>
        <div>
          <dt>In maintenance</dt>
          <dd>{k.in_maintenance}</dd>
        </div>
        <div>
          <dt>Available</dt>
          <dd>
            {k.readiness_pct}
            <small>%</small>
          </dd>
        </div>
      </dl>
      <div className="fleet-grid">
        <div>
          <USMap stations={data.stations} aircraft={data.aircraft} legs={legs.data} now={health.data?.sim_now} />
          <ErrorBox error={legs.error} />
        </div>
        <div className="instrument-card" style={{ padding: 0, overflow: "hidden" }}>
          <div style={{ padding: 12, borderBottom: "1px solid var(--rule)" }}>
            <input className="search" aria-label="Filter aircraft" placeholder="Filter by tail, station, type or status" value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
          <div style={{ maxHeight: 520, overflow: "auto" }}>
            <table className="risk-table">
              <thead>
                <tr>
                  <th>Tail</th>
                  <th>Type</th>
                  <th>Where</th>
                  <th>Status</th>
                  <th>Worst part</th>
                  <th style={{ textAlign: "right" }}>RUL p10</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((a) => (
                  <tr key={a.tail} onClick={() => navigate(`/aircraft/${a.tail}`)}>
                    <td className="tail">
                      <Link to={`/aircraft/${a.tail}`} onClick={(e) => e.stopPropagation()}>
                        {a.tail}
                      </Link>
                      {delays.has(a.tail) && <> <Chip tone="amber">Delay {delays.get(a.tail)} min</Chip></>}
                    </td>
                    <td className="muted">{profileFor(a.type)?.key === "b738" ? "737-800" : profileFor(a.type)?.key === "a320" ? "A320" : a.type}</td>
                    <td className="mono">{a.location === "AIR" ? "airborne" : a.location}</td>
                    <td>
                      <StatusPill status={a.status} />
                    </td>
                    <td className="muted">{a.worst_component ? COMPONENT[a.worst_component.split("/")[1]] ?? a.worst_component.split("/")[1] : "-"}</td>
                    <td className="mono" style={{ textAlign: "right" }}>
                      {a.rul_p10_min == null ? "-" : Math.round(a.rul_p10_min)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length === 0 && <p className="muted" style={{ padding: 16 }}>No aircraft match "{query}".</p>}
          </div>
          <p className="muted" style={{ fontSize: 12, margin: 0, padding: "8px 12px", borderTop: "1px solid var(--rule)" }}>
            Sorted by status, then by the lowest conservative remaining life (p10, in flights).
          </p>
        </div>
      </div>

      <div className="instrument-card" style={{ padding: 16, marginTop: 16 }}>
        <div className="comp-head" style={{ marginBottom: 8 }}>
          <h2 style={{ fontSize: 15 }}>Maintenance stations</h2>
          <span className="muted" style={{ fontSize: 12 }}>
            hangar bays, technicians on shift at {fmtZ(health.data?.sim_now)} and the parts held
          </span>
        </div>
        <div style={{ overflowX: "auto" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Station</th>
                <th style={{ textAlign: "right" }}>Bays</th>
                <th style={{ textAlign: "right" }}>On ground</th>
                <th>Technicians on shift</th>
                <th>Next shift change</th>
                <th>Parts on hand</th>
                <th>Resupply</th>
              </tr>
            </thead>
            <tbody>
              {[...data.stations]
                .sort((a, b) => a.iata.localeCompare(b.iata))
                .map((s) => {
                  const here = data.aircraft.filter((a) => a.location === s.iata).length;
                  const t0 = Date.parse(health.data?.sim_now ?? "") || Date.now();
                  const current = s.technicians.filter((t) => Date.parse(t.shift_start) <= t0 && t0 < Date.parse(t.shift_end));
                  const present = current.filter((t) => t.available !== false);
                  const absent = current.length - present.length;
                  const skills = present.reduce<Record<string, number>>((m, t) => {
                    t.skills.forEach((k) => (m[k] = (m[k] ?? 0) + 1));
                    return m;
                  }, {});
                  const change = current.length ? Math.min(...current.map((t) => Date.parse(t.shift_end))) : null;
                  return (
                    <tr key={s.iata} style={{ cursor: "default" }}>
                      <td className="mono" style={{ fontWeight: 600 }}>{s.iata}</td>
                      <td className="mono" style={{ textAlign: "right", color: s.hangar_slots === 0 ? "var(--alert)" : undefined }}>
                        {s.hangar_slots === 0 ? "closed" : s.hangar_slots}
                      </td>
                      <td className="mono" style={{ textAlign: "right" }}>{here}</td>
                      <td>
                        <span className="mono">{present.length}</span>{" "}
                        <span className="muted">
                          {Object.entries(skills)
                            .map(([k, n]) => `${k} ${n}`)
                            .join(", ")}
                          {absent > 0 ? `; ${absent} absent` : ""}
                        </span>
                      </td>
                      <td className="mono muted">{change ? fmtZ(new Date(change).toISOString()) : "-"}</td>
                      <td className="muted">
                        {s.parts.map((p) => `${p.part} ${p.qty}${p.eta_hours > 0 ? ` (+${p.eta_hours} h)` : ""}`).join(" · ")}
                      </td>
                      <td className="muted">
                        {s.parts
                          .filter((p) => p.qty === 0 && p.ship_from)
                          .map((p) => `${p.part} from ${p.ship_from}, ${p.ship_hours} h, $${Math.round(p.ship_cost_usd).toLocaleString()}`)
                          .join(" · ") || "all stocked"}
                      </td>
                    </tr>
                  );
                })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
