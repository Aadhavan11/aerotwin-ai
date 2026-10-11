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
  return (
    <div className="fleet-network">
      <div className="network-overlay">
        <strong>CONTINENTAL OPERATIONS NETWORK</strong>SCHEMATIC PROJECTION /
        SCHEDULED ROTATIONS
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        aria-label="Simulated aircraft locations and scheduled routes"
      >
        <defs>
          <pattern
            id="map-grid"
            width="40"
            height="40"
            patternUnits="userSpaceOnUse"
          >
            <path
              d="M40 0H0V40"
              fill="none"
              stroke="#173142"
              strokeWidth=".7"
            />
          </pattern>
          <radialGradient id="map-glow">
            <stop offset="0" stopColor="#1b5866" stopOpacity=".22" />
            <stop offset="1" stopColor="#0a1825" stopOpacity="0" />
          </radialGradient>
        </defs>
        <rect width={W} height={H} fill="url(#map-grid)" />
        <ellipse cx="550" cy="320" rx="430" ry="260" fill="url(#map-glow)" />
        <polygon
          points={OUTLINE.map(([lo, la]) => `${px(lo)},${py(la)}`).join(" ")}
          fill="#102d3d"
          stroke="#2e657c"
          strokeWidth="1.5"
        />
        {[-120, -110, -100, -90, -80, -70].map((lon) => (
          <g key={lon}>
            <line
              x1={px(lon)}
              x2={px(lon)}
              y1="40"
              y2={H - 25}
              stroke="#193b4e"
              strokeDasharray="2 6"
            />
            <text
              x={px(lon) + 4}
              y={H - 35}
              fill="#345b71"
              fontSize="8"
              fontFamily="monospace"
            >
              {Math.abs(lon)}°W
            </text>
          </g>
        ))}
        {routes.map((l) => {
          const a = station(l.dep),
            b = station(l.arr);
          return a && b ? (
            <path
              key={l.id}
              d={path(a, b)}
              fill="none"
              stroke="#40829a"
              strokeWidth="1.2"
              opacity=".6"
              className="network-flow"
            />
          ) : null;
        })}
        {stations.map((s) => {
          const x = px(s.lon),
            y = py(s.lat),
            here = byStation.get(s.iata) ?? [],
            closed = s.hangar_slots === 0;
          return (
            <g key={s.iata}>
              <circle
                cx={x}
                cy={y}
                r="34"
                fill="none"
                stroke="#235169"
                strokeWidth=".7"
              />
              <circle
                cx={x}
                cy={y}
                r="12"
                fill="#0d293a"
                stroke={closed ? "#fb7185" : "#64d8df"}
                strokeWidth="1.5"
              />
              <circle
                cx={x}
                cy={y}
                r="3"
                fill={closed ? "#fb7185" : "#8fe7e3"}
              />
              <text
                x={x + 38}
                y={y + 1}
                fontSize="13"
                fontWeight="600"
                fill="#b2deea"
                fontFamily="monospace"
              >
                {s.iata}
              </text>
              <text
                x={x + 38}
                y={y + 15}
                fontSize="8"
                fill="#638fa6"
                fontFamily="monospace"
              >
                {here.length} AC / {s.hangar_slots} BAY
              </text>
              <title>{`${s.iata}: ${here.length} aircraft, ${s.hangar_slots} hangar slots`}</title>
              {here.map((a, i) => {
                const angle =
                    (i / Math.max(here.length, 1)) * Math.PI * 2 - Math.PI / 2,
                  r = 24 + Math.floor(i / 8) * 9,
                  ax = x + Math.cos(angle) * r,
                  ay = y + Math.sin(angle) * r,
                  color = STATUS_STYLE[a.status]?.dot ?? "#8ca7b8";
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
                    className="cursor-pointer"
                  >
                    <circle
                      cx={ax}
                      cy={ay}
                      r="5"
                      fill={color}
                      stroke="#0a1824"
                      strokeWidth="1.5"
                    />
                    {a.status === "alert" && (
                      <circle
                        cx={ax}
                        cy={ay}
                        r="5"
                        fill="none"
                        stroke={color}
                        className="ping-soft"
                      />
                    )}
                    <title>
                      {a.tail} · {a.status}
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
          const progress =
            (at - Date.parse(l.sched_dep)) /
            (Date.parse(l.sched_arr) - Date.parse(l.sched_dep));
          const x = px(a.lon) + (px(b.lon) - px(a.lon)) * progress,
            y = py(a.lat) + (py(b.lat) - py(a.lat)) * progress;
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
              className="cursor-pointer"
            >
              <circle cx={x} cy={y} r="9" fill="#173c50" stroke="#8cdbe3" />
              <path
                d="m-5 0 10 0m-5-4v8"
                transform={`translate(${x},${y})`}
                stroke="#b7eff2"
                strokeWidth="1.5"
              />
              <title>
                {l.tail} · {l.dep} → {l.arr} · interpolated scheduled position
              </title>
            </g>
          );
        })}
      </svg>
      <div className="network-footer">
        <span>
          {stations.length} STATIONS / {active.length} SCHEDULED IN FLIGHT
        </span>
        <span>POSITIONS INTERPOLATED FROM SIM TIME</span>
      </div>
    </div>
  );
}
