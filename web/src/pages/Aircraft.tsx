// STUB. The full Aircraft Twin screen (clickable 2D aircraft, RUL band chart, sensor trend, timeline) is Aadhavan's:
// work/OWNERSHIP.md piece 13, spec work/04-AADHAVAN-frontend-and-story.md screen 2. Replace this file; keep the route /aircraft/:tail.
import { Link, useParams } from "react-router-dom";
import { usePoll } from "../api/client";
import type { TwinView } from "../api/types";
import { Card, ErrorBox, Loading, StatusPill } from "../components/ui";
import { fmtZ } from "../lib/format";

export default function Aircraft() {
  const { tail = "" } = useParams();
  const { data, error } = usePoll<TwinView>(`/aircraft/${tail}`, 3000);
  if (!data) return error ? <ErrorBox error={error} /> : <Loading what={tail} />;
  const a = data.aircraft;
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 text-sm"><Link to="/" className="text-blue-600 hover:underline">← Fleet</Link>
        <h1 className="text-xl font-semibold">{a.tail}</h1><StatusPill status={a.status} />
        <span className="text-slate-500">{a.type} · home {a.home} · at {a.location} · {a.cycles} cycles</span></div>
      <p className="rounded-lg border border-dashed border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">Placeholder page: the interactive Aircraft Twin screen is being built by Aadhavan (work/OWNERSHIP.md piece 13). This shows the same data in a plain list.</p>
      <div className="grid gap-3 md:grid-cols-3">
        {data.components.map((c) => (
          <Card key={c.component_id} title={c.component_id.split("/")[1]} right={<StatusPill status={c.status} />}>
            <div className="h-2 overflow-hidden rounded bg-slate-100"><div className="h-full" style={{ width: `${c.health * 100}%`, background: c.health < 0.3 ? "#ef4444" : c.health < 0.6 ? "#f59e0b" : "#10b981" }} /></div>
            <div className="mt-2 text-sm">Health {(c.health * 100).toFixed(0)}% · anomaly {c.anomaly_score.toFixed(2)}</div>
            <div className="text-sm text-slate-600">RUL p10/p50/p90: {c.rul_cycles ? `${c.rul_cycles.p10} / ${c.rul_cycles.p50} / ${c.rul_cycles.p90} cycles` : "n/a"}</div>
            <div className="text-xs text-slate-500">data quality: {c.data_quality.status}</div>
          </Card>
        ))}
      </div>
      <Card title="Timeline"><ul className="space-y-1 text-sm">{data.timeline.map((e, i) => <li key={i}><span className="text-slate-500">{fmtZ(e.ts)}</span> · <b>{e.kind}</b> · {e.summary}</li>)}</ul></Card>
    </div>
  );
}
