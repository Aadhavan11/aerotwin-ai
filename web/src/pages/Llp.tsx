import { useMemo, useState } from "react";
import { getLlp, getLlpTail, getShopVisits, type LlpPart, type PipelineMonth } from "../api/llp";
import { Btn, Card, Chip, Kpi, Loading } from "../components/ui";
import { metric } from "../lib/operations";
import { pipelinePlot } from "../lib/ops2";
import { useAvailable } from "../lib/useAvailable";

function PartsTable({ parts, select }: { parts: LlpPart[]; select: (tail: string) => void }) {
  if (!parts.length) return <p className="muted" role="status">No parts match these filters.</p>;
  return <div className="overflow-auto"><table className="data-table">
    <thead><tr>{["Tail", "Position / label", "Scope", "Part no / serial", "CSN / limit", "Remaining cycles", "Remaining days", "Limit date", "Driver", "Status"].map((label) => <th key={label}>{label}</th>)}</tr></thead>
    <tbody>{parts.map((part, index) => <tr key={`${part.tail}-${part.position}-${index}`}>
      <td><Btn disabled={!part.tail} onClick={() => { if (part.tail) select(part.tail); }}>{part.tail ?? "—"}</Btn></td>
      <td>{part.position ?? "—"} / {part.label ?? "—"}</td><td>{part.scope ?? "—"}</td><td>{part.part_no ?? "—"} / {part.serial ?? "—"}</td>
      <td>{metric(part.csn)} / {metric(part.cycle_limit)}</td><td>{metric(part.remaining_cycles)}</td><td>{metric(part.remaining_days, 1)}</td>
      <td>{part.limit_date ?? "—"}</td><td>{part.limit_driver ?? "—"}</td>
      <td><Chip tone={part.status && part.status !== "ok" ? "amber" : "slate"}>{part.status ?? "—"}</Chip></td>
    </tr>)}</tbody>
  </table></div>;
}

function Pipeline({ pipeline }: { pipeline: PipelineMonth[] }) {
  const plot = pipelinePlot(pipeline, 540, 180);
  if (!plot.bars.length) return <p className="muted" role="status">No pipeline data available.</p>;
  return <figure className="weibull" style={{ margin: 0 }}>
    <div className="overflow-auto"><svg width="100%" style={{ minWidth: 640 }} viewBox="0 0 640 260" role="img" aria-label="12-month shop-visit pipeline">
      <title>Monthly expected shop visits and cumulative expected and p50 counts</title>
      <g transform="translate(60 20)">
        <path d="M 0 0 L 0 180 L 540 180" fill="none" stroke="currentColor" />
        {[0, 0.5, 1].map((fraction) => <text key={fraction} x={-8} y={184 - fraction * 180} textAnchor="end" fontSize={11}>{metric(plot.max * fraction, 1)}</text>)}
        {plot.bars.map((bar, index) => <g key={index}><rect {...bar} fill="var(--accent)" /><text x={bar.x + bar.width / 2} y={200} textAnchor="middle" fontSize={11}>{plot.labels[index]}</text></g>)}
        <path d={plot.linePath} fill="none" stroke="var(--warn)" strokeWidth={2} />
        <path d={plot.dashedPath} fill="none" stroke="var(--muted)" strokeWidth={2} strokeDasharray="6 4" />
        <text x={270} y={220} textAnchor="middle" fontSize={11}>Month</text>
      </g>
    </svg></div>
    <figcaption>Bars: monthly expected · Solid: cumulative expected · Dashed: cumulative p50 count</figcaption>
  </figure>;
}

