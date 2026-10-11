import { lazy, Suspense, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { usePoll } from "../api/client";
import type { TwinComponent, TwinView } from "../api/types";
import { Card, Chip, ErrorBox, Loading, StatusPill } from "../components/ui";
import FaultFocus from "../components/FaultFocus";
import { fmtZ, STATUS_ORDER, STATUS_STYLE } from "../lib/format";

const AircraftScene = lazy(() => import("../components/AircraftScene"));

type Telemetry = { cycle: number; ts: string; values: Record<string, number> };
const labels: Record<string, string> = {
  engine_1: "Engine 1",
  engine_2: "Engine 2",
  brakes: "Brakes",
  landing_gear: "Landing gear",
  hydraulics: "Hydraulics",
  avionics: "Avionics",
};
const units: Record<string, string> = {
  T24: "°R",
  T30: "°R",
  T50: "°R",
  Nc: "rpm",
  Nf: "rpm",
  Ps30: "psia",
  P30: "psia",
  pad_wear_mm: "mm",
  strut_pressure_kpa: "kPa",
  pump_pressure_kpa: "kPa",
  error_rate_per_khr: "1/kh",
  disc_temp_c: "°C",
  fluid_temp_c: "°C",
  box_temp_c: "°C",
  shock_load_g: "g",
};
const short = (c: TwinComponent) =>
  c.component_id.split("/").pop() ?? c.component_id;
const name = (c: TwinComponent) => labels[short(c)] ?? short(c);
const number = (n: number) => Number(n.toFixed(1)).toLocaleString();
const zone: Record<string, { x: number; y: number; w: number; h: number }> = {
  avionics: { x: 178, y: 38, w: 44, h: 48 },
  engine_1: { x: 110, y: 163, w: 35, h: 70 },
  engine_2: { x: 255, y: 163, w: 35, h: 70 },
  hydraulics: { x: 181, y: 166, w: 38, h: 62 },
  landing_gear: { x: 182, y: 246, w: 36, h: 40 },
  brakes: { x: 174, y: 300, w: 52, h: 24 },
};

function Airframe({
  components,
  selected,
  onSelect,
}: {
  components: TwinComponent[];
  selected: string;
  onSelect: (id: string) => void;
}) {
  return (
    <svg
      viewBox="0 0 400 410"
      className="mx-auto h-80 w-full"
      aria-label="Aircraft component health diagram"
    >
      <path
        d="M200 15 C173 40 173 75 177 135 L40 225 L40 245 L179 200 L183 325 L125 369 L125 385 L194 364 L200 397 L206 364 L275 385 L275 369 L217 325 L221 200 L360 245 L360 225 L223 135 C227 75 227 40 200 15Z"
        fill="#191923"
        stroke="#3a3a46"
        strokeWidth="2"
      />
      {components.map((c) => {
        const z = zone[short(c)];
        if (!z) return null;
        const color = STATUS_STYLE[c.status]?.dot ?? "#94a3b8";
        return (
          <g
            key={c.component_id}
            role="button"
            tabIndex={0}
            aria-label={`${name(c)}: ${c.status}, ${number(c.health * 100)} percent health`}
            aria-pressed={selected === c.component_id}
            onClick={() => onSelect(c.component_id)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onSelect(c.component_id);
              }
            }}
            className="cursor-pointer outline-none focus:drop-shadow-lg"
          >
            <title>
              {name(c)} · {c.status} · {number(c.health * 100)}% health
            </title>
            <rect
              {...{ x: z.x, y: z.y, width: z.w, height: z.h }}
              rx="8"
              fill={color}
              fillOpacity=".75"
              stroke={selected === c.component_id ? "#f4f4f8" : color}
              strokeWidth={selected === c.component_id ? 4 : 2}
            />
            {c.status === "alert" && (
              <rect
                {...{ x: z.x - 4, y: z.y - 4, width: z.w + 8, height: z.h + 8 }}
                rx="10"
                fill="none"
                stroke="#ef4444"
                strokeWidth="2"
                strokeDasharray="4 3"
              />
            )}
          </g>
        );
      })}
    </svg>
  );
}

