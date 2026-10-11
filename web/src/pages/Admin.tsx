import { useState } from "react";
import { post, usePoll } from "../api/client";
import type { AuditEntry, HealthInfo, MetricsBundle } from "../api/types";
import { Btn, Card, Chip, ErrorBox, Kpi, Loading } from "../components/ui";
import { downloadCsv } from "../lib/derive";
import { fmtZ } from "../lib/format";

const ML_LABELS: Record<string, string> = {
  model_version: "Model version",
  data_source: "Training data",
  n_test: "Held-out test engines",
  rul_mae: "Remaining-life error, MAE (cycles)",
  rul_rmse: "Remaining-life error, RMSE (cycles)",
  rul_mae_late_life: "Remaining-life error late in life (cycles)",
  rul_interval_coverage_calibrated: "Share of cases inside the p10 to p90 band",
  anomaly_precision: "Anomaly precision",
  anomaly_recall: "Anomaly recall",
  anomaly_fpr: "Anomaly false-positive rate",
};
const PLAN_LABELS: Record<string, string> = {
  downtime_h: "Downtime (h)",
  flights_affected: "Flights affected",
  logistics_cost_usd: "Logistics cost ($)",
  tasks_total: "Tasks",
  tasks_scheduled: "Tasks scheduled",
  late_or_unscheduled: "Late or unscheduled",
  schedule_changes: "Schedule changes",
};
const show = (v: unknown): string => (typeof v === "number" ? String(Math.round(v * 10000) / 10000) : typeof v === "string" ? v : JSON.stringify(v));

