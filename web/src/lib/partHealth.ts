import type { TwinComponent } from "../api/types";
import { deviation, type SensorCatalog } from "../components/EngineSection";

export type PartHealth = { value: number; status: "ok" | "watch" | "alert"; driver?: string };

// Which engine modules each sub-part is exposed to (a sensor station belongs to one module).
const ENGINE_PARTS: Record<string, { modules: string[]; floor: number }> = {
  fan: { modules: ["fan"], floor: 0 },
  inlet: { modules: ["fan", "bypass"], floor: 0.04 },
  nacelle: { modules: ["bypass", "fan"], floor: 0.05 },
  reverser: { modules: ["bypass"], floor: 0.04 },
  exhaust: { modules: ["lpt", "hpt"], floor: 0 },
  core: { modules: ["lpc", "hpc", "combustor", "hpt", "lpt"], floor: 0 },
  pylon: { modules: [], floor: 0.03 },
};
// Share of a non-engine component's wear that a sub-part carries.
const SHARE: Record<string, number> = { nose_gear: 0.5, radome: 0.3 };

const jitter = (key: string) => {
  let h = 2166136261;
  for (const ch of key) h = Math.imul(h ^ ch.charCodeAt(0), 16777619);
  return (((h >>> 0) % 1000) / 1000 - 0.5) * 0.04; // +/- 2 points, stable per part
};
const clamp = (v: number) => Math.min(0.99, Math.max(0.02, v));
const statusOf = (v: number): PartHealth["status"] => (v < 0.5 ? "alert" : v < 0.75 ? "watch" : "ok");

/**
 * Health of one sub-part. The twin scores a component as a whole; for engines the parts nearest the abnormal readings are
 * lowered (sensor deviations by module), for other systems a part carries a fixed share of the wear. Estimated, not measured.
 */
export function partHealth(partId: string, component: TwinComponent | undefined, catalog?: SensorCatalog): PartHealth | undefined {
  if (!component) return undefined;
  const loss = 1 - component.health;
  if (component.kind !== "engine") {
    const share = SHARE[partId] ?? 1;
    const v = clamp(1 - loss * share + jitter(partId + component.component_id));
    return { value: v, status: share === 1 ? (component.status === "alert" ? "alert" : component.status === "watch" ? "watch" : statusOf(v)) : statusOf(v) };
  }
  const sub = partId.split("_").pop() ?? "";
  const spec = ENGINE_PARTS[sub];
  if (!spec) return { value: clamp(component.health), status: component.status === "ok" ? "ok" : (component.status as "watch" | "alert") };
  const values = component.sensors_last ?? {};
  const stress = (modules: string[]) => {
    let best = 0;
    let who = "";
    for (const m of catalog?.engine ?? []) {
      if (!modules.includes(m.module)) continue;
      const z = deviation(m, values[m.name]);
      if (z != null && z > best) {
        best = z;
        who = m.name;
      }
    }
    return { s: Math.max(0, Math.min(1, (best - 0.5) / 5)), who, z: best };
  };
  const all = Object.values(ENGINE_PARTS).map((p) => stress(p.modules).s);
  const top = Math.max(...all, 0);
  const mine = stress(spec.modules);
  const rel = top > 0.02 ? mine.s / top : 0.35;
  const weight = Math.min(1, spec.floor + (1 - spec.floor) * rel * 0.96 + 0.04);
  const v = clamp(1 - loss * weight + jitter(partId));
  const driver = mine.who && mine.s > 0.05 ? `${mine.who} reads ${mine.z.toFixed(1)}σ from the healthy baseline` : undefined;
  return { value: v, status: statusOf(v), driver };
}