function ComponentDetail({
  component,
  tail,
}: {
  component: TwinComponent;
  tail: string;
}) {
  const telemetry = usePoll<Telemetry[]>(
    `/aircraft/${encodeURIComponent(tail)}/telemetry?component=${encodeURIComponent(component.component_id)}&last=60`,
    3000,
  );
  const keys = [
    ...new Set([
      "T50",
      "Nc",
      "Ps30",
      ...Object.keys(component.sensors_last ?? {}),
    ]),
  ]
    .filter((k) => k in (component.sensors_last ?? {}))
    .slice(0, 3);
  const history = component.history ?? [];
  const forecast = (component.predicted ?? []).map((p) => ({
    ...p,
    band: [p.health_lo * 100, p.health_hi * 100],
    median: p.health_p50 * 100,
  }));
  const rul = component.rul_cycles;
  const rows = telemetry.data ?? [];
  const [tab, setTab] = useState<"sensors" | "history">("sensors");
  return (
    <div className="space-y-4">
      <Card
        title={name(component)}
        right={<StatusPill status={component.status} />}
      >
        <div className="grid gap-4 sm:grid-cols-3">
          <div>
            <p className="text-xs uppercase tracking-wide text-slate-500">
              Component health
            </p>
            <div className="my-1 text-3xl font-semibold">
              {number(component.health * 100)}
              <span className="text-base text-slate-500">%</span>
            </div>
            <div
              role="meter"
              aria-label="Component health"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={component.health * 100}
              className="h-2 rounded-full bg-slate-100"
            >
              <div
                className="h-full rounded-full"
                style={{
                  width: `${component.health * 100}%`,
                  background: STATUS_STYLE[component.status]?.dot,
                }}
              />
            </div>
          </div>
          <div>
            <p className="text-xs uppercase tracking-wide text-slate-500">
              Anomaly score
            </p>
            <p className="my-1 text-3xl font-semibold">
              {component.anomaly_score.toFixed(2)}
            </p>
            <p className="text-xs text-slate-500">
              0–1 · larger means more unusual
            </p>
          </div>
          <div>
            <p className="text-xs uppercase tracking-wide text-slate-500">
              Data quality
            </p>
            <p
              className={`my-1 text-xl font-semibold ${component.data_quality.status === "ok" ? "text-emerald-700" : "text-amber-700"}`}
            >
              {component.data_quality.status}
            </p>
            <p className="text-xs text-slate-500">
              Updated {fmtZ(component.updated)}
            </p>
          </div>
        </div>
        {component.data_quality.reasons.length > 0 && (
          <ul className="mt-4 space-y-1 rounded-lg bg-amber-50 p-3 text-xs text-amber-900">
            {component.data_quality.reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
        <dl className="mt-4 grid grid-cols-3 gap-3 border-t border-slate-100 pt-3 text-xs">
          <div>
            <dt className="text-slate-500">Cycles</dt>
            <dd className="font-medium">{component.cycles.toLocaleString()}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Flight hours</dt>
            <dd className="font-medium">{number(component.hours)}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Last maintenance</dt>
            <dd className="font-medium">
              {component.last_maintenance
                ? fmtZ(component.last_maintenance)
                : "No record"}
            </dd>
          </div>
        </dl>
        <div className="mt-3 flex flex-wrap gap-1">
          {keys.map((s) => (
            <Chip key={s} tone="blue">
              {s}
            </Chip>
          ))}
        </div>
      </Card>
      <Card title="Remaining useful life" right={<Chip>cycles = flights</Chip>}>
        {rul ? (
          <>
            <div className="grid grid-cols-3 gap-3">
              {[
                ["Conservative · p10", rul.p10],
                ["Median · p50", rul.p50],
                ["Upper · p90", rul.p90],
              ].map(([label, value]) => (
                <div
                  key={label}
                  className={`rounded-lg p-3 ${String(label).includes("p10") ? "bg-amber-50" : "bg-slate-50"}`}
                >
                  <div className="text-xs text-slate-500">{label}</div>
                  <div className="mt-1 text-2xl font-semibold">
                    {number(Number(value))}
                  </div>
                </div>
              ))}
            </div>
            <p className="mt-3 text-xs text-slate-500">
              Maintenance deadlines use conservative p10. The interval expresses
              model uncertainty; it is not an approved operating limit.
            </p>
          </>
        ) : (
          <p className="text-sm text-slate-500">
            RUL is unavailable. Step the simulation to collect telemetry; check
            the data-quality reasons above.
          </p>
        )}
      </Card>
      <Card title="Predicted health · uncertainty band">
        {forecast.length > 0 ? (
          <>
            <div className="h-56">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart
                  data={forecast}
                  margin={{ left: 0, right: 15, top: 10, bottom: 12 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#272730" />
                  <XAxis
                    dataKey="cycle"
                    type="number"
                    domain={["dataMin", "dataMax"]}
                    tick={{ fontSize: 11 }}
                    label={{
                      value: "Component cycles",
                      position: "insideBottom",
                      offset: -10,
                      fontSize: 11,
                    }}
                  />
                  <YAxis domain={[0, 100]} tick={{ fontSize: 11 }} unit="%" />
                  <Tooltip labelFormatter={(v) => `Cycle ${v}`} />
                  <Area
                    type="monotone"
                    dataKey="band"
                    name="Lower–upper health (%)"
                    stroke="none"
                    fill="#4c8dff"
                    fillOpacity={0.65}
                  />
                  <Area
                    type="monotone"
                    dataKey="median"
                    name="Median health (%)"
                    stroke="#4c8dff"
                    fill="none"
                    strokeWidth={2}
                  />
                  <ReferenceLine
                    x={component.cycles}
                    stroke="#8d8d9b"
                    strokeDasharray="4 4"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
            <p className="text-xs text-slate-500">
              Solid line: median prediction. Blue band: lower to upper health
              estimate from the twin.
            </p>
          </>
        ) : (
          <p className="text-sm text-slate-500">
            No predicted curve is available for this component yet.
          </p>
        )}
      </Card>
      <Card
        title="Component evidence"
        right={
          <div className="flex gap-1">
            {(["sensors", "history"] as const).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                aria-pressed={tab === t}
                className={`rounded-md px-2 py-1 text-xs ${tab === t ? "bg-slate-900 text-white" : "bg-slate-100"}`}
              >
                {t === "sensors" ? "Sensor trends" : "Health history"}
              </button>
            ))}
          </div>
        }
      >
        {tab === "sensors" ? (
          <>
            <ErrorBox error={telemetry.error} />
            {telemetry.loading ? (
              <Loading what="sensor trends" />
            ) : rows.length === 0 || keys.length === 0 ? (
              <p className="text-sm text-slate-500">
                No sensor telemetry recorded yet.
              </p>
            ) : (
              <div className="space-y-4">
                {keys.map((key, i) => (
                  <div key={key}>
                    <div className="mb-1 flex justify-between text-xs">
                      <span className="font-medium">
                        {key} {units[key] && `(${units[key]})`}
                      </span>
                      <span className="text-slate-500">
                        latest {number(component.sensors_last?.[key] ?? 0)}
                      </span>
                    </div>
                    <div className="h-32">
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart
                          data={rows.map((r) => ({
                            cycle: r.cycle,
                            value: r.values[key] ?? null,
                          }))}
                          margin={{ left: 4, right: 15, top: 5, bottom: 0 }}
                        >
                          <CartesianGrid
                            strokeDasharray="3 3"
                            stroke="#272730"
                          />
                          <XAxis dataKey="cycle" tick={{ fontSize: 10 }} />
                          <YAxis
                            domain={["auto", "auto"]}
                            tick={{ fontSize: 10 }}
                            width={60}
                          />
                          <Tooltip labelFormatter={(v) => `Cycle ${v}`} />
                          <Line
                            type="monotone"
                            dataKey="value"
                            name={key}
                            stroke={["#4c8dff", "#4ade80", "#c4b5fd"][i]}
                            strokeWidth={2}
                            dot={false}
                            connectNulls={false}
                          />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                ))}
              </div>
            )}
            <p className="mt-2 text-xs text-slate-500">
              Measured simulator outputs · each sensor has its own scale ·
              horizontal axis is component cycles.
            </p>
          </>
        ) : history.length === 0 ? (
          <p className="text-sm text-slate-500">
            No scored history available yet.
          </p>
        ) : (
          <div className="max-h-64 overflow-auto">
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-white text-slate-500">
                <tr>
                  <th className="py-2">Time (UTC)</th>
                  <th>Cycle</th>
                  <th>Health</th>
                  <th>Anomaly</th>
                  <th>RUL p50</th>
                </tr>
              </thead>
              <tbody>
                {[...history]
                  .reverse()
                  .slice(0, 60)
                  .map((h) => (
                    <tr
                      key={`${h.cycle}-${h.ts}`}
                      className="border-t border-slate-100"
                    >
                      <td className="py-2">{fmtZ(h.ts)}</td>
                      <td>{h.cycle}</td>
                      <td>{number(h.health * 100)}%</td>
                      <td>{h.anomaly_score.toFixed(2)}</td>
                      <td>
                        {h.rul_p50 == null ? "Unknown" : number(h.rul_p50)}
                      </td>
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

export default function Aircraft() {
  const { tail = "" } = useParams();
  const { data, error, source, reload } = usePoll<TwinView>(
    `/aircraft/${encodeURIComponent(tail)}`,
    3000,
  );
  const [selected, setSelected] = useState("");
  if (!data)
    return (
      <div>
        <Link to="/" className="text-sm text-blue-600">
          ← Fleet
        </Link>
        {error ? (
          <>
            <ErrorBox error={error} />
            <button onClick={reload} className="mt-3 text-sm text-blue-600">
              Retry loading aircraft
            </button>
          </>
        ) : (
          <Loading what={tail} />
        )}
      </div>
    );
  const a = data.aircraft;
  const component =
    data.components.find((c) => c.component_id === selected) ??
    // Open on the system that needs attention: worst status first, then the shortest conservative remaining life.
    [...data.components].sort(
      (x, y) =>
        (STATUS_ORDER[x.status] ?? 9) - (STATUS_ORDER[y.status] ?? 9) ||
        (x.rul_cycles?.p10 ?? Infinity) - (y.rul_cycles?.p10 ?? Infinity),
    )[0];
  const isEngine = !!component && component.kind === "engine";
  return (
    <div className="space-y-4">
      <div className="eyebrow twin-eyebrow">Aircraft twin</div>
      <div className="flex flex-wrap items-center gap-3">
        <Link to="/" className="text-sm text-blue-600 hover:underline">
          ← Fleet
        </Link>
        <h1 className="page-title">
          {a.tail}{" "}
          <span className="font-normal text-slate-500">twin</span>
        </h1>
        <StatusPill status={a.status} />
        {source?.includes("fixture") && <Chip tone="amber">Fixture data</Chip>}
      </div>
      <p className="text-sm text-slate-500">
        {a.type} · home {a.home} ·{" "}
        {a.location === "AIR" ? "In flight" : `At ${a.location}`} ·{" "}
        {a.cycles.toLocaleString()} flights · {number(a.hours)} flight hours
      </p>
      <ErrorBox error={error} />
      <div className="grid items-start gap-4 xl:grid-cols-[1.15fr_1fr]">
        <div className="space-y-4">
          <Card title="Aircraft" right={<Chip>3D MODEL</Chip>}>
            <Suspense
              fallback={
                <div className="scene-loading">
                  Loading the 3D model…
                </div>
              }
            >
              <AircraftScene
                aircraftType={a.type}
                components={data.components}
                selected={component?.component_id ?? ""}
                onSelect={setSelected}
                fallback={
                  <Airframe
                    components={data.components}
                    selected={component?.component_id ?? ""}
                    onSelect={setSelected}
                  />
                }
              />
            </Suspense>
            <div className="grid grid-cols-2 gap-2">
              {data.components.map((c) => (
                <button
                  key={c.component_id}
                  onClick={() => setSelected(c.component_id)}
                  aria-pressed={component?.component_id === c.component_id}
                  className={`rounded-lg border p-2 text-left text-xs ${component?.component_id === c.component_id ? "border-blue-500 bg-blue-50" : "border-slate-200 hover:bg-slate-50"}`}
                >
                  <span className="mb-1 block font-semibold">{name(c)}</span>
                  <StatusPill status={c.status} />
                </button>
              ))}
            </div>
          </Card>
        </div>
        {component ? (
          <ComponentDetail
            key={component.component_id}
            component={component}
            tail={tail}
          />
        ) : (
          <Card title="Components">
            <p className="text-sm text-slate-500">
              This aircraft has no registered components.
            </p>
          </Card>
        )}
      </div>
      {/* The whole aircraft first; then, for an engine, the close-up flies in on it with every sensor against its healthy baseline. */}
      {isEngine && <FaultFocus key={component.component_id} tail={a.tail} component={component} aircraftType={a.type} />}
      <Card title="Aircraft timeline">
        <ol className="max-h-96 space-y-4 overflow-auto">
          {data.timeline.length === 0 && (
            <li className="text-sm text-slate-500">
              No events recorded. Flights, injected faults and maintenance
              events appear here.
            </li>
          )}
          {[...data.timeline]
            .sort((x, y) => Date.parse(y.ts) - Date.parse(x.ts))
            .map((e, i) => (
              <li
                key={`${e.ts}-${e.ref}-${i}`}
                className="border-l-2 border-slate-200 pl-3"
              >
                <p className="text-xs text-slate-500">{fmtZ(e.ts)}</p>
                <p className="mt-1 text-xs font-semibold capitalize">
                  {e.kind.replaceAll("_", " ")}
                </p>
                <p className="mt-1 text-sm text-slate-600">{e.summary}</p>
                {e.ref && (
                  <span className="mt-1 block text-xs text-slate-400">
                    {e.ref}
                  </span>
                )}
              </li>
            ))}
        </ol>
      </Card>
    </div>
  );
}
