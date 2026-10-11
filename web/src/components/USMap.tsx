import { useNavigate } from "react-router-dom";
import type { AircraftSummary, Leg, Station } from "../api/types";
import { STATUS_STYLE } from "../lib/format";

// Rough outline of the contiguous US as [lon, lat]; offline, no map tiles.
const OUTLINE: [number, number][] = [
  [-124.7, 48.4],
  [-123.0, 48.3],
  [-122.8, 49.0],
  [-95.2, 49.0],
  [-94.6, 48.7],
  [-93.0, 48.6],
  [-89.6, 48.0],
  [-88.4, 48.3],
  [-84.9, 46.9],
  [-84.1, 46.5],
  [-83.5, 46.0],
  [-82.5, 45.3],
  [-82.4, 43.0],
  [-83.1, 42.3],
  [-82.6, 41.7],
  [-79.0, 42.8],
  [-79.0, 43.3],
  [-76.4, 43.6],
  [-75.0, 44.9],
  [-71.5, 45.0],
  [-70.8, 45.3],
  [-70.0, 46.7],
  [-69.2, 47.4],
  [-67.8, 47.1],
  [-67.8, 45.7],
  [-67.0, 44.8],
  [-70.0, 43.8],
  [-70.7, 42.7],
  [-70.0, 41.8],
  [-71.5, 41.4],
  [-73.7, 40.9],
  [-74.0, 40.5],
  [-74.0, 39.5],
  [-75.5, 38.5],
  [-75.9, 37.0],
  [-76.0, 36.9],
  [-75.5, 35.3],
  [-76.5, 34.7],
  [-78.5, 33.8],
  [-79.9, 32.6],
  [-81.4, 30.8],
  [-81.2, 29.8],
  [-80.0, 26.8],
  [-80.2, 25.5],
  [-81.0, 25.2],
  [-81.8, 26.1],
  [-82.7, 27.5],
  [-82.7, 29.0],
  [-84.0, 30.1],
  [-85.3, 29.7],
  [-87.5, 30.3],
  [-89.0, 30.3],
  [-89.4, 29.0],
  [-90.5, 29.1],
  [-91.8, 29.5],
  [-93.8, 29.7],
  [-94.7, 29.3],
  [-96.5, 28.3],
  [-97.4, 27.3],
  [-97.2, 25.9],
  [-99.1, 26.4],
  [-99.5, 27.5],
  [-100.7, 29.1],
  [-101.4, 29.8],
  [-102.4, 29.8],
  [-103.1, 29.0],
  [-104.5, 29.6],
  [-104.9, 30.6],
  [-106.5, 31.8],
  [-108.2, 31.8],
  [-108.2, 31.3],
  [-111.1, 31.3],
  [-114.8, 32.5],
  [-117.1, 32.5],
  [-117.3, 33.2],
  [-118.5, 34.0],
  [-120.6, 34.5],
  [-121.9, 36.6],
  [-122.5, 37.8],
  [-123.8, 39.3],
  [-124.4, 40.4],
  [-124.1, 42.0],
  [-124.5, 43.0],
  [-124.1, 46.2],
  [-124.7, 48.4],
];
const W = 1000,
  H = 560;
const px = (lon: number) => ((lon + 125) / 59) * W;
const py = (lat: number) => ((50 - lat) / 26) * H;

