import { useMemo, useState } from "react";
import { runMonteCarlo, type ComparisonPlan, type MonteCarloResult, type RiskHistogram } from "../api/risk";
import { Card, Kpi, Loading } from "../components/ui";
import { metric } from "../lib/operations";
import { apiDetail, histogramPlot } from "../lib/ops2";
import { useRole } from "../lib/role";
import { useAvailable } from "../lib/useAvailable";

const percent = (value: number | null | undefined) => value == null ? "—" : `${metric(value * 100, 1)}%`;
const money = (value: number | null | undefined) => "$" + metric(value);

function Histogram({ histogram }: { histogram?: RiskHistogram | null }) {
  const plot = histogramPlot(histogram, 540, 180);
  if (!plot) return <p className="muted" role="status">No valid histogram available.</p>;
  const edges = histogram?.bin_edges ?? [];
  return <figure className="weibull" style={{ margin: 0 }}>
    <div className="overflow-auto"><svg width="100%" style={{ minWidth: 640 }} viewBox="0 0 640 260" role="img" aria-label="Monte-Carlo total cost histogram">
      <title>Distribution of total simulated cost in USD</title>
      <g transform="translate(60 20)">
        <path d="M 0 0 L 0 180 L 540 180" fill="none" stroke="currentColor" />
        {plot.ticks.map((tick, index) => <text key={index} x={-8} y={184 - (plot.max ? tick / plot.max : 0) * 180} textAnchor="end" fontSize={11}>{metric(tick, 1)}</text>)}
        {plot.bars.map(({ label, ...bar }, index) => <rect key={index} {...bar} fill="var(--accent)"><title>{label} USD: {metric(histogram?.counts?.[index])} trials</title></rect>)}
        <text x={0} y={202} fontSize={11}>{money(edges[0])}</text><text x={540} y={202} textAnchor="end" fontSize={11}>{money(edges[edges.length - 1])}</text>
        <text x={270} y={226} textAnchor="middle" fontSize={11}>Total cost (USD) · count of trials</text>
      </g>
    </svg></div>
  </figure>;
}

function Comparison({ comparison }: { comparison: NonNullable<MonteCarloResult["comparison"]> }) {
  const rows: { label: string; render: (plan: ComparisonPlan | null | undefined) => string }[] = [
    { label: "Plan id", render: (plan) => plan?.plan_id ?? "—" },
    { label: "Mode", render: (plan) => plan?.mode ?? "—" },
    { label: "Mean cost", render: (plan) => money(plan?.cost_usd?.mean) },
    { label: "CVaR95", render: (plan) => money(plan?.cost_usd?.cvar95) },
    { label: "P(miss any deadline)", render: (plan) => percent(plan?.p_miss_any_deadline) },
    { label: "Flights delayed mean", render: (plan) => metric(plan?.flights_delayed_mean, 1) },
    { label: "Flights cancelled mean", render: (plan) => metric(plan?.flights_cancelled_mean, 1) },
    { label: "AOG hours mean", render: (plan) => metric(plan?.aog_h_mean, 1) },
    { label: "Deadline misses mean", render: (plan) => metric(plan?.deadline_misses_mean, 1) },
  ];
  return <Card title="Optimised and baseline comparison">
    <div className="overflow-auto"><table className="data-table"><thead><tr><th>Metric</th><th>Optimised</th><th>Baseline</th></tr></thead><tbody>
      {rows.map((row) => <tr key={row.label}><td>{row.label}</td><td>{row.render(comparison.optimised)}</td><td>{row.render(comparison.baseline)}</td></tr>)}
    </tbody></table></div>
    <div className="facts"><Kpi label="Delta mean cost" value={money(comparison.delta_mean_cost_usd)} /><Kpi label="Delta CVaR95" value={money(comparison.delta_cvar95_usd)} /><Kpi label="Delta P(miss any deadline)" value={percent(comparison.delta_p_miss_any_deadline)} hint="Optimised minus baseline" /></div>
    <p className="muted">{comparison.note ?? "—"}</p>
  </Card>;
}

