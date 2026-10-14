import type { AircraftSummary, Assignment, Station } from "../api/types";
import { hoursText, ms } from "../lib/derive";
import { fmtZ } from "../lib/format";
import { Chip } from "./ui";

/**
 * One row per maintenance station: hangar bays, technicians on shift now, parts on hand, and what is out of stock or on its way.
 * Everything comes from the same simulation snapshot as the rest of the page; "now" is the simulation clock.
 */
export default function StationsTable({
  stations,
  aircraft,
  assignments,
  now,
}: {
  stations: Station[];
  aircraft: AircraftSummary[];
  assignments: Assignment[];
  now?: string;
}) {
  const t = now ? ms(now) : Date.now();
  const rows = [...stations]
    .sort((a, b) => a.iata.localeCompare(b.iata))
    .map((s) => {
      // Bays in use: planned jobs running right now plus aircraft already in maintenance at this station (counted once per tail).
      const busyTails = new Set<string>();
      for (const a of assignments) if (a.station === s.iata && ms(a.start) <= t && t < ms(a.end)) busyTails.add(a.tail);
      for (const a of aircraft) if (a.status === "in_maintenance" && a.location === s.iata) busyTails.add(a.tail);
      const free = Math.max(0, s.hangar_slots - busyTails.size);
      const next = assignments
        .filter((a) => a.station === s.iata && ms(a.start) > t)
        .sort((a, b) => ms(a.start) - ms(b.start))[0];
      // Only the current shift: the roster also lists shifts that start later, which must not be counted as people here now.
      const rostered = s.technicians.filter((x) => ms(x.shift_start) <= t && t < ms(x.shift_end));
      const onShift = rostered.filter((x) => x.available);
      const unavailable = rostered.length - onShift.length;
      const later = s.technicians.filter((x) => ms(x.shift_start) > t && ms(x.shift_start) <= t + 12 * 3_600_000).length;
      const skills = new Map<string, number>();
      for (const x of onShift) for (const k of x.skills) skills.set(k, (skills.get(k) ?? 0) + 1);
      const onHand = s.parts.filter((p) => p.qty > 0);
      const resupply = s.parts.filter((p) => p.qty === 0 || p.eta_hours > 0);
      return { s, busy: busyTails.size, free, next, onShift, unavailable, later, skills, onHand, resupply };
    });

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr>
            <th className="py-2 pr-4">Station</th>
            <th className="pr-4">Bays</th>
            <th className="pr-4">Technicians on shift</th>
            <th className="pr-4">Parts on hand</th>
            <th>Re-supply</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ s, busy, free, next, onShift, unavailable, later, skills, onHand, resupply }) => (
            <tr key={s.iata} className="border-t border-slate-200 align-top">
              <td className="py-3 pr-4">
                <b className="font-mono text-slate-800">{s.iata}</b>
              </td>
              <td className="py-3 pr-4 tabular-nums">
                {s.hangar_slots === 0 ? (
                  <Chip tone="amber">closed</Chip>
                ) : (
                  <>
                    <b className={free === 0 ? "text-amber-600" : "text-slate-800"}>
                      {free} free
                    </b>
                    <span className="text-slate-500"> of {s.hangar_slots}</span>
                    {busy > 0 && <div className="text-xs text-slate-500">{busy} in use</div>}
                  </>
                )}
                {next && (
                  <div className="text-xs text-slate-500">
                    next: {next.tail} {fmtZ(next.start)}
                  </div>
                )}
              </td>
              <td className="py-3 pr-4">
                <b className="tabular-nums text-slate-800">{onShift.length}</b>
                <span className="text-slate-500"> on shift now</span>
                {unavailable > 0 && <span className="text-amber-600"> · {unavailable} called out</span>}
                <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-slate-500">
                  {[...skills.entries()].map(([k, n]) => (
                    <span key={k}>
                      {k.replace("_", " ")} {n}
                    </span>
                  ))}
                  {onShift.length === 0 && <span>nobody on shift now</span>}
                </div>
                {later > 0 && <div className="mt-1 text-xs text-slate-500">{later} more start within 12 h</div>}
              </td>
              <td className="py-3 pr-4">
                <div className="flex flex-wrap gap-1">
                  {onHand.map((p) => (
                    <Chip key={p.part} title={`${p.part}: ${p.qty} on hand`}>
                      {p.part} ×{p.qty}
                    </Chip>
                  ))}
                  {onHand.length === 0 && <span className="text-xs text-slate-500">nothing on hand</span>}
                </div>
              </td>
              <td className="py-3 text-xs text-slate-600">
                {resupply.length === 0 ? (
                  <span className="text-slate-500">Everything stocked</span>
                ) : (
                  <ul className="space-y-1">
                    {resupply.map((p) => (
                      <li key={p.part}>
                        <b className="text-amber-600">{p.part}</b>{" "}
                        {p.eta_hours > 0
                          ? `arrives in ${hoursText(p.eta_hours)}`
                          : p.ship_from
                            ? `out of stock: ships from ${p.ship_from}, ${hoursText(p.ship_hours)}, $${Math.round(p.ship_cost_usd).toLocaleString()}`
                            : "out of stock, no source listed"}
                      </li>
                    ))}
                  </ul>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