export default function USMap({
  stations,
  aircraft,
  legs = [],
  now,
}: {
  stations: Station[];
  aircraft: AircraftSummary[];
  legs?: Leg[];
  now?: string;
}) {
  const nav = useNavigate();
  const at = now ? Date.parse(now) : NaN;
  const byStation = new Map<string, AircraftSummary[]>();
  aircraft.forEach((a) =>
    byStation.set(a.location, [...(byStation.get(a.location) ?? []), a]),
  );
  const station = (code: string) => stations.find((s) => s.iata === code);
  const active = legs.filter(
    (l) => Date.parse(l.sched_dep) <= at && Date.parse(l.sched_arr) > at && aircraft.some(a => a.tail === l.tail && a.location === "AIR"),
  );
  const routes = [
    ...new Map(
      legs
        .filter(
          (l) =>
            Date.parse(l.sched_dep) >= at &&
            Date.parse(l.sched_dep) < at + 86400000,
        )
        .map((l) => [[l.dep, l.arr].sort().join("-"), l]),
    ).values(),
  ];
  const path = (a: Station, b: Station) =>
    `M${px(a.lon)},${py(a.lat)} Q${(px(a.lon) + px(b.lon)) / 2},${Math.min(py(a.lat), py(b.lat)) - Math.abs(px(a.lon) - px(b.lon)) * 0.15} ${px(b.lon)},${py(b.lat)}`;
  const STATUS_FILL: Record<string, string> = { ok: "#3a3a3f", watch: "#a8650c", alert: "#b3261e", in_maintenance: "#8e8e93", aog: "#16191d" };
  // A small top-view aircraft glyph, nose towards +x.
  const glyph = "M7 0 L3 -1 L1 -6 L-1 -6 L0 -1 L-5 -1 L-6.5 -3 L-7.5 -3 L-6.5 0 L-7.5 3 L-6.5 3 L-5 1 L0 1 L-1 6 L1 6 L3 1 Z";
  return (
    <div className="fleet-map">
      <svg viewBox={`0 0 ${W} ${H}`} aria-label="Simulated aircraft locations and scheduled routes">
        <polygon points={OUTLINE.map(([lo, la]) => `${px(lo)},${py(la)}`).join(" ")} fill="#f2f2ef" stroke="#c9c9c4" strokeWidth="1.2" />
        {routes.map((l) => {
          const a = station(l.dep),
            b = station(l.arr);
          return a && b ? <path key={l.id} d={path(a, b)} fill="none" stroke="#b9bdc2" strokeWidth="1" /> : null;
        })}
        {stations.map((s) => {
          const x = px(s.lon),
            y = py(s.lat),
            here = byStation.get(s.iata) ?? [],
            closed = s.hangar_slots === 0;
          return (
            <g key={s.iata}>
              <rect x={x - 4} y={y - 4} width={8} height={8} fill={closed ? "#b3261e" : "#16191d"} />
              <text x={x + 10} y={y - 6} fontSize="14" fontWeight="600" fill="#16191d" fontFamily="var(--font-mono)" stroke="#f2f2ef" strokeWidth="4" paintOrder="stroke">
                {s.iata}
              </text>
              <text x={x + 10} y={y + 9} fontSize="11" fill="#6b717a" stroke="#f2f2ef" strokeWidth="4" paintOrder="stroke">
                {here.length} on ground · {closed ? "hangar closed" : `${s.hangar_slots} bay${s.hangar_slots === 1 ? "" : "s"}`}
              </text>
              <title>{`${s.iata}: ${here.length} aircraft on the ground, ${s.hangar_slots} hangar slots`}</title>
              {here.map((a, i) => {
                const col = i % 6,
                  row = Math.floor(i / 6),
                  ax = x - 4 + col * 9,
                  ay = y + 20 + row * 9;
                return (
                  <g
                    key={a.tail}
                    role="button"
                    tabIndex={0}
                    aria-label={`Open ${a.tail}: ${a.status}`}
                    onClick={() => nav(`/aircraft/${a.tail}`)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        nav(`/aircraft/${a.tail}`);
                      }
                    }}
                    style={{ cursor: "pointer" }}
                  >
                    <circle cx={ax} cy={ay} r={a.status === "ok" ? 3 : 4} fill={STATUS_FILL[a.status] ?? "#6b717a"} />
                    <title>
                      {a.tail} · {STATUS_STYLE[a.status]?.label ?? a.status}
                    </title>
                  </g>
                );
              })}
            </g>
          );
        })}
        {active.map((l) => {
          const a = station(l.dep),
            b = station(l.arr);
          if (!a || !b) return null;
          const raw = (at - Date.parse(l.sched_dep)) / (Date.parse(l.sched_arr) - Date.parse(l.sched_dep));
          const progress = Math.min(0.9, Math.max(0.1, raw)); // keep the glyph on the route, clear of the station marker
          const x = px(a.lon) + (px(b.lon) - px(a.lon)) * progress,
            y = py(a.lat) + (py(b.lat) - py(a.lat)) * progress;
          const heading = (Math.atan2(py(b.lat) - py(a.lat), px(b.lon) - px(a.lon)) * 180) / Math.PI;
          const st = aircraft.find((c) => c.tail === l.tail)?.status ?? "ok";
          return (
            <g
              key={l.id}
              onClick={() => nav(`/aircraft/${l.tail}`)}
              role="button"
              tabIndex={0}
              aria-label={`Open ${l.tail}, scheduled in flight from ${l.dep} to ${l.arr}`}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  nav(`/aircraft/${l.tail}`);
                }
              }}
              style={{ cursor: "pointer" }}
            >
              <path d={glyph} transform={`translate(${x},${y}) rotate(${heading}) scale(1.05)`} fill={STATUS_FILL[st] ?? "#16191d"} />
              <title>
                {l.tail} · {l.dep} to {l.arr} · position interpolated from the schedule
              </title>
            </g>
          );
        })}
      </svg>
      <div className="map-legend">
        <span>
          {stations.length} stations · {active.length} in flight (interpolated from the schedule)
        </span>
        <span>Dots: aircraft on the ground, coloured by status. Arcs: next 24 h of flights.</span>
      </div>
    </div>
  );
}