export default function Risk() {
  const { role } = useRole();
  const canEdit = role.id === "ops" || role.id === "maintenance" || role.id === "admin";
  const [planId, setPlanId] = useState("");
  const [n, setN] = useState(500);
  const [seed, setSeed] = useState("0");
  const [validation, setValidation] = useState("");
  const [request, setRequest] = useState<{ plan_id?: string; n: number; seed: number } | null>(null);
  const load = useMemo(() => request ? async () => {
    try { return { result: await runMonteCarlo(request), message: "" }; }
    catch (error) { return { result: null, message: apiDetail(error) }; }
  } : null, [request]);
  const simulation = useAvailable(load);
  const running = request != null && simulation.loading;
  const result = simulation.data?.result;
  const ranking = (result?.risk_ranking ?? []).filter((item) => item != null);
  const submit = () => {
    if (!canEdit || running) return;
    if (!seed.trim() || !Number.isSafeInteger(Number(seed)) || Number(seed) < 0) { setValidation("Enter a whole-number seed of at least 0."); return; }
    setValidation("");
    setRequest({ ...(planId.trim() ? { plan_id: planId.trim() } : {}), n, seed: Number(seed) });
  };
  return <div className="space-y-5">
    <div className="page-heading"><div><h1>Plan risk</h1><p>Simulate maintenance uncertainty and compare costs and deadline risk.</p></div><span className="sim-tag">SIMULATED</span></div>
    <Card title="Monte-Carlo simulation">
      {!canEdit ? <p className="muted" role="status">Not available for your role.</p> : <form className="insights-form flex flex-wrap items-end gap-2" onSubmit={(event) => { event.preventDefault(); submit(); }}>
        <label>Plan id (blank = current optimised plan)<input className="search" value={planId} onChange={(event) => setPlanId(event.target.value)} /></label>
        <label>Trials<select className="select" value={n} onChange={(event) => setN(Number(event.target.value))}>{[100, 500, 2000].map((value) => <option key={value} value={value}>{value}</option>)}</select></label>
        <label>Seed<input className="search" type="number" min={0} step={1} required value={seed} onChange={(event) => setSeed(event.target.value)} /></label>
        <button className="app-agents" type="submit" disabled={running}>{running ? "Simulating…" : "Simulate"}</button>
      </form>}
      {(validation || simulation.error || simulation.data?.message) && <p className="muted" role="status">{validation || simulation.error || simulation.data?.message}</p>}
      {running && <div role="status"><Loading what="simulation" /></div>}
      {result && <p className="muted">Elapsed {metric(result.elapsed_s, 2)} s · {result.cached == null ? "Cache status unavailable" : result.cached ? "served from cache" : "fresh run"} · n {metric(result.n)} · seed {metric(result.seed)}</p>}
    </Card>
    {result && <>
      <div className="facts"><Kpi label="P(miss any deadline)" value={percent(result.p_miss_any_deadline)} /><Kpi label="Mean cost" value={money(result.cost_usd?.mean)} /><Kpi label="CVaR95 cost" value={money(result.cost_usd?.cvar95)} /><Kpi label="P(unplanned AOG)" value={percent(result.p_unplanned_aog)} /></div>
      <Card title="Cost histogram"><Histogram histogram={result.histogram} /></Card>
      <Card title="Risk ranking">{!ranking.length ? <p className="muted" role="status">No risk ranking available.</p> : <div className="overflow-auto"><table className="data-table">
        <thead><tr>{["Rank", "Task id", "Tail", "Station", "Start", "End", "P(late)", "P(overrun infeasible)", "Expected cost", "Share of risk cost"].map((label) => <th key={label}>{label}</th>)}</tr></thead>
        <tbody>{ranking.map((item, index) => <tr key={`${item.task_id}-${index}`}>
          <td>{metric(item.rank)}</td><td>{item.task_id ?? "—"}</td><td>{item.tail ?? "—"}</td><td>{item.station ?? "—"}</td><td>{item.start ?? "—"}</td><td>{item.end ?? "—"}</td>
          <td>{percent(item.p_late)}</td><td>{percent(item.p_overrun_infeasible)}</td><td>{money(item.expected_cost_usd)}</td><td>{percent(item.share_of_risk_cost)}</td>
        </tr>)}</tbody>
      </table></div>}</Card>
      {result.comparison != null && <Comparison comparison={result.comparison} />}
    </>}
  </div>;
}
