import type { InsightsTailDelay } from "../api/insights";

export function delaysByTail(rows?: InsightsTailDelay[]): Map<string, number> {
  return new Map<string, number>((rows ?? [])
    .filter((row) => Number.isFinite(row.total_delay_min) && row.total_delay_min > 0)
    .map((row) => [row.tail, row.total_delay_min]));
}

/** Plot coordinates: left 30, right 230, top 15 (F=1), bottom 110 (F=0). */
export function weibullCurve(eta: number, beta: number, b10: number): { path: string; extent: number } | null {
  if (!Number.isFinite(eta) || !Number.isFinite(beta) || !Number.isFinite(b10) || eta <= 0 || beta <= 0) return null;
  const extent = Math.max(2 * eta, b10);
  if (!Number.isFinite(extent) || extent <= 0) return null;
  const points: string[] = [];
  for (let i = 0; i < 60; i++) {
    const fraction = i / 59;
    const t = fraction * extent;
    const f = 1 - Math.exp(-Math.pow(t / eta, beta));
    const x = 30 + fraction * 200;
    const y = 110 - f * 95;
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    points.push(`${i === 0 ? "M" : "L"} ${x.toFixed(2)} ${y.toFixed(2)}`);
  }
  return { path: points.join(" "), extent };
}
