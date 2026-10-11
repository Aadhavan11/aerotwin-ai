import { lazy, Suspense } from "react";
import { usePoll } from "../api/client";
import type { TwinComponent } from "../api/types";
import { niceComponent } from "../lib/derive";
import AskPanel from "./AskPanel";
import { Card, Chip, StatusPill } from "./ui";

const AircraftScene = lazy(() => import("./AircraftScene"));

type SensorMeta = {
  name: string;
  unit: string;
  label: string;
  module: string;
  description: string;
  healthy_mean: number | null;
  healthy_std: number | null;
  worsens: number; // +1: the sensor rises towards failure, -1: it falls
};
type TelemetryRow = { ts: string; cycle: number; values: Record<string, number> };

const MODULES: Record<string, { label: string; at: number }> = {
  fan: { label: "Fan", at: 0.1 },
  bypass: { label: "Bypass duct", at: 0.22 },
  lpc: { label: "Low-pressure compressor", at: 0.3 },
  hpc: { label: "High-pressure compressor", at: 0.44 },
  combustor: { label: "Combustor", at: 0.58 },
  hpt: { label: "High-pressure turbine", at: 0.7 },
  lpt: { label: "Low-pressure turbine", at: 0.84 },
};
const ORDER = ["fan", "bypass", "lpc", "hpc", "combustor", "hpt", "lpt"];

const num = (v: number) => {
  const a = Math.abs(v);
  return v.toLocaleString(undefined, { maximumFractionDigits: a >= 1000 ? 1 : a >= 100 ? 2 : a >= 1 ? 3 : 4 });
};

/**
 * The engine in trouble, blown up, with the point of failure marked, and every sensor against its healthy baseline.
 * Baseline = average of healthy engines (first 60% of life, NASA C-MAPSS). A sensor's distance from it is counted in the standard
 * deviations of that healthy population; only movement in the direction a sensor takes towards failure counts as a warning.
 * Where the marker goes is inferred from which engine module the biggest warnings belong to: it points at where to look first,
 * it is not an inspection result.
 */
