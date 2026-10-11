import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Area, AreaChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { usePoll } from "../api/client";
import type { Leg, TwinComponent, TwinView } from "../api/types";
import { Card, ErrorBox, Loading, StatusPill } from "../components/ui";
import EngineSection, { deviation, deviationClass, unitLabel, type GenericSpec, type SensorCatalog } from "../components/EngineSection";
import { profileFor, type Profile } from "../components/aircraftModels";
import { fmtZ } from "../lib/format";
import Ring from "../components/Ring";

const AircraftScene = lazy(() => import("../components/AircraftScene"));

type Telemetry = { cycle: number; ts: string; values: Record<string, number> };
const NAMES: Record<string, string> = {
  engine_1: "Engine 1 (left)",
  engine_2: "Engine 2 (right)",
  brakes: "Wheel brakes",
  landing_gear: "Landing gear",
  hydraulics: "Hydraulic systems",
  avionics: "Avionics",
};
const SHORT: Record<string, string> = {
  engine_1: "Engine 1",
  engine_2: "Engine 2",
  brakes: "Brakes",
  landing_gear: "Landing gear",
  hydraulics: "Hydraulics",
  avionics: "Avionics",
};
const short = (c: TwinComponent) => c.component_id.split("/").pop() ?? c.component_id;
const nameOf = (c: TwinComponent) => NAMES[short(c)] ?? short(c);
const n1 = (v: number) => Number(v.toFixed(1)).toLocaleString();
const pct = (v: number) => `${Math.round(v * 100)}`;
/** The models cap remaining life (engines at 125 flights, the rule-based systems at 500): show a capped value as "at least". */
const rulText = (v: number, kind: string) => {
  const cap = kind === "engine" ? 125 : 500;
  return v >= cap - 0.05 ? `${cap}+` : n1(v);
};

/** Remaining useful life as a band on a real scale: p10 to p90 shaded, p50 marked. */
function RulBand({ p10, p50, p90, kind }: { p10: number; p50: number; p90: number; kind: string }) {
  const max = Math.max(60, Math.ceil((p90 * 1.15) / 10) * 10);
  const x = (v: number) => `${(Math.min(v, max) / max) * 100}%`;
  return (
    <div>
      <div className="rul-band" aria-label={`RUL p10 ${p10}, p50 ${p50}, p90 ${p90} cycles`}>
        <div style={{ position: "absolute", left: 0, right: 0, top: 15, height: 4, background: "#ececea" }} />
        <div style={{ position: "absolute", left: x(p10), width: `calc(${x(p90)} - ${x(p10)})`, top: 9, height: 16, background: "#dbe5ef", borderLeft: "2px solid #a8650c" }} />
        <div style={{ position: "absolute", left: x(p50), top: 5, width: 2, height: 24, background: "#16191d" }} />
        {[0, max / 2, max].map((t) => (
          <span key={t} className="mono" style={{ position: "absolute", left: x(t), top: 26, fontSize: 10, color: "#6b717a", transform: t === max ? "translateX(-100%)" : t ? "translateX(-50%)" : undefined }}>
            {t}
          </span>
        ))}
      </div>
      <dl className="figures" style={{ marginTop: 18 }}>
        <div>
          <dt>Conservative (p10)</dt>
          <dd>
            {rulText(p10, kind)}
            <small>flights</small>
          </dd>
        </div>
        <div>
          <dt>Median (p50)</dt>
          <dd>{rulText(p50, kind)}</dd>
        </div>
        <div>
          <dt>Upper (p90)</dt>
          <dd>{rulText(p90, kind)}</dd>
        </div>
      </dl>
    </div>
  );
}

/** Wear signal of a non-engine component between its new value and its limit. */
function WearGauge({ spec, values }: { spec: GenericSpec; values: Record<string, number> }) {
  const v = values[spec.wear_signal];
  const span = spec.limit - spec.healthy;
  const used = v == null ? null : Math.min(1, Math.max(0, (v - spec.healthy) / span));
  const cls = used == null ? "" : used > 0.85 ? "s-alert" : used > 0.6 ? "s-watch" : "";
  return (
    <div>
      <div className="comp-head">
        <span>{spec.wear_signal.replaceAll("_", " ")}</span>
        <span className="mono">
          {v == null ? "-" : n1(v)} {spec.unit}
        </span>
      </div>
      <div className={`bar ${cls}`} style={{ height: 8 }}>
        <span style={{ width: `${(used ?? 0) * 100}%` }} />
      </div>
      <div className="comp-head muted mono" style={{ fontSize: 11, marginTop: 4 }}>
        <span>new {spec.healthy}</span>
        <span>limit {spec.limit}</span>
      </div>
      {spec.extras.map((e) => (
        <p key={e} className="muted" style={{ margin: "10px 0 0", fontSize: 13 }}>
          {e.replaceAll("_", " ")}: <span className="mono">{values[e] == null ? "-" : n1(values[e])}</span>
        </p>
      ))}
      <p className="muted" style={{ fontSize: 12, marginTop: 12 }}>
        Rule-based demo model with synthetic limits; not a manufacturer limit.
      </p>
    </div>
  );
}

