import type { Assignment, Leg } from "../api/types";

export const ms = (iso: string): number => new Date(iso).getTime();

/** "SIM-017/engine_2" -> "Engine 2" */
export const niceComponent = (id: string): string => {
  const part = (id.split("/").pop() ?? id).replace(/_/g, " ");
  return part.charAt(0).toUpperCase() + part.slice(1);
};

export const legsOf = (legs: Leg[], tail: string): Leg[] => legs.filter((l) => l.tail === tail).sort((a, b) => ms(a.sched_dep) - ms(b.sched_dep));

/** A leg whose flying time overlaps a maintenance window of the same aircraft (the planner should keep this at zero). */
export const overlaps = (l: Leg, a: Assignment): boolean => l.tail === a.tail && ms(l.sched_dep) < ms(a.end) && ms(l.sched_arr) > ms(a.start);

/** Where does the part come from? `part_source` is the station that ships it; empty means it is already at the work station. */
export const partText = (a: Assignment): string => (!a.part_source || a.part_source === a.station ? "part on hand" : `part shipped from ${a.part_source}`);

export const hoursText = (h: number | null | undefined): string => (h === null || h === undefined ? "n/a" : `${Math.round(h * 10) / 10} h`);

export function downloadCsv(filename: string, rows: (string | number)[][]): void {
  const esc = (v: string | number) => `"${String(v).replace(/"/g, '""')}"`;
  const blob = new Blob([rows.map((r) => r.map(esc).join(",")).join("\n")], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