export default function FaultFocus({ tail, component, aircraftType }: { tail: string; component: TwinComponent; aircraftType: string }) {
  const meta = usePoll<{ engine: SensorMeta[]; baseline: string }>("/ml/sensors", 0);
  const latest = usePoll<TelemetryRow[]>(`/aircraft/${tail}/telemetry?component=${encodeURIComponent(component.component_id)}&last=1`, 4000);
  const row = latest.data?.[0];
  const sensors = meta.data?.engine ?? [];
  const engineKey = component.component_id.split("/").pop() ?? "engine";

  const rows = sensors
    .filter((s) => s.healthy_mean != null && s.healthy_std != null && row?.values[s.name] != null)
    .map((s) => {
      const base = s.healthy_mean as number;
      const sd = Math.max(s.healthy_std as number, 1e-9);
      const now = row!.values[s.name];
      const delta = now - base;
      const z = delta / sd;
      return { s, base, now, delta, pct: base !== 0 ? (delta / base) * 100 : 0, z, toward: z * s.worsens }; // toward > 0: moving towards failure
    })
    .sort((a, b) => b.toward - a.toward);

  const warn = rows.filter((r) => r.toward >= 3);
  const byModule = new Map<string, number>();
  for (const r of warn) byModule.set(r.s.module, (byModule.get(r.s.module) ?? 0) + Math.min(r.toward, 50));
  const suspect = [...byModule.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? null;
  const sus = suspect ? MODULES[suspect] : null;
  const top = suspect ? warn.filter((r) => r.s.module === suspect).slice(0, 3) : [];
  const tone = (r: (typeof rows)[number], i: number) => (r.toward >= 3 && i < 3 ? "bg-red-100 text-red-800" : r.toward >= 1.5 ? "bg-amber-100 text-amber-800" : "");

  return (
    <Card title={component.status === "ok" ? `Close-up: ${niceComponent(component.component_id)}` : `Where the fault is: ${niceComponent(component.component_id)}`} right={<StatusPill status={component.status} />}>
      <div className="grid gap-5 xl:grid-cols-[1fr_1.15fr]">
        <div>
          <Suspense fallback={<div className="scene-loading">Loading the close-up…</div>}>
            <AircraftScene
              aircraftType={aircraftType}
              components={[component]}
              selected={component.component_id}
              onSelect={() => undefined}
              fallback={<p className="text-sm text-slate-500">The 3D close-up is not available on this device.</p>}
              focus={{
                component: engineKey,
                fraction: sus?.at ?? 0.5,
                title: niceComponent(component.component_id),
                note: sus ? `Where the sensors point: ${sus.label}` : component.status === "ok" ? "No fault: sensors are near baseline" : "No clear location from the sensors",
                marker: !!sus,
              }}
            />
          </Suspense>
          <div className="mt-3" aria-label="Engine sections, front to back">
            <div className="flex overflow-hidden rounded-full border border-slate-200 text-center text-[11px]">
              {ORDER.map((m) => (
                <div
                  key={m}
                  title={MODULES[m].label}
                  className={`flex-1 px-1 py-1.5 ${m === suspect ? "bg-red-100 font-semibold text-red-800" : "text-slate-500"}`}
                >
                  {m === "lpc" ? "LPC" : m === "hpc" ? "HPC" : m === "hpt" ? "HPT" : m === "lpt" ? "LPT" : MODULES[m].label.split(" ")[0]}
                </div>
              ))}
            </div>
            <div className="mt-1 flex justify-between text-[11px] text-slate-500">
              <span>front</span>
              <span>back</span>
            </div>
          </div>
          <p className="mt-3 text-sm text-slate-600">
            {sus ? (
              <>
                <b className="text-red-600">{sus.label}.</b> The largest warnings come from{" "}
                {top.map((r, i) => (
                  <span key={r.s.name}>
                    {i > 0 ? ", " : ""}
                    <b>{r.s.name}</b> ({r.z > 0 ? "+" : ""}
                    {r.z.toFixed(1)} σ)
                  </span>
                ))}
                . This is where to look first; it is an inference from the sensor pattern, not an inspection result.
              </>
            ) : component.status === "ok" ? (
              "Every sensor is within normal range of its healthy baseline."
            ) : (
              "No sensor has moved far enough toward failure to point at one section."
            )}
          </p>
        </div>

        <div>
          <div className="mb-2 flex items-center justify-between gap-2 text-xs text-slate-500">
            <span>
              {row ? `Latest reading, flight ${row.cycle}` : "Waiting for a reading"}
            </span>
            <Chip>sorted by warning</Chip>
          </div>
          {meta.error || latest.error ? (
            <p className="text-sm text-red-600">Could not load the sensor data.</p>
          ) : rows.length === 0 ? (
            <p className="text-sm text-slate-500">No sensor readings yet for this engine. Advance the simulation or wait for telemetry.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr>
                    <th className="py-2 pr-3">Sensor</th>
                    <th className="pr-3 text-right">Baseline</th>
                    <th className="pr-3 text-right">Latest</th>
                    <th className="pr-3 text-right">Change</th>
                    <th className="text-right">Deviation</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r, i) => (
                    <tr key={r.s.name} className={`border-t border-slate-200 ${tone(r, i)}`} title={r.s.description}>
                      <td className="py-1.5 pr-3">
                        <b className="font-mono">{r.s.name}</b>
                        <div className="text-xs opacity-80">
                          {r.s.label} · {MODULES[r.s.module]?.label ?? r.s.module}
                        </div>
                      </td>
                      <td className="pr-3 text-right font-mono tabular-nums">{num(r.base)}</td>
                      <td className="pr-3 text-right font-mono tabular-nums">{num(r.now)}</td>
                      <td className="pr-3 text-right font-mono tabular-nums">
                        {r.delta > 0 ? "+" : ""}
                        {num(r.delta)}
                        <div className="text-xs opacity-80">
                          {r.pct > 0 ? "+" : ""}
                          {r.pct.toFixed(2)}%
                        </div>
                      </td>
                      <td className="text-right font-mono tabular-nums">
                        <b>
                          {r.z > 0 ? "+" : ""}
                          {r.z.toFixed(1)} σ
                        </b>
                        <div className="text-xs opacity-80">{r.toward >= 1.5 ? "toward failure" : r.toward <= -1.5 ? "away from failure" : "normal"}</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <p className="mt-3 text-xs text-slate-500">
            Baseline: {meta.data?.baseline ?? "healthy engines"}. Deviation is the distance from that baseline in standard deviations of the healthy
            engines; red marks the largest movements toward failure, amber the smaller ones. Units are the NASA C-MAPSS sensor units.
          </p>
        </div>
      </div>
      <AskPanel
        className="mt-5"
        placeholder={`Ask about ${niceComponent(component.component_id)} on ${tail}…`}
        suggestions={component.status === "ok" ? ["Is anything wrong with this engine?", "When is the next planned check?"] : ["Why is this engine a concern?", "Which sensors moved the most?", "What happens if we delay the maintenance?"]}
      />
    </Card>
  );
}
