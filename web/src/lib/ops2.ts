import type { PipelineMonth } from "../api/llp";
import type { RiskHistogram } from "../api/risk";

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const dimensions = (width: number, height: number) => finite(width) && finite(height) && width > 0 && height > 0;

export function histogramPlot(hist: RiskHistogram | null | undefined, width: number, height: number) {
  const edges = hist?.bin_edges;
  const counts = hist?.counts;
  if (!dimensions(width, height) || !Array.isArray(edges) || !Array.isArray(counts) || !counts.length ||
    counts.length !== edges.length - 1 || !edges.every(finite) || !counts.every(finite) ||
    edges.some((edge, index) => index > 0 && edge <= edges[index - 1])) return null;
  const max = Math.max(0, ...counts);
  const step = width / counts.length;
  return {
    max, ticks: [0, max / 2, max],
    bars: counts.map((count, index) => {
      const barHeight = Math.max(0, count) / (max || 1) * height;
      return { x: index * step, y: height - barHeight, width: step * 0.9, height: barHeight, label: `${edges[index]}–${edges[index + 1]}` };
    }),
  };
}

export function pipelinePlot(pipeline: (PipelineMonth | null)[] | null | undefined, width: number, height: number) {
  const items = (pipeline ?? []).filter((item): item is PipelineMonth => !!item && finite(item.month));
  const max = Math.max(1, ...items.flatMap((item) => [item.expected, item.cumulative_expected, item.cumulative_p50_count].filter(finite)));
  if (!dimensions(width, height)) return { bars: [], linePath: "", dashedPath: "", max, labels: [] };
  const step = width / Math.max(1, items.length);
  const path = (key: "cumulative_expected" | "cumulative_p50_count") => {
    const points: string[] = [];
    items.forEach((item, index) => {
      const value = item[key];
      if (finite(value)) points.push(`${points.length ? "L" : "M"} ${step * (index + 0.5)} ${height - Math.max(0, value) / max * height}`);
    });
    return points.join(" ");
  };
  return {
    max, labels: items.map((item) => item.month), linePath: path("cumulative_expected"), dashedPath: path("cumulative_p50_count"),
    bars: items.map((item, index) => {
      const barHeight = finite(item.expected) ? Math.max(0, item.expected) / max * height : 0;
      return { x: step * (index + 0.2), y: height - barHeight, width: step * 0.6, height: barHeight };
    }),
  };
}

export function expiryCountdown(expiresAt: string | null | undefined, simNow: string | null | undefined): { expired: boolean; label: string } {
  if (!expiresAt || !simNow) return { expired: false, label: "—" };
  const expires = Date.parse(expiresAt);
  const now = Date.parse(simNow);
  if (!finite(expires) || !finite(now)) return { expired: false, label: "—" };
  if (expires <= now) return { expired: true, label: "expired" };
  const minutes = Math.floor((expires - now) / 60000);
  const hours = Math.floor(minutes / 60);
  return { expired: false, label: hours >= 24 ? `${Math.floor(hours / 24)}d ${hours % 24}h` : hours > 0 ? `${hours}h ${minutes % 60}m` : `${minutes}m` };
}

export function apiDetail(error: unknown): string {
  if (!(error instanceof Error)) return "Not available. Please try again.";
  const status = error.message.match(/^(\d{3}) /)?.[1];
  if (status === "401" || status === "403") return "Not available for your role.";
  if (!status) return "Not available. Please try again.";
  const separator = error.message.indexOf(": ");
  const raw = separator < 0 ? error.message : error.message.slice(separator + 2);
  try {
    const body: unknown = JSON.parse(raw);
    if (body && typeof body === "object") {
      if ("detail" in body && typeof body.detail === "string") return body.detail;
      if ("error" in body && typeof body.error === "string") return body.error;
    }
  } catch { /* Truncated or non-JSON API response. */ }
  return raw.slice(0, 200);
}
