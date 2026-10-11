import type { ReactNode } from "react";

const COLOR: Record<string, [string, string]> = {
  ok: ["#4aa3ff", "#0066cc"],
  watch: ["#ffb340", "#b25000"],
  alert: ["#ff6b60", "#d70015"],
};

/** Circular gauge: the arc is the health, the colour is the status. Children sit in the middle. */
export default function Ring({ value, status = "ok", size = 44, stroke = 5, children }: { value: number; status?: string; size?: number; stroke?: number; children?: ReactNode }) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const v = Math.min(1, Math.max(0, value));
  const [a, b] = COLOR[status] ?? COLOR.ok;
  const id = `g${size}${status}`;
  return (
    <span className="health-ring" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden>
        <defs>
          <linearGradient id={id} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor={a} />
            <stop offset="1" stopColor={b} />
          </linearGradient>
        </defs>
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="rgba(0,0,0,0.07)" strokeWidth={stroke} />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={`url(#${id})`}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${c * v} ${c}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
      <span className="health-ring-label">{children}</span>
    </span>
  );
}
