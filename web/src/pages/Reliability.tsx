import { Link } from "react-router-dom";
import { getReliabilityPack, type AlertStatus, type ParetoItem } from "../api/reliability";
import ReliabilityTabs from "../components/ReliabilityTabs";
import { Card, Chip, Kpi } from "../components/ui";
import { metric, overallNff, paretoPlot, worstAlert } from "../lib/operations";
import { useAvailable } from "../lib/useAvailable";

function AlertChip({ status }: { status?: AlertStatus | null }) {
  return <Chip tone={status === "warning" || status === "alert" ? "amber" : "slate"}>{status?.replaceAll("_", " ") ?? "—"}</Chip>;
}

function Pareto({ items }: { items: ParetoItem[] }) {
  if (!items.length) return <p className="muted">No Pareto data available.</p>;
  const plot = paretoPlot(items);
  return (
    <figure className="weibull" style={{ margin: 0 }}>
      <figcaption>Unscheduled removals by ATA chapter · cumulative share</figcaption>
      <div className="overflow-auto">
        <svg width="100%" style={{ minWidth: Math.max(640, items.length * 55) }} viewBox="0 0 640 265" role="img" aria-label="ATA Pareto: removal counts as bars, cumulative share as a line">
          <title>Unscheduled removals and cumulative share by ATA chapter</title>
          <path d="M 50 30 L 50 210 L 570 210 L 570 30" fill="none" stroke="currentColor" />
          {[0, 0.5, 1].map((fraction) => <g key={fraction}>
            <text x={42} y={214 - fraction * 180} textAnchor="end" fontSize={11}>{metric(plot.max * fraction, 1)}</text>
            <text x={580} y={214 - fraction * 180} fontSize={11}>{fraction}</text>
          </g>)}
          {items.map((item, index) => {
            const bar = plot.bars[index];
            return <g key={`${item.ata_chapter}-${index}`}>
              <rect {...bar} fill="var(--accent)"><title>ATA {item.ata_chapter}: {metric(item.unscheduled_removals)} removals · cumulative {item.cumulative_share == null ? "—" : `${metric(item.cumulative_share * 100, 1)}%`}</title></rect>
              <text x={bar.x + bar.width / 2} y={230} textAnchor="middle" fontSize={11}>{item.ata_chapter}</text>
            </g>;
          })}
          <path d={plot.line} fill="none" stroke="var(--warn)" strokeWidth={2} />
          <text x={50} y={255} fontSize={11} fill="var(--accent)" style={{ fill: "var(--accent)" }}>Bars: removals (left)</text>
          <text x={570} y={255} textAnchor="end" fontSize={11} style={{ fill: "var(--warn)" }}>Line: cumulative share (right, 0–1)</text>
        </svg>
      </div>
    </figure>
  );
}

export default function Reliability() {
  const pack = useAvailable(getReliabilityPack);
  const data = pack.data;
  const kinds = data?.metrics?.per_kind ?? [];
  const alerts = data?.alerts ?? [];
  const repeats = data?.repeat_defects ?? [];
  const nff = overallNff(kinds);
  const worst = worstAlert(alerts);
  return (
    <div className="space-y-5">
      <ReliabilityTabs />
      {pack.loading ? <Card title="Reliability pack"><p className="console-loading animate-pulse" role="status">Computing reliability pack, this can take up to ~15 seconds…</p></Card> : pack.error || !data ? (
        <Card title="Reliability pack"><p className="muted" role="status">{pack.error ?? "Reliability is not available."}</p></Card>
      ) : <>
        <div className="facts">
          <Kpi label="Unscheduled removals" value={metric(data.unscheduled_removals_total)} />
          <Kpi label="Worst alert status" value={<AlertChip status={worst} />} tone={worst === "alert" ? "red" : worst === "warning" ? "amber" : "slate"} />
          <Kpi label="Overall NFF rate" value={nff === null ? "—" : `${metric(nff * 100, 1)}%`} hint="No fault found removals / unscheduled removals" />
        </div>
        <Card title="Per-kind reliability" right={<span className="sim-tag">SIMULATED</span>}>
          {!kinds.length ? <p className="muted">No reliability kinds available.</p> : <div className="overflow-auto"><table className="data-table">
            <thead><tr><th>Kind</th><th>ATA</th><th>Removals</th><th>Unscheduled</th><th>MTBUR (FH / FC)</th><th>Removal rate / 1,000 FH (CI)</th><th>Alert status</th></tr></thead>
            <tbody>{kinds.map((kind, index) => <tr key={`${kind.kind}-${index}`}>
              <td>{kind.kind}</td><td>{kind.ata_section ?? kind.ata_chapter ?? "—"}</td><td>{metric(kind.removals)}</td><td>{metric(kind.unscheduled_removals)}</td>
              <td>{metric(kind.mtbur_fh, 1)} / {metric(kind.mtbur_fc, 1)}</td>
              <td>{metric(kind.removal_rate_per_1000fh, 2)} ({metric(kind.rate_ci_low, 2)}–{metric(kind.rate_ci_high, 2)})</td>
              <td><AlertChip status={kind.alert_status} /></td>
            </tr>)}</tbody>
          </table></div>}
        </Card>
        <Card title="ATA Pareto"><Pareto items={data.pareto ?? []} /></Card>
        <Card title="Alerts">
          {!alerts.length ? <p className="muted">No alerts available.</p> : <ul className="suggest">{alerts.map((alert, index) => <li key={`${alert.kind}-${index}`}>
            <div><div className="suggest-head"><b>{alert.kind}</b><span>ATA {alert.ata_chapter}</span><AlertChip status={alert.status} /></div>
              <p>{metric(alert.latest_count)} removals over {metric(alert.period_days)} days · {metric(alert.latest_rate_per_1000fh, 2)} / 1,000 FH · alert threshold {metric(alert.threshold_per_1000fh, 2)}</p>
              <small>{alert.rule}</small>
            </div>
          </li>)}</ul>}
        </Card>
        <Card title="Repeat defects">
          {!repeats.length ? <p className="muted">No repeat defects.</p> : <div className="overflow-auto"><table className="data-table">
            <thead><tr><th>Tail</th><th>Kind / position</th><th>Count</th><th>Window (days)</th><th>Reasons</th></tr></thead>
            <tbody>{repeats.map((defect, index) => <tr key={`${defect.tail}-${defect.position}-${index}`}>
              <td><Link to={`/aircraft/${encodeURIComponent(defect.tail)}`}>{defect.tail}</Link></td><td>{defect.kind} / {defect.position}</td>
              <td>{metric(defect.count)}</td><td>{metric(defect.window_days)}</td><td>{(defect.reasons ?? []).join(", ") || "—"}</td>
            </tr>)}</tbody>
          </table></div>}
        </Card>
        <Card title="Provenance">
          <p className="muted">{data.provenance ?? "—"} · As of {data.as_of ?? "—"} · {data.taxonomy_label ?? "—"}</p>
          <p className="muted">{Object.entries(data.source ?? {}).map(([key, value]) => `${key}: ${typeof value === "number" ? metric(value, 2) : value ?? "—"}`).join(" · ") || "Source not available."}</p>
          <p className="muted">{data.disclaimer ?? "—"}</p>
        </Card>
      </>}
    </div>
  );
}
