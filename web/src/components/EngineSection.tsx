// Longitudinal section of a CFM56-type two-spool turbofan with the C-MAPSS sensors placed at the stations where they are measured.
// Schematic, not to manufacturer drawings; stage counts follow the engine type (CFM56-5B: 4-stage booster, CFM56-7B: 3-stage).
import type { ReactNode } from "react";

export type SensorMeta = {
  name: string;
  unit: string;
  label: string;
  module: string;
  station: string;
  description: string;
  healthy_mean: number | null;
  healthy_std: number | null;
  worsens: number;
};
export type GenericSpec = { kind: string; wear_signal: string; worsens: "up" | "down"; healthy: number; limit: number; unit: string; extras: string[] };
export type SensorCatalog = { engine: SensorMeta[]; generic: Record<string, GenericSpec>; baseline: string };

/** Standard deviations away from the healthy fleet baseline, signed so that positive means "towards failure". */
export function deviation(m: SensorMeta | undefined, v: number | undefined): number | null {
  if (!m || v == null || m.healthy_mean == null || !m.healthy_std) return null;
  return ((v - m.healthy_mean) / m.healthy_std) * (m.worsens || 1);
}
export function deviationClass(z: number | null) {
  if (z == null) return "";
  return z >= 3.5 ? "s-alert" : z >= 2 ? "s-watch" : "";
}
const UNIT: Record<string, string> = { degR: "°R", psia: "psia", rpm: "rpm", "pps/psi": "pps/psi", "lbm/s": "lb/s", "-": "" };
export const unitLabel = (u: string) => UNIT[u] ?? u;
const fmt = (v: number) => (Math.abs(v) >= 1000 ? v.toFixed(0) : Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(2));

const AX = 200; // engine axis in drawing units
const OFF = 26; // the drawing sits OFF units down, leaving room for tags above and below
type Module = "fan" | "lpc" | "hpc" | "combustor" | "hpt" | "lpt" | "bypass";
const MODULES: { id: Module; label: string; x0: number; x1: number }[] = [
  { id: "fan", label: "Fan", x0: 104, x1: 162 },
  { id: "lpc", label: "Booster (LPC)", x0: 166, x1: 228 },
  { id: "hpc", label: "HP compressor", x0: 232, x1: 374 },
  { id: "combustor", label: "Combustor", x0: 378, x1: 432 },
  { id: "hpt", label: "HPT", x0: 434, x1: 458 },
  { id: "lpt", label: "LP turbine", x0: 460, x1: 566 },
];
// Where each tagged sensor attaches on the drawing, and where its tag sits (above or below).
const ANCHOR: Record<string, { x: number; y: number; tx: number; row: number }> = {
  Nf: { x: 140, y: AX - 70, tx: 96, row: -2 },
  T24: { x: 228, y: AX - 64, tx: 206, row: -1 },
  T30: { x: 372, y: AX - 46, tx: 330, row: -2 },
  Ps30: { x: 370, y: AX - 46, tx: 450, row: -1 },
  T50: { x: 564, y: AX - 64, tx: 580, row: -2 },
  Nc: { x: 300, y: AX + 30, tx: 262, row: 1 },
  phi: { x: 404, y: AX + 44, tx: 404, row: 2 },
  W31: { x: 446, y: AX + 50, tx: 540, row: 1 },
};

function stages(x0: number, x1: number, n: number, rOut0: number, rOut1: number, rIn0: number, rIn1: number, fill: string) {
  const out: ReactNode[] = [];
  const w = (x1 - x0) / n;
  for (let i = 0; i < n; i++) {
    const xa = x0 + i * w;
    const t = (i + 0.5) / n;
    const ro = rOut0 + (rOut1 - rOut0) * t;
    const ri = rIn0 + (rIn1 - rIn0) * t;
    for (const s of [-1, 1]) {
      out.push(<rect key={`v${i}${s}`} x={xa + w * 0.08} y={s < 0 ? AX - ro : AX + ri} width={w * 0.28} height={ro - ri} fill="#d9dee2" />);
      out.push(<rect key={`b${i}${s}`} x={xa + w * 0.48} y={s < 0 ? AX - ro + 1 : AX + ri} width={w * 0.34} height={ro - ri - 1} fill={fill} />);
    }
  }
  return out;
}

