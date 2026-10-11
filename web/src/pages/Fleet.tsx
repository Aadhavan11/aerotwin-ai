import { useNavigate } from "react-router-dom";
import { usePoll } from "../api/client";
import type { FleetOverview } from "../api/types";
import { Card, ErrorBox, Kpi, Loading, StatusPill } from "../components/ui";
import USMap from "../components/USMap";
import { STATUS_STYLE, STATUS_ORDER } from "../lib/format";

export default function Fleet() {
  const nav = useNavigate();
  const { data, error, source } = usePoll<FleetOverview>("/fleet", 3000);
  if (!data) return error ? <ErrorBox error={error} /> : <Loading what="fleet" />;
  const k = data.kpis;
  const rows = [...data.aircraft].sort((a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9) || (a.rul_p10_min ?? 999) - (b.rul_p10_min ?? 999));
  const counts = data.aircraft.reduce<Record<string, number>>((m, a) => ((m[a.status] = (m[a.status] ?? 0) + 1), m), {});
  return (
    <div className="space-y-4">
      <ErrorBox error={error} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Kpi label="Aircraft" value={k.aircraft} hint={`data: ${source || "?"}`} />
        <Kpi label="Needing attention" value={k.needing_attention} tone={k.needing_attention ? "amber" : "emerald"} hint="watch or alert" />
        <Kpi label="Maintenance due in 72 h" value={k.predicted_maintenance_72h} tone={k.predicted_maintenance_72h ? "amber" : "slate"} />
        <Kpi label="In maintenance" value={k.in_maintenance} tone="blue" />
        <Kpi label="Fleet readiness" value={`${k.readiness_pct}%`} tone={k.readiness_pct >= 95 ? "emerald" : "amber"} />
      </div>
      <div className="grid gap-4 lg:grid-cols-5">
        <Card title="Stations and aircraft" className="lg:col-span-3"
          right={<div className="flex flex-wrap gap-2 text-xs text-slate-600">{Object.entries(counts).map(([s, n]) => (
            <span key={s} className="flex items-center gap-1"><span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: STATUS_STYLE[s]?.dot }} />{STATUS_STYLE[s]?.label ?? s} {n}</span>))}</div>}>
          <USMap stations={data.stations} aircraft={data.aircraft} />
          <p className="mt-2 text-xs text-slate-500">Dots are aircraft parked at a station; click one to open its digital twin. A red ring on a station means its hangar is closed.</p>
        </Card>
        <Card title="Aircraft by risk" className="lg:col-span-2">
          <div className="max-h-[28rem] overflow-auto">
            <table className="w-full whitespace-nowrap text-left text-sm">
              <thead className="sticky top-0 bg-white text-xs uppercase text-slate-500">
                <tr><th className="py-1.5 pr-3">Tail</th><th className="pr-3">At</th><th className="pr-3">Status</th><th className="pr-3">Worst component</th><th className="text-right">RUL p10</th></tr>
              </thead>
              <tbody>
                {rows.map((a) => (
                  <tr key={a.tail} onClick={() => nav(`/aircraft/${a.tail}`)} className="cursor-pointer border-t border-slate-100 hover:bg-slate-50">
                    <td className="py-1.5 pr-3 font-medium">{a.tail}</td>
                    <td className="pr-3">{a.location}</td>
                    <td className="pr-3"><StatusPill status={a.status} /></td>
                    <td className="pr-3 text-xs text-slate-600">{a.worst_component?.split("/")[1] ?? "-"}</td>
                    <td className="text-right tabular-nums">{a.rul_p10_min ?? "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      </div>
    </div>
  );
}
