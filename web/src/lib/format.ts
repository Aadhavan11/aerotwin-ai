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
  ok: { dot: "#10b981", pill: "bg-emerald-100 text-emerald-800", label: "OK" },
  watch: { dot: "#f59e0b", pill: "bg-amber-100 text-amber-800", label: "Watch" },
  alert: { dot: "#ef4444", pill: "bg-red-100 text-red-800", label: "Alert" },
  in_maintenance: { dot: "#3b82f6", pill: "bg-blue-100 text-blue-800", label: "In maintenance" },
  aog: { dot: "#7c3aed", pill: "bg-violet-100 text-violet-800", label: "AOG" },
};
export const STATUS_ORDER: Record<string, number> = { aog: 0, alert: 1, watch: 2, in_maintenance: 3, ok: 4 };
export const AGENT_STYLE: Record<string, string> = {
  health: "bg-rose-100 text-rose-800", scheduling: "bg-sky-100 text-sky-800", logistics: "bg-amber-100 text-amber-800",
  operations: "bg-violet-100 text-violet-800", coordinator: "bg-emerald-100 text-emerald-800",
};
export const STATION_COLORS = ["#2563eb", "#059669", "#d97706", "#7c3aed", "#db2777", "#0891b2", "#65a30d", "#dc2626"];
