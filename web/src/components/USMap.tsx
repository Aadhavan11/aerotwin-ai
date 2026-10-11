import { useNavigate } from "react-router-dom";
import type { AircraftSummary, Station } from "../api/types";
import { STATUS_STYLE } from "../lib/format";

// Rough outline of the contiguous US as [lon, lat]; offline, no map tiles.
const OUTLINE: [number, number][] = [[-124.7,48.4],[-123.0,48.3],[-122.8,49.0],[-95.2,49.0],[-94.6,48.7],[-93.0,48.6],[-89.6,48.0],[-88.4,48.3],[-84.9,46.9],[-84.1,46.5],[-83.5,46.0],[-82.5,45.3],[-82.4,43.0],[-83.1,42.3],[-82.6,41.7],[-79.0,42.8],[-79.0,43.3],[-76.4,43.6],[-75.0,44.9],[-71.5,45.0],[-70.8,45.3],[-70.0,46.7],[-69.2,47.4],[-67.8,47.1],[-67.8,45.7],[-67.0,44.8],[-70.0,43.8],[-70.7,42.7],[-70.0,41.8],[-71.5,41.4],[-73.7,40.9],[-74.0,40.5],[-74.0,39.5],[-75.5,38.5],[-75.9,37.0],[-76.0,36.9],[-75.5,35.3],[-76.5,34.7],[-78.5,33.8],[-79.9,32.6],[-81.4,30.8],[-81.2,29.8],[-80.0,26.8],[-80.2,25.5],[-81.0,25.2],[-81.8,26.1],[-82.7,27.5],[-82.7,29.0],[-84.0,30.1],[-85.3,29.7],[-87.5,30.3],[-89.0,30.3],[-89.4,29.0],[-90.5,29.1],[-91.8,29.5],[-93.8,29.7],[-94.7,29.3],[-96.5,28.3],[-97.4,27.3],[-97.2,25.9],[-99.1,26.4],[-99.5,27.5],[-100.7,29.1],[-101.4,29.8],[-102.4,29.8],[-103.1,29.0],[-104.5,29.6],[-104.9,30.6],[-106.5,31.8],[-108.2,31.8],[-108.2,31.3],[-111.1,31.3],[-114.8,32.5],[-117.1,32.5],[-117.3,33.2],[-118.5,34.0],[-120.6,34.5],[-121.9,36.6],[-122.5,37.8],[-123.8,39.3],[-124.4,40.4],[-124.1,42.0],[-124.5,43.0],[-124.1,46.2],[-124.7,48.4]];
const W = 1000, H = 560;
const px = (lon: number) => ((lon + 125) / 59) * W;
const py = (lat: number) => ((50 - lat) / 26) * H;

export default function USMap({ stations, aircraft }: { stations: Station[]; aircraft: AircraftSummary[] }) {
  const nav = useNavigate();
  const byStation = new Map<string, AircraftSummary[]>();
  aircraft.forEach((a) => byStation.set(a.location, [...(byStation.get(a.location) ?? []), a]));
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full rounded-lg bg-slate-50" role="img" aria-label="Map of stations and aircraft">
      <polygon points={OUTLINE.map(([lo, la]) => `${px(lo)},${py(la)}`).join(" ")} fill="#f1f5f9" stroke="#cbd5e1" strokeWidth={2} />
      {stations.map((s) => {
        const x = px(s.lon), y = py(s.lat);
        const here = byStation.get(s.iata) ?? [];
        const closed = s.hangar_slots === 0;
        return (
          <g key={s.iata}>
            <circle cx={x} cy={y} r={9} fill="white" stroke={closed ? "#ef4444" : "#475569"} strokeWidth={2} />
            <text x={x + 24 + Math.ceil(here.length / 8) * 9} y={y + 5} fontSize={15} fontWeight={700} fill="#334155" stroke="#f1f5f9" strokeWidth={3} paintOrder="stroke">{s.iata}</text>
            <title>{`${s.iata}: ${s.hangar_slots} hangar slot(s), ${s.technicians.length} technicians, ${here.length} aircraft here`}</title>
            {here.map((a, i) => {
              const ang = (i / Math.max(here.length, 1)) * Math.PI * 2 - Math.PI / 2;
              const r = 20 + Math.floor(i / 8) * 9;
              const ax = x + Math.cos(ang) * r, ay = y + Math.sin(ang) * r;
              const c = STATUS_STYLE[a.status]?.dot ?? "#64748b";
              return (
                <g key={a.tail} className="cursor-pointer" onClick={() => nav(`/aircraft/${a.tail}`)}>
                  {a.status === "alert" && <circle cx={ax} cy={ay} r={6} fill={c} className="ping-soft" />}
                  <circle cx={ax} cy={ay} r={6} fill={c} stroke="white" strokeWidth={1.5} />
                  <title>{`${a.tail} · ${a.status}${a.worst_component ? " · " + a.worst_component + " RUL p10 " + a.rul_p10_min : ""}`}</title>
                </g>
              );
            })}
          </g>
        );
      })}
    </svg>
  );
}