export default function EngineSection({
  engine,
  values,
  catalog,
  selected,
  onSelect,
}: {
  engine: string;
  values: Record<string, number>;
  catalog: SensorCatalog;
  selected: string;
  onSelect: (sensor: string) => void;
}) {
  const lpcStages = /5B/.test(engine) ? 4 : 3;
  const meta = new Map(catalog.engine.map((m) => [m.name, m]));
  const selModule = meta.get(selected)?.module;
  const mirror = (d: string) => (
    <>
      <path d={d} />
      <path d={d} transform={`translate(0 ${2 * AX}) scale(1 -1)`} />
    </>
  );
  const tag = (name: string) => {
    const a = ANCHOR[name];
    const m = meta.get(name);
    if (!a || !m) return null;
    const v = values[name];
    const z = deviation(m, v);
    const cls = deviationClass(z);
    const ty = a.row === -2 ? 2 : a.row === -1 ? 30 : a.row === 1 ? 398 : 426;
    const sel = selected === name;
    return (
      <g key={name} className={`sec-tag ${cls} ${sel ? "sel" : ""}`} onClick={() => onSelect(name)} style={{ cursor: "pointer" }}>
        <line x1={a.x} y1={a.y + OFF} x2={a.tx + 44} y2={a.row < 0 ? ty + 22 : ty} stroke="#9aa1a7" strokeWidth={0.8} />
        <circle cx={a.x} cy={a.y + OFF} r={2.6} fill="#16191d" />
        <rect x={a.tx} y={ty} width={88} height={22} rx={2} fill={sel ? "#eef3f8" : "#fff"} stroke={sel ? "#2f5d8a" : "#cfcfca"} />
        <text x={a.tx + 6} y={ty + 15} fontSize={10.5}>
          <tspan fontWeight={600}>{name}</tspan>
          <tspan dx={5} className="mono">{v == null ? "-" : fmt(v)}</tspan>
          <tspan dx={3} fill="#6b717a">{z == null ? "" : `${z >= 0 ? "+" : ""}${z.toFixed(1)}σ`}</tspan>
        </text>
      </g>
    );
  };
  return (
    <div className="engine-section">
      <svg viewBox="0 0 700 452" role="img" aria-label={`${engine} engine section with sensor stations`}>
        <defs>
          <pattern id="hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="5" stroke="#d3d7da" strokeWidth="1" />
          </pattern>
        </defs>
        <g transform={`translate(0 ${OFF})`}>
        {/* module bands, clickable */}
        {MODULES.map((m) => (
          <rect
            key={m.id}
            x={m.x0}
            y={AX - 100}
            width={m.x1 - m.x0}
            height={200}
            fill={selModule === m.id ? "#eef3f8" : "transparent"}
            onClick={() => {
              const first = catalog.engine.find((s) => s.module === m.id && ANCHOR[s.name]);
              if (first) onSelect(first.name);
            }}
            style={{ cursor: "pointer" }}
          />
        ))}
        {/* nacelle (fan cowl and reverser) */}
        <g fill="url(#hatch)" stroke="#8d949a" strokeWidth={1}>
          {mirror("M40 64 C 46 48, 82 42, 140 40 L 400 44 C 470 48, 505 58, 524 70 L 510 74 C 470 66, 420 62, 380 62 L 168 62 C 120 61, 72 62, 52 68 Z")}
        </g>
        {/* core cowl / splitter */}
        <g fill="#eceeef" stroke="#8d949a" strokeWidth={1}>
          {mirror("M162 120 C 170 110, 190 106, 236 104 L 470 104 C 540 106, 586 118, 604 132 L 600 138 C 580 126, 540 116, 470 116 L 240 118 C 210 119, 188 122, 172 128 Z")}
        </g>
        {/* fan blades and spinner */}
        <g fill="#c4cad0" stroke="#7f878e" strokeWidth={0.8}>
          {mirror("M122 153 C 126 124, 130 96, 138 64 L 162 64 C 158 96, 154 126, 154 153 Z")}
        </g>
        <path d={`M56 ${AX} C 66 ${AX - 28}, 92 ${AX - 44}, 122 ${AX - 47} L 122 ${AX + 47} C 92 ${AX + 44}, 66 ${AX + 28}, 56 ${AX} Z`} fill="#4b5157" />
        {/* booster, HP compressor, combustor, turbines */}
        {stages(168, 226, lpcStages, 66, 58, 42, 40, "#b8c3cc")}
        {stages(234, 372, 9, 58, 44, 34, 36, "#a9b6c1")}
        <g fill="#ead2b5" stroke="#b98f63" strokeWidth={0.8}>
          {mirror(`M380 ${AX - 54} C 396 ${AX - 60}, 420 ${AX - 58}, 432 ${AX - 50} L 432 ${AX - 34} C 418 ${AX - 30}, 396 ${AX - 30}, 380 ${AX - 38} Z`)}
        </g>
        {stages(436, 456, 1, 50, 50, 38, 38, "#c9a17d")}
        {stages(462, 562, 4, 54, 68, 34, 30, "#bf9a7c")}
        {/* shafts and exhaust plug */}
        <rect x={120} y={AX - 4} width={446} height={8} fill="#9aa1a7" />
        <rect x={234} y={AX - 14} width={224} height={6} fill="#b5bbc0" />
        <rect x={234} y={AX + 8} width={224} height={6} fill="#b5bbc0" />
        <path d={`M564 ${AX - 30} C 600 ${AX - 28}, 640 ${AX - 14}, 668 ${AX} C 640 ${AX + 14}, 600 ${AX + 28}, 564 ${AX + 30} Z`} fill="#5d646a" />
        {/* stations */}
        {[
          ["2", 112],
          ["24", 228],
          ["3", 374],
          ["4", 434],
          ["5", 564],
        ].map(([n, x]) => (
          <g key={n}>
            <line x1={x} y1={AX - 136} x2={x} y2={AX + 136} stroke="#2f5d8a" strokeDasharray="3 3" strokeWidth={0.8} opacity={0.55} />
            <text x={Number(x) + 6} y={AX + 128} textAnchor="start" fontSize={10} fill="#2f5d8a" className="mono">
              {n}
            </text>
          </g>
        ))}
        {/* module labels */}
        {MODULES.map((m) => (
          <text key={m.id} x={(m.x0 + m.x1) / 2} y={AX + 116} textAnchor="middle" fontSize={9.5} fill="#4a5058">
            {m.id === "hpt" ? "HPT" : m.label.split(" ")[0] === "HP" ? "HPC" : m.label.replace("Booster (LPC)", "LPC")}
          </text>
        ))}
        <text x={300} y={AX - 112} textAnchor="middle" fontSize={9.5} fill="#6b717a">
          bypass duct
        </text>
        </g>
        {Object.keys(ANCHOR).map(tag)}
      </svg>
      <div className="engine-legend">
        <span>Dashed lines: engine stations 2, 24, 3, 4, 5.</span>
        <span>σ: distance from the healthy baseline, positive towards failure.</span>
        <span>
          {lpcStages}-stage booster, 9-stage HPC, 1-stage HPT, 4-stage LPT ({engine}).
        </span>
      </div>
    </div>
  );
}