export default function Llp() {
  const [tab, setTab] = useState<"parts" | "pipeline">("parts");
  const [tail, setTail] = useState("");
  const [status, setStatus] = useState("all");
  const [selected, setSelected] = useState("");
  const filterTail = tail.trim() || undefined;
  const loadParts = useMemo(() => tab === "parts" ? () => getLlp(filterTail) : null, [tab, filterTail]);
  const loadVisits = useMemo(() => tab === "pipeline" ? () => getShopVisits(filterTail) : null, [tab, filterTail]);
  const loadTail = useMemo(() => selected && tab === "parts" ? () => getLlpTail(selected) : null, [selected, tab]);
  const parts = useAvailable(loadParts);
  const visits = useAvailable(loadVisits);
  const detail = useAvailable(loadTail);
  const list = (parts.data?.parts ?? []).filter((part) => part && (status === "all" || part.status === status));
  const engines = (visits.data?.engines ?? []).filter((engine) => engine != null);
  return <div className="space-y-5">
    <div className="page-heading"><div><h1>Life-limited parts</h1><p>Track part limits and forecast engine shop visits.</p></div><span className="sim-tag">SIMULATED</span></div>
    <nav className="seg" aria-label="Life-limited parts views">
      <button className={tab === "parts" ? "on" : ""} aria-current={tab === "parts" ? "page" : undefined} onClick={() => setTab("parts")}>Parts</button>
      <button className={tab === "pipeline" ? "on" : ""} aria-current={tab === "pipeline" ? "page" : undefined} onClick={() => setTab("pipeline")}>Shop-visit pipeline</button>
    </nav>
    <Card title="Filters"><div className="insights-form flex flex-wrap items-end gap-2">
      <label>Tail (blank = all)<input className="search" value={tail} onChange={(event) => setTail(event.target.value)} /></label>
      {tab === "parts" && <label>Status<select className="select" value={status} onChange={(event) => setStatus(event.target.value)}>{["all", "ok", "watch", "alert", "expired"].map((value) => <option key={value}>{value}</option>)}</select></label>}
    </div></Card>
    {tab === "parts" ? <>
      <div className="facts">{(["ok", "watch", "alert", "expired"] as const).map((key) => <Kpi key={key} label={key} value={metric(parts.data?.summary?.[key])} tone={key === "ok" ? "slate" : "amber"} />)}</div>
      <Card title="Parts">{parts.loading ? <Loading what="parts" /> : parts.error || !parts.data ? <p className="muted" role="status">{parts.error ?? "Parts are not available."}</p> : <PartsTable parts={list} select={setSelected} />}</Card>
      {selected && <Card title={`Aircraft ${selected}`}>
        {detail.loading ? <Loading what="aircraft parts" /> : detail.error || !detail.data ? <p className="muted" role="status">{detail.error ?? "Aircraft is not available."}</p> : <>
          <div className="facts"><Kpi label="Aircraft cycles" value={metric(detail.data.aircraft?.cycles)} /><Kpi label="Cycles per day" value={metric(detail.data.aircraft?.cycles_per_day, 1)} /><Kpi label="Location" value={detail.data.aircraft?.location ?? "—"} /></div>
          <PartsTable parts={(detail.data.parts ?? []).filter((part) => part != null)} select={setSelected} />
        </>}
      </Card>}
    </> : <>
      <div className="facts"><Kpi label="Engines" value={metric(visits.data?.count)} />{(["egt_margin", "llp", "rul"] as const).map((key) => <Kpi key={key} label={key} value={metric(visits.data?.drivers?.[key])} />)}</div>
      <Card title="Ranked engines">{visits.loading ? <Loading what="shop visits" /> : visits.error || !visits.data ? <p className="muted" role="status">{visits.error ?? "Shop visits are not available."}</p> : !engines.length ? <p className="muted" role="status">No engines match this tail.</p> : <div className="overflow-auto"><table className="data-table">
        <thead><tr>{["Rank", "Tail", "Engine", "Driver", "p10 date", "p50 date", "p90 date", "Overdue", "Notes"].map((label) => <th key={label}>{label}</th>)}</tr></thead>
        <tbody>{engines.map((engine, index) => <tr key={`${engine.component_id}-${index}`}>
          <td>{metric(engine.rank)}</td><td>{engine.tail ?? "—"}</td><td>{engine.engine ?? "—"}</td>
          <td><Chip tone={engine.driver === "llp" ? "amber" : "slate"}>{engine.driver ?? "—"}</Chip>{engine.driver === "llp" && <span> {engine.driver_llp_part ?? "—"}</span>}</td>
          <td>{engine.p10_date ?? "—"}</td><td>{engine.p50_date ?? "—"}</td><td>{engine.p90_date ?? "—"}</td>
          <td>{engine.overdue == null ? "—" : <Chip tone={engine.overdue ? "amber" : "slate"}>{engine.overdue ? "Overdue" : "No"}</Chip>}</td>
          <td>{engine.rul_censored ? "RUL censored at 125 cycles, not used as driver" : "—"}</td>
        </tr>)}</tbody>
      </table></div>}</Card>
      <Card title="12-month pipeline">{visits.loading ? <Loading what="pipeline" /> : visits.error ? <p className="muted" role="status">{visits.error}</p> : <Pipeline pipeline={visits.data?.pipeline ?? []} />}</Card>
    </>}
  </div>;
}
