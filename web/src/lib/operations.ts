import type { AlertStatus, ParetoItem, ReliabilityAlert, ReliabilityKind } from "../api/reliability";
import type { SimulationRun } from "../api/runs";

export function metric(value: number | null | undefined, digits = 0): string {
  return value == null || !Number.isFinite(value) ? "—" : value.toLocaleString("en-US", { maximumFractionDigits: digits });
}

export function worstAlert(alerts: Pick<ReliabilityAlert, "status">[]): AlertStatus | null {
  const order: AlertStatus[] = ["normal", "insufficient_data", "warning", "alert"];
  return alerts.reduce<AlertStatus | null>((worst, alert) =>
    order.includes(alert.status) && (worst === null || order.indexOf(alert.status) > order.indexOf(worst)) ? alert.status : worst, null);
}

export function overallNff(kinds: Pick<ReliabilityKind, "unscheduled_removals" | "nff_removals">[]): number | null {
  if (!kinds.length || kinds.some((kind) => kind.unscheduled_removals == null || kind.nff_removals == null ||
    !Number.isFinite(kind.unscheduled_removals) || !Number.isFinite(kind.nff_removals))) return null;
  const removals = kinds.reduce((sum, kind) => sum + kind.unscheduled_removals!, 0);
  const nff = kinds.reduce((sum, kind) => sum + kind.nff_removals!, 0);
  return removals > 0 ? nff / removals : null;
}

/** Shared SVG scales: counts on the left, cumulative fraction (0–1) on the right. */
export function paretoPlot(items: ParetoItem[]): {
  max: number;
  bars: { x: number; y: number; width: number; height: number }[];
  line: string;
} {
  const max = Math.max(1, ...items.map((item) => Number.isFinite(item.unscheduled_removals) ? Math.max(0, item.unscheduled_removals ?? 0) : 0));
  const step = 520 / Math.max(1, items.length);
  let connected = false;
  const line: string[] = [];
  const bars = items.map((item, index) => {
    const x = 50 + step * (index + 0.5);
    const height = item.unscheduled_removals == null || !Number.isFinite(item.unscheduled_removals) ? 0 : Math.max(0, item.unscheduled_removals) / max * 180;
    if (item.cumulative_share != null && Number.isFinite(item.cumulative_share)) {
      line.push(`${connected ? "L" : "M"} ${x} ${210 - Math.max(0, Math.min(1, item.cumulative_share)) * 180}`);
      connected = true;
    } else connected = false;
    return { x: x - step * 0.3, y: 210 - height, width: step * 0.6, height };
  });
  return { max, bars, line: line.join(" ") };
}

export function runActive(run: Pick<SimulationRun, "status">): boolean {
  return run.status === "running" || run.status === "queued";
}

export function runResumable(run: Pick<SimulationRun, "status">): boolean {
  return run.status === "interrupted" || run.status === "failed";
}

export function runProgress(run: Pick<SimulationRun, "cycles_done" | "total_cycles">): number | null {
  if (!Number.isFinite(run.cycles_done) || !Number.isFinite(run.total_cycles) || run.total_cycles <= 0) return null;
  return Math.max(0, Math.min(1, run.cycles_done / run.total_cycles));
}

export function validRunInput(years: string, seed: string): boolean {
  return !!years.trim() && !!seed.trim() && Number.isFinite(Number(years)) && Number(years) > 0 && Number(years) <= 20 && Number.isSafeInteger(Number(seed));
}