function ComponentPanel({ component, tail, sensor, catalog }: { component: TwinComponent; tail: string; sensor: string; catalog?: SensorCatalog }) {
  const telemetry = usePoll<Telemetry[]>(
    `/aircraft/${encodeURIComponent(tail)}/telemetry?component=${encodeURIComponent(component.component_id)}&last=60`,
    3000,
  );
  const [tab, setTab] = useState<"trend" | "history">("trend");
  const isEngine = component.kind === "engine";
  const gen = catalog?.generic[component.kind];
  const keys = isEngine ? [sensor, ...["T50", "T30", "Ps30"].filter((k) => k !== sensor)].slice(0, 3) : gen ? [gen.wear_signal] : [];
  const rows = telemetry.data ?? [];
  const forecast = (component.predicted ?? []).map((p) => ({ cycle: p.cycle, band: [p.health_lo * 100, p.health_hi * 100], median: p.health_p50 * 100 }));
  const rul = component.rul_cycles;
  const st = component.status;
  return (
    <div className="space-y-4">
      <Card>
        <div className="comp-head">
          <span style={{ display: "inline-flex", alignItems: "center", gap: 12 }}>
            <Ring value={component.health} status={st} size={52} stroke={6}>
              <span className="mono" style={{ fontSize: 13, fontWeight: 600 }}>{pct(component.health)}</span>
            </Ring>
            <h2>{nameOf(component)}</h2>
          </span>
          <StatusPill status={st} />
        </div>
        <dl className="figures" style={{ marginTop: 12 }}>
          <div>
            <dt>Health</dt>
            <dd className={st === "alert" ? "tone-alert" : st === "watch" ? "tone-watch" : ""}>
              {pct(component.health)}
              <small>%</small>
            </dd>
          </div>
          <div>
            <dt>Anomaly score</dt>
            <dd>{component.anomaly_score.toFixed(2)}</dd>
          </div>
          <div>
            <dt>Data quality</dt>
            <dd style={{ fontFamily: "var(--font-sans)", fontSize: 16, paddingTop: 6 }}>{component.data_quality.status}</dd>
          </div>
        </dl>
        {component.data_quality.reasons.length > 0 && (
          <ul className="reasons">
            {component.data_quality.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
        <dl className="mt-4 grid grid-cols-3 gap-3 text-xs">
          <div>
            <dt className="muted">Flights since restored</dt>
            <dd className="mono">{component.cycles.toLocaleString()}</dd>
          </div>
          <div>
            <dt className="muted">Hours since restored</dt>
            <dd className="mono">{Math.round(component.hours).toLocaleString()}</dd>
          </div>
          <div>
            <dt className="muted">Last service</dt>
            <dd className="mono">{component.last_maintenance ? fmtZ(component.last_maintenance) : "none recorded"}</dd>
          </div>
        </dl>
      </Card>

      <Card title="Remaining useful life">
        {rul ? (
          <>
            <RulBand p10={rul.p10} p50={rul.p50} p90={rul.p90} kind={component.kind} />
            <p className="muted" style={{ fontSize: 12, margin: "10px 0 0" }}>
              Flights remaining. The scheduler treats p10 as a hard deadline; the band is model uncertainty, not an approved limit.
            </p>
          </>
        ) : (
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>
            No estimate yet. Advance the simulation so the twin has telemetry to score.
          </p>
        )}
      </Card>

      {forecast.length > 0 && (
        <Card title="Predicted health">
          <div style={{ height: 200 }}>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={forecast} margin={{ left: -12, right: 12, top: 8, bottom: 0 }}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="cycle" type="number" domain={["dataMin", "dataMax"]} tickLine={false} />
                <YAxis domain={[0, 100]} unit="%" tickLine={false} axisLine={false} />
                <Tooltip labelFormatter={(v) => `Cycle ${v}`} formatter={(v) => (Array.isArray(v) ? v.map((x) => Math.round(Number(x))).join("–") : Math.round(Number(v)))} />
                <Area type="monotone" dataKey="band" name="p10–p90" stroke="none" fill="#2f5d8a" fillOpacity={0.14} isAnimationActive={false} />
                <Area type="monotone" dataKey="median" name="p50" stroke="#2f5d8a" fill="none" strokeWidth={1.6} isAnimationActive={false} />
                <ReferenceLine x={component.cycles} stroke="#6b717a" strokeDasharray="3 3" />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <p className="muted" style={{ fontSize: 12, margin: "6px 0 0" }}>
            Line: median. Shaded: p10 to p90. Dashed: now.
          </p>
        </Card>
      )}

      <Card
        title={tab === "trend" ? "Sensor trend" : "Scored history"}
        right={
          <div className="seg">
            <button aria-pressed={tab === "trend"} onClick={() => setTab("trend")}>
              Trend
            </button>
            <button aria-pressed={tab === "history"} onClick={() => setTab("history")}>
              History
            </button>
          </div>
        }
      >
        {tab === "trend" ? (
          <>
            <ErrorBox error={telemetry.error} />
            {telemetry.loading && !rows.length ? (
              <Loading what="telemetry" />
            ) : rows.length === 0 || keys.length === 0 ? (
              <p className="muted" style={{ fontSize: 13, margin: 0 }}>
                No telemetry recorded yet.
              </p>
            ) : (
              keys.map((key, i) => {
                const m = catalog?.engine.find((s) => s.name === key);
                return (
                  <div key={key} style={{ marginTop: i ? 14 : 0 }}>
                    <div className="comp-head" style={{ fontSize: 12 }}>
                      <span>
                        <strong className="mono">{key}</strong> <span className="muted">{m?.description ?? ""}</span>
                      </span>
                      <span className="mono muted">{m ? unitLabel(m.unit) : ""}</span>
                    </div>
                    <div style={{ height: i === 0 ? 150 : 100 }}>
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={rows.map((r) => ({ cycle: r.cycle, value: r.values[key] ?? null }))} margin={{ left: -4, right: 12, top: 6, bottom: 0 }}>
                          <CartesianGrid vertical={false} />
                          <XAxis dataKey="cycle" tickLine={false} />
                          <YAxis domain={["auto", "auto"]} width={58} tickLine={false} axisLine={false} />
                          <Tooltip labelFormatter={(v) => `Cycle ${v}`} />
                          {m?.healthy_mean != null && <ReferenceLine y={m.healthy_mean} stroke="#2f7a4f" strokeDasharray="4 3" />}
                          <Line type="monotone" dataKey="value" name={key} stroke={i === 0 ? "#16191d" : "#6b717a"} strokeWidth={i === 0 ? 1.6 : 1.2} dot={false} isAnimationActive={false} />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                );
              })
            )}
            {isEngine && (
              <p className="muted" style={{ fontSize: 12, margin: "8px 0 0" }}>
                Green dashed: healthy fleet baseline. Horizontal axis: engine cycles.
              </p>
            )}
          </>
        ) : (component.history ?? []).length === 0 ? (
          <p className="muted" style={{ fontSize: 13, margin: 0 }}>
            No scored history yet.
          </p>
        ) : (
          <div style={{ maxHeight: 260, overflow: "auto" }}>
            <table className="sensor-table">
              <thead>
                <tr>
                  <th>Time (UTC)</th>
                  <th>Cycle</th>
                  <th>Health</th>
                  <th>Anomaly</th>
                  <th>RUL p50</th>
                </tr>
              </thead>
              <tbody>
                {[...(component.history ?? [])]
                  .reverse()
                  .slice(0, 80)
                  .map((h) => (
                    <tr key={`${h.cycle}-${h.ts}`}>
                      <td className="mono">{fmtZ(h.ts)}</td>
                      <td className="mono">{h.cycle}</td>
                      <td className="mono">{pct(h.health)}%</td>
                      <td className="mono">{h.anomaly_score.toFixed(2)}</td>
                      <td className="mono">{h.rul_p50 == null ? "-" : n1(h.rul_p50)}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}


/** Every monitored system of the aircraft in one table; a row selects the component everywhere else on the page. */
function SystemsTable({ components, selected, onSelect }: { components: TwinComponent[]; selected: string; onSelect: (id: string) => void }) {
  const rows = [...components].sort((a, b) => nameOf(a).localeCompare(nameOf(b)));
  return (
    <Card title="Monitored systems">
      <div style={{ overflowX: "auto" }}>
        <table className="data-table compact">
          <thead>
            <tr>
              <th>System</th>
              <th>Status</th>
              <th>Health</th>
              <th>Anomaly</th>
              <th style={{ textAlign: "right" }}>RUL p10/p50/p90</th>
              <th style={{ textAlign: "right" }} title="Flights since the system was last restored (overhaul, replacement or shop visit)">Since restored</th>
              <th style={{ textAlign: "right" }}>Hours</th>
              <th>Last service</th>
              <th>Data</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => (
              <tr key={c.component_id} className={c.component_id === selected ? "picked" : ""} onClick={() => onSelect(c.component_id)}>
                <td style={{ fontWeight: 500, whiteSpace: "nowrap" }}>{SHORT[short(c)] ?? nameOf(c)}</td>
                <td>
                  <StatusPill status={c.status} />
                </td>
                <td style={{ minWidth: 84 }}>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
                    <Ring value={c.health} status={c.status} size={30} stroke={4} />
                    <span className="mono" style={{ fontSize: 12 }}>
                      {pct(c.health)}%
                    </span>
                  </span>
                </td>
                <td className="mono">{c.anomaly_score.toFixed(2)}</td>
                <td className="mono" style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                  {c.rul_cycles ? `${rulText(c.rul_cycles.p10, c.kind)} / ${rulText(c.rul_cycles.p50, c.kind)} / ${rulText(c.rul_cycles.p90, c.kind)}` : "-"}
                </td>
                <td className="mono" style={{ textAlign: "right" }}>
                  {c.cycles.toLocaleString()}
                </td>
                <td className="mono" style={{ textAlign: "right" }}>
                  {Math.round(c.hours).toLocaleString()}
                </td>
                <td className="mono muted" style={{ whiteSpace: "nowrap" }}>{c.last_maintenance ? fmtZ(c.last_maintenance) : "none"}</td>
                <td className="muted">{c.data_quality.status === "degraded" ? "partial" : c.data_quality.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="muted" style={{ fontSize: 12, margin: "10px 0 0" }}>
        RUL is in flights; a capped value (125+ for engines, 500+ for the other systems) means at least that many. Select a row to show that system on the model and in the panels.
      </p>
    </Card>
  );
}

function Specs({ profile, type }: { profile: Profile | null; type: string }) {
  const rows: [string, string][] = profile
    ? [
        ["Type", profile.label],
        ["Engines", `2 × ${profile.engine}`],
        ["Fan diameter", `${(profile.nacelle.fanR * 2).toFixed(2)} m, ${profile.nacelle.blades} blades`],
        ["Length", `${profile.length} m`],
        ["Wingspan", `${profile.span} m`],
        ["Height", `${profile.height} m`],
        ["Fuselage", `${profile.fusW} m wide, ${profile.fusH} m high`],
        ["Wing", `${profile.wing.leSweep}° sweep, ${profile.wing.dihedral}° dihedral`],
        ["Wing tip", profile.winglet.kind === "sharklet" ? "Sharklet" : "Blended winglet"],
        ["Landing gear", `track ${profile.gear.track} m, main tyres ⌀ ${profile.gear.mainTire} m`],
        ["Hydraulics", profile.hydraulics.join(", ")],
      ]
    : [["Type", type]];
  return (
    <Card title="Specification">
      <dl className="spec-list">
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <p className="muted" style={{ fontSize: 12, margin: "10px 0 0" }}>
        Published type dimensions; the aircraft model is a reference, not manufacturer CAD.
      </p>
    </Card>
  );
}

/** What needs attention on the aircraft itself. Planning the fix lives in the Maintenance section. */
function Alerts({ data, tail }: { data: TwinView; tail: string }) {
  const open = data.components.filter((c) => c.status === "alert" || c.status === "watch");
  const notes = data.components.filter((c) => c.status === "ok" && c.data_quality.status === "bad");
  const latest = (id: string) =>
    [...data.timeline].filter((e) => e.kind === "anomaly" && e.ref === id).sort((x, y) => Date.parse(y.ts) - Date.parse(x.ts))[0];
  return (
    <Card title="Alerts">
      {open.length === 0 && notes.length === 0 ? (
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          No system is on watch or in alert. Anomaly scores and remaining life are inside their normal range.
        </p>
      ) : (
        <ul className="suggest">
          {open.map((c) => {
            const ev = latest(c.component_id);
            return (
              <li key={c.component_id}>
                <div className="comp-head">
                  <strong>{nameOf(c)}</strong>
                  <StatusPill status={c.status} />
                </div>
                <div className="muted" style={{ fontSize: 13, marginTop: 3, lineHeight: 1.5 }}>
                  Anomaly score {c.anomaly_score.toFixed(2)} · health {pct(c.health)}%
                  {c.rul_cycles ? ` · conservative life ${rulText(c.rul_cycles.p10, c.kind)} flights` : ""}
                </div>
                {ev && (
                  <div style={{ fontSize: 13, marginTop: 4, lineHeight: 1.5 }}>
                    {ev.summary} <span className="muted mono">({fmtZ(ev.ts)})</span>
                  </div>
                )}
              </li>
            );
          })}
          {notes.map((c) => (
            <li key={c.component_id}>
              <div className="comp-head">
                <strong>{nameOf(c)}</strong>
                <span className="status s-watch">Data quality</span>
              </div>
              <div className="muted" style={{ fontSize: 13, marginTop: 3 }}>{c.data_quality.reasons.join("; ") || "Telemetry cannot be scored."}</div>
            </li>
          ))}
        </ul>
      )}
      <p style={{ fontSize: 13, margin: "12px 0 0" }}>
        <Link to={`/maintenance/${tail}`}>Open maintenance for {tail} →</Link>
      </p>
    </Card>
  );
}

function NextFlights({ tail, now }: { tail: string; now?: string }) {
  const legs = usePoll<Leg[]>(`/legs?tail=${encodeURIComponent(tail)}`, 15000).data;
  const t0 = now ? Date.parse(now) : 0;
  const next = (legs ?? []).filter((l) => Date.parse(l.sched_arr) > t0).sort((a, b) => Date.parse(a.sched_dep) - Date.parse(b.sched_dep)).slice(0, 6);
  return (
    <Card title="Next flights">
      {next.length === 0 ? (
        <p className="muted" style={{ fontSize: 13, margin: 0 }}>
          No further scheduled flights in the loaded horizon.
        </p>
      ) : (
        <ul className="plain-list">
          {next.map((l) => (
            <li key={l.id}>
              <span className="mono">
                {l.dep} to {l.arr}
              </span>
              <span className="muted">
                {fmtZ(l.sched_dep)} to {fmtZ(l.sched_arr)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export default function Aircraft() {
  const { tail = "" } = useParams();
  const { data, error, reload } = usePoll<TwinView>(`/aircraft/${encodeURIComponent(tail)}`, 3000);
  const catalog = usePoll<SensorCatalog>("/ml/sensors", 600_000).data;
  const health = usePoll<{ sim_now: string }>("/health", 5000);
  const [selected, setSelected] = useState("");
  const [sensor, setSensor] = useState("T50");
  useEffect(() => setSelected(""), [tail]);
  const component = useMemo(() => {
    if (!data) return undefined;
    return (
      data.components.find((c) => c.component_id === selected) ??
      [...data.components].sort((x, y) => (x.rul_cycles?.p10 ?? Infinity) - (y.rul_cycles?.p10 ?? Infinity))[0]
    );
  }, [data, selected]);

  if (!data)
    return (
      <div>
        <Link to="/">← Fleet</Link>
        {error ? (
          <>
            <ErrorBox error={error} />
            <button onClick={reload} className="app-agents" style={{ marginTop: 12 }}>
              Retry
            </button>
          </>
        ) : (
          <Loading what={tail} />
        )}
      </div>
    );

  const a = data.aircraft;
  const profile = profileFor(a.type);
  const values = component?.sensors_last ?? {};
  const engineMeta = catalog?.engine ?? [];
  return (
    <div>
      <div className="twin-head">
        <Link to="/" style={{ fontSize: 13 }}>
          ← Fleet
        </Link>
        <h1>{a.tail}</h1>
        <span style={{ fontSize: 15 }}>{profile?.label ?? a.type}</span>
        <StatusPill status={a.status} />
        <div className="seg" role="tablist" aria-label="Section" style={{ marginLeft: "auto" }}>
          <span role="tab" aria-selected={true} className="on">
            Aircraft
          </span>
          <Link to={`/maintenance/${a.tail}`} role="tab" aria-selected={false}>
            Maintenance
          </Link>
        </div>
      </div>
      <p className="twin-meta">
        Home {a.home} · {a.location === "AIR" ? "in flight" : `on the ground at ${a.location}`} · {a.cycles.toLocaleString()} flights ·{" "}
        {Math.round(a.hours).toLocaleString()} flight hours
      </p>
      <ErrorBox error={error} />
      <div className="twin-grid">
        <div className="space-y-4">
          <Suspense fallback={<div className="study" style={{ height: 500 }} />}>
            <AircraftScene
              aircraftType={a.type}
              catalog={catalog}
              components={data.components}
              selected={component?.component_id ?? ""}
              onSelect={setSelected}
              fallback={
                <ul>
                  {data.components.map((c) => (
                    <li key={c.component_id}>
                      <button className="app-agents" onClick={() => setSelected(c.component_id)}>
                        {nameOf(c)}
                      </button>
                    </li>
                  ))}
                </ul>
              }
            />
          </Suspense>

          <SystemsTable components={data.components} selected={component?.component_id ?? ""} onSelect={setSelected} />

          {component && component.kind === "engine" && catalog && (
            <Card title={`${nameOf(component)} · ${profile?.engine ?? "turbofan"} sensor stations`}>
              <EngineSection engine={profile?.engine ?? "CFM56-7B"} values={values} catalog={catalog} selected={sensor} onSelect={setSensor} />
              <table className="sensor-table">
                <thead>
                  <tr>
                    <th>Sensor</th>
                    <th>Station</th>
                    <th style={{ textAlign: "right" }}>Latest</th>
                    <th style={{ textAlign: "right" }}>Healthy baseline</th>
                    <th style={{ textAlign: "right" }}>Deviation</th>
                  </tr>
                </thead>
                <tbody>
                  {engineMeta.map((m) => {
                    const z = deviation(m, values[m.name]);
                    return (
                      <tr key={m.name} className={sensor === m.name ? "picked" : ""} onClick={() => setSensor(m.name)}>
                        <td>
                          <strong className="mono">{m.name}</strong> <span className="muted">{m.description}</span>
                        </td>
                        <td className="mono muted">{m.station}</td>
                        <td className="mono" style={{ textAlign: "right" }}>
                          {values[m.name] == null ? "-" : n1(values[m.name])} <span className="muted">{unitLabel(m.unit)}</span>
                        </td>
                        <td className="mono muted" style={{ textAlign: "right" }}>
                          {m.healthy_mean == null ? "-" : `${n1(m.healthy_mean)} ± ${n1(m.healthy_std ?? 0)}`}
                        </td>
                        <td className={`mono status-text ${deviationClass(z)}`} style={{ textAlign: "right" }}>
                          {z == null ? "-" : `${z >= 0 ? "+" : ""}${z.toFixed(1)}σ`}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              <p className="muted" style={{ fontSize: 12, margin: "8px 0 0" }}>
                Baseline: {catalog.baseline}. Healthy engines differ from each other, so readings within about 2σ are normal.
              </p>
            </Card>
          )}
          {component && component.kind !== "engine" && catalog?.generic[component.kind] && (
            <Card title={`${nameOf(component)} · wear`}>
              <WearGauge spec={catalog.generic[component.kind]} values={values} />
            </Card>
          )}

          <Card title="Timeline">
            {data.timeline.length === 0 ? (
              <p className="muted" style={{ fontSize: 13, margin: 0 }}>
                Nothing recorded yet. Flights, injected faults, alerts and maintenance appear here.
              </p>
            ) : (
              <ol className="timeline">
                {[...data.timeline]
                  .sort((x, y) => Date.parse(y.ts) - Date.parse(x.ts))
                  .slice(0, 60)
                  .map((e, i) => (
                    <li key={`${e.ts}-${e.ref}-${i}`}>
                      <time>{fmtZ(e.ts)}</time>
                      <div>
                        <div className="kind">{e.kind.replaceAll("_", " ")}</div>
                        <div>{e.summary}</div>
                      </div>
                    </li>
                  ))}
              </ol>
            )}
          </Card>
        </div>
        <div className="space-y-4">
          <Alerts data={data} tail={tail} />
          {component ? (
            <ComponentPanel key={component.component_id} component={component} tail={tail} sensor={sensor} catalog={catalog} />
          ) : (
            <Card title="Components">
              <p className="muted">This aircraft has no registered components.</p>
            </Card>
          )}
          <Specs profile={profile} type={a.type} />
          <NextFlights tail={tail} now={health.data?.sim_now} />
        </div>
      </div>
    </div>
  );
}
