import type { ReactNode } from "react";
import { STATUS_STYLE } from "../lib/format";

export function Card({
  title,
  right,
  children,
  className = "",
}: {
  title?: string;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`instrument-card p-4 ${className}`}
    >
      {(title || right) && (
        <header className="mb-3 flex items-center justify-between gap-2">
          {title && (
            <h2 className="text-sm font-semibold text-slate-700">{title}</h2>
          )}
          {right}
        </header>
      )}
      {children}
    </section>
  );
}

export function Kpi({
  label,
  value,
  hint,
  tone = "slate",
}: {
  label: string;
  value: ReactNode;
  hint?: string;
  tone?: "slate" | "red" | "amber" | "emerald" | "blue";
}) {
  const tones = {
    slate: "text-slate-900",
    red: "text-red-600",
    amber: "text-amber-600",
    emerald: "text-emerald-600",
    blue: "text-blue-600",
  };
  return (
    <div className="instrument-card p-4">
      <div className="text-xs text-slate-500">{label}</div>
      <div className={`mt-1 font-mono text-2xl font-medium ${tones[tone]}`}>
        {value}
      </div>
      {hint && <div className="mt-1 text-xs text-slate-500">{hint}</div>}
    </div>
  );
}

export function StatusPill({ status }: { status: string }) {
  const s = STATUS_STYLE[status];
  return <span className={`status s-${status}`}>{s?.label ?? status}</span>;
}

export function Btn({
  children,
  onClick,
  disabled,
  tone = "default",
  className = "",
  title,
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  tone?: "default" | "primary" | "danger" | "ghost";
  className?: string;
  title?: string;
}) {
  const tones = {
    default:
      "border border-slate-300 bg-white text-slate-800 hover:bg-slate-100",
    primary: "bg-[#16191d] text-white hover:bg-[#2f3338]",
    danger: "bg-red-600 text-white hover:bg-red-500",
    ghost: "text-slate-600 hover:bg-slate-100",
  };
  return (
    <button
      title={title}
      disabled={disabled}
      onClick={onClick}
      className={`rounded px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${tones[tone]} ${className}`}
    >
      {children}
    </button>
  );
}

export function Chip({
  children,
  tone = "slate",
  title,
}: {
  children: ReactNode;
  tone?: string;
  title?: string;
}) {
  const tones: Record<string, string> = {
    slate: "bg-slate-100 text-slate-700",
    blue: "bg-blue-50 text-blue-700",
    amber: "bg-amber-50 text-amber-800",
  };
  return (
    <span
      title={title}
      className={`inline-block rounded-sm px-1.5 py-0.5 text-xs ${tones[tone] ?? tones.slate}`}
    >
      {children}
    </span>
  );
}

export const Loading = ({ what = "data" }: { what?: string }) => (
  <div className="p-6 text-sm text-slate-500">Loading {what}…</div>
);

export function ErrorBox({ error }: { error?: string }) {
  if (!error) return null;
  return (
    <div className="border-l-2 border-red-700 bg-red-50 p-3 text-sm text-red-800">
      {error}{" "}
      <span className="text-red-600">(showing the last good data if any)</span>
    </div>
  );
}
