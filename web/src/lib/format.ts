const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Oct 12 20:30Z" (always UTC; the simulation clock is UTC) */
export function fmtZ(iso?: string | null): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "-";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()} ${p(d.getUTCHours())}:${p(d.getUTCMinutes())}Z`;
}
export const hoursBetween = (a: string, b: string) => (new Date(b).getTime() - new Date(a).getTime()) / 3_600_000;
export const fmtH = (h: number) => `${Math.round(h * 10) / 10} h`;
export const fmtUsd = (n: number) => `$${Math.round(n).toLocaleString()}`;

export const STATUS_STYLE: Record<string, { dot: string; pill: string; label: string }> = {
  ok: { dot: "#2f7a4f", pill: "bg-emerald-50 text-emerald-800", label: "OK" },
  watch: { dot: "#a8650c", pill: "bg-amber-50 text-amber-800", label: "Watch" },
  alert: { dot: "#b3261e", pill: "bg-red-50 text-red-800", label: "Alert" },
  in_maintenance: { dot: "#2f5d8a", pill: "bg-sky-50 text-sky-900", label: "In maintenance" },
  aog: { dot: "#16191d", pill: "bg-slate-100 text-slate-900", label: "AOG" },
};
export const STATUS_ORDER: Record<string, number> = { aog: 0, alert: 1, watch: 2, in_maintenance: 3, ok: 4 };
export const AGENT_STYLE: Record<string, string> = {
  health: "bg-slate-100 text-slate-700 font-mono", scheduling: "bg-slate-100 text-slate-700 font-mono", logistics: "bg-slate-100 text-slate-700 font-mono",
  operations: "bg-slate-100 text-slate-700 font-mono", coordinator: "bg-[#16191d] text-white font-mono",
};
export const STATION_COLORS = ["#2f5d8a", "#2f7a4f", "#a8650c", "#6b4f8a", "#8a4f5e", "#3f7f86", "#6f7d3a", "#9a4b2f"];