/** Administrator: are the services real and healthy, how good are the data and models, who did what, and scenario controls. */
export default function Admin() {
  const health = usePoll<HealthInfo>("/health", 4000);
  const metrics = usePoll<MetricsBundle>("/metrics", 10000);
  const audit = usePoll<AuditEntry[]>("/audit", 4000);
  const [filter, setFilter] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const h = health.data;
  if (!h) return health.error ? <ErrorBox error={health.error} /> : <Loading what="system status" />;

  const modules = Object.entries(h.modules);
  const real = modules.filter(([, v]) => v === "real").length;
  const entries = [...(audit.data ?? [])].reverse();
  const f = filter.trim().toLowerCase();
  const rows = f ? entries.filter((e) => `${e.actor} ${e.action} ${e.ref} ${e.detail}`.toLowerCase().includes(f)) : entries;
  const ml = metrics.data?.ml ?? {};
  const planBlock = (metrics.data?.plan ?? {}) as Record<string, unknown>;
  const base = (planBlock.baseline ?? {}) as Record<string, unknown>;
  const opt = (planBlock.optimized ?? {}) as Record<string, unknown>;

  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(true);
    setMsg("");
    try {
      await fn();
      setMsg(`${label}: done`);
      audit.reload();
      health.reload();
    } catch (e) {
      setMsg(`${label} failed: ${String((e as Error).message)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      <div className="page-heading">
        <div>
          <div className="eyebrow">System and audit</div>
          <h1>System health and the audit trail</h1>
          <p>Which services run real code, how good the data and models are, who did what, and scenario controls.</p>
        </div>
      </div>
      <ErrorBox error={health.error ?? metrics.error ?? audit.error} />

      <div className="facts">
        <Kpi label="Services running real code" value={`${real} of ${modules.length}`} tone={real === modules.length ? "emerald" : "amber"} hint={h.mock ? "Fixture mode is on" : "Not in fixture mode"} />
        <Kpi label="Import errors" value={Object.keys(h.errors).length} tone={Object.keys(h.errors).length ? "red" : "emerald"} hint="Modules that failed to load" />
        <Kpi label="Audit entries" value={entries.length} hint="Every decision is recorded" />
        <Kpi label="Simulation clock" value={fmtZ(h.sim_now)} hint={`API ${h.version}`} />
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Services">
          <table className="w-full text-sm">
            <tbody>
              {modules.map(([k, v]) => (
                <tr key={k} className="border-t border-slate-200 first:border-0">
                  <td className="py-1.5 text-slate-800">{k.replace("whatif", "what-if")}</td>
                  <td className="py-1.5 text-right">
                    <Chip tone={v === "real" ? "blue" : "amber"}>{v === "real" ? "real module" : v}</Chip>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {Object.entries(h.errors).map(([k, v]) => (
            <div key={k} className="mt-2 rounded-md border border-red-200 p-2 text-xs text-red-600">
              {k}: {v}
            </div>
          ))}
          <div className="mt-3 space-y-0.5 text-xs text-slate-500">
            <div>Data source: {h.data_source}</div>
            <div>
              Language-model mode: {h.llm_key_present ? `available (${h.llm_model})` : "not configured. The scripted path is used and every number comes from the tools"}
            </div>
          </div>
        </Card>

        <Card title="Scenario controls">
          <p className="mb-3 text-sm text-slate-500">Administrators can move the simulation clock or restore the starting scenario. Both are written to the audit log.</p>
          <div className="flex flex-wrap gap-2">
            <Btn disabled={busy} onClick={() => act("Advance 24 h", () => post("/sim/step", { hours: 24 }))}>
              Advance simulation 24 h
            </Btn>
            <Btn
              tone="danger"
              disabled={busy}
              onClick={() => {
                if (window.confirm("Reset the simulation to the golden scenario? Work orders and the current plan will be cleared.")) act("Reset", () => post("/scenario/reset", { name: "golden" }));
              }}
            >
              Reset to the golden scenario
            </Btn>
          </div>
          {msg && <p className="mt-2 text-xs text-slate-500">{msg}</p>}
          <div className="mt-4 rounded-lg border border-slate-200 p-3 text-xs text-slate-500">
            <b className="text-slate-700">Governance.</b> Advisory only: nothing here certifies an aircraft or executes maintenance. Work orders always wait for a named person. Roles in this prototype choose what is shown and who sees the approve buttons; they are not
            authentication.
          </div>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Card title="Health model: data and quality">
          {Object.keys(ml).length === 0 ? (
            <p className="text-sm text-slate-500">No model metrics available.</p>
          ) : (
            <>
              <table className="w-full text-sm">
                <tbody>
                  {Object.keys(ML_LABELS)
                    .filter((k) => k in ml)
                    .map((k) => (
                      <tr key={k} className="border-t border-slate-200 first:border-0">
                        <td className="py-1.5 text-slate-600">{ML_LABELS[k]}</td>
                        <td className="py-1.5 text-right tabular-nums text-slate-800">{show(ml[k])}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
              <details className="mt-3 text-xs text-slate-500">
                <summary className="cursor-pointer text-blue-600">All {Object.keys(ml).length} recorded values</summary>
                <table className="mt-2 w-full">
                  <tbody>
                    {Object.entries(ml)
                      .filter(([k, v]) => !(k in ML_LABELS) && (typeof v === "number" || typeof v === "string"))
                      .map(([k, v]) => (
                        <tr key={k} className="border-t border-slate-200">
                          <td className="py-1">{k.replace(/_/g, " ")}</td>
                          <td className="py-1 text-right tabular-nums text-slate-700">{show(v)}</td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              </details>
            </>
          )}
          <p className="mt-2 text-xs text-slate-500">Measured on held-out simulated engines, not on airline data.</p>
        </Card>
        <Card title="Plan quality: optimized vs baseline">
          {Object.keys(opt).length === 0 ? (
            <p className="text-sm text-slate-500">No plan metrics yet.</p>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-slate-500">
                  <th className="py-1 font-medium">Metric</th>
                  <th className="py-1 text-right font-medium">Optimized</th>
                  <th className="py-1 text-right font-medium">Baseline</th>
                </tr>
              </thead>
              <tbody>
                {Object.keys(PLAN_LABELS)
                  .filter((k) => k in opt)
                  .map((k) => (
                    <tr key={k} className="border-t border-slate-200">
                      <td className="py-1.5 text-slate-600">{PLAN_LABELS[k]}</td>
                      <td className="py-1.5 text-right tabular-nums text-slate-800">{show(opt[k])}</td>
                      <td className="py-1.5 text-right tabular-nums text-slate-500">{show(base[k])}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          )}
          {typeof planBlock.replan_seconds === "number" && <p className="mt-2 text-xs text-slate-500">Last re-plan took {show(planBlock.replan_seconds)} s.</p>}
        </Card>
      </div>

      <Card
        title="Audit trail"
        right={
          <div className="flex items-center gap-2">
            <input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter by who, what or reference"
              aria-label="Filter the audit trail"
              className="w-56 rounded-md border border-slate-300 bg-transparent px-2 py-1 text-xs text-slate-800"
            />
            <Btn onClick={() => downloadCsv("aerotwin-audit.csv", [["time", "actor", "action", "reference", "detail"], ...entries.map((e) => [e.ts, e.actor, e.action, e.ref, e.detail])])}>Export CSV</Btn>
          </div>
        }
      >
        {rows.length === 0 ? (
          <p className="text-sm text-slate-500">{entries.length === 0 ? "No audit entries yet." : "No entries match the filter."}</p>
        ) : (
          <div className="max-h-96 overflow-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-white">
                <tr className="text-left text-slate-500">
                  <th className="py-1 pr-3 font-medium">Time (UTC)</th>
                  <th className="py-1 pr-3 font-medium">Who</th>
                  <th className="py-1 pr-3 font-medium">Action</th>
                  <th className="py-1 pr-3 font-medium">Reference</th>
                  <th className="py-1 font-medium">Detail</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((e, i) => (
                  <tr key={i} className="border-t border-slate-200 align-top">
                    <td className="py-1 pr-3 tabular-nums text-slate-500">{fmtZ(e.ts)}</td>
                    <td className="py-1 pr-3 text-slate-800">{e.actor}</td>
                    <td className="py-1 pr-3 text-slate-700">{e.action.replace(/[._]/g, " ")}</td>
                    <td className="py-1 pr-3 text-slate-600">{e.ref}</td>
                    <td className="py-1 text-slate-500">{e.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
