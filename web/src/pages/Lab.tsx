import { useState } from "react";
import { post, usePoll } from "../api/client";
import type {
  Comparison,
  HealthInfo,
  InjectResult,
  PlanMetrics,
  ReplanResult,
  ScenarioEvent,
  Station,
} from "../api/types";
import { Btn, Card, Chip, ErrorBox } from "../components/ui";
import { fmtH, fmtUsd, fmtZ } from "../lib/format";

type Row = {
  name: string;
  feasible: boolean;
  metrics: PlanMetrics;
  notes?: string[];
};
type Result =
  | { kind: "inject"; title: string; res: InjectResult }
  | { kind: "replan"; title: string; res: ReplanResult }
  | { kind: "reset"; title: string }
  | null;

export default function Lab() {
  const health = usePoll<HealthInfo>("/health", 5000);
  const stations = usePoll<Station[]>("/stations", 5000);
  const now = health.data?.sim_now ?? "2026-10-12T12:00:00Z";
  const engineTech = stations.data
    ?.find((s) => s.iata === "DFW")
    ?.technicians.find(
      (t) =>
        t.available &&
        t.skills.includes("engine") &&
        Date.parse(t.shift_end) > Date.parse(now),
    );
  const [busy, setBusy] = useState("");
  const [err, setErr] = useState("");
  const [result, setResult] = useState<Result>(null);
  const [cmp, setCmp] = useState<Row[] | null>(null);

  const run = async (id: string, f: () => Promise<void>) => {
    setBusy(id);
    setErr("");
    try {
      await f();
    } catch (e) {
      setErr(String((e as Error).message));
    } finally {
      await health.reload();
      await stations.reload();
      setBusy("");
    }
  };
  const ev = (
    e: Partial<ScenarioEvent> & { type: ScenarioEvent["type"] },
  ): ScenarioEvent => ({ at: now, ...e }) as ScenarioEvent;
  const actions: {
    id: string;
    label: string;
    hint: string;
    go: () => Promise<void>;
  }[] = [
    {
      id: "step",
      label: "Advance simulation 24 h",
      hint: "Replay completed flights and update component health.",
      go: async () => {
        await post("/sim/step", { hours: 24 });
        setResult(null);
      },
    },
    {
      id: "anomaly",
      label: "Inject engine anomaly (SIM-017)",
      hint: "Engine 2 develops an abnormal temperature trend.",
      go: async () => {
        const { data } = await post<InjectResult>(
          "/scenario/inject",
          ev({
            type: "anomaly",
            tail: "SIM-017",
            component_id: "SIM-017/engine_2",
            severity: 0.9,
          }),
        );
        setResult({ kind: "inject", title: "Anomaly injected", res: data });
      },
    },
    {
      id: "delay",
      label: "Delay borescope kit at DFW by 36 h",
      hint: "Recompute the plan after a 36-hour delivery delay.",
      go: async () => {
        const { data } = await post<ReplanResult>("/plan/replan", {
          event: ev({
            type: "part_delay",
            station: "DFW",
            part: "BORESCOPE-KIT",
            delay_hours: 36,
            note: "supplier delay",
          }),
        });
        setResult({
          kind: "replan",
          title: "Kit delayed 36 h at DFW",
          res: data,
        });
      },
    },
    {
      id: "closed",
      label: "Close the ATL hangar",
      hint: "ATL has 0 hangar slots from now on.",
      go: async () => {
        const { data } = await post<ReplanResult>("/plan/replan", {
          event: ev({ type: "facility_closed", station: "ATL" }),
        });
        setResult({ kind: "replan", title: "ATL hangar closed", res: data });
      },
    },
    {
      id: "tech",
      label: `Technician unavailable (${engineTech?.id ?? "DFW"})`,
      hint: "The engine technician at DFW calls out.",
      go: async () => {
        if (!engineTech)
          throw new Error("No available DFW engine technician found");
        const { data } = await post<ReplanResult>("/plan/replan", {
          event: ev({
            type: "tech_unavailable",
            station: "DFW",
            technician_id: engineTech?.id,
          }),
        });
        setResult({
          kind: "replan",
          title: `${engineTech?.id ?? "DFW"} unavailable`,
          res: data,
        });
      },
    },
    {
      id: "reset",
      label: "Reset to the golden scenario",
      hint: "Back to the starting state.",
      go: async () => {
        await post("/scenario/reset", { name: "golden" });
        setResult({ kind: "reset", title: "Reset" });
        setCmp(null);
      },
    },
  ];
  const runCompare = () =>
    run("compare", async () => {
      const { data } = await post<Comparison>("/whatif/compare", {
        scenarios: [
          { name: "Current plan", events: [] },
          {
            name: "Kit delayed 36 h (DFW)",
            events: [
              ev({
                type: "part_delay",
                station: "DFW",
                part: "BORESCOPE-KIT",
                delay_hours: 36,
              }),
            ],
          },
          {
            name: "ATL hangar closed",
            events: [ev({ type: "facility_closed", station: "ATL" })],
          },
          {
            name: `${engineTech?.id ?? "DFW"} unavailable`,
            events: [
              ev({
                type: "tech_unavailable",
                station: "DFW",
                technician_id: engineTech?.id,
              }),
            ],
          },
          {
            name: "Force service at ATL",
            events: [],
            constraints: { force_station: "ATL" },
          },
        ],
      });
      setCmp(data.rows as unknown as Row[]);
    });

  return (
    <div className="space-y-4">
      <div className="page-head">
        <div>
          <h1>Scenarios</h1>
          <p>Inject a fault or take a resource away, then compare the plan before and after. Every change here is simulated.</p>
        </div>
      </div>
      <ErrorBox error={err} />
      <Card
        title="Scenario controls"
        right={
          <span className="text-xs text-slate-500">sim time {fmtZ(now)}</span>
        }
      >
        <div className="grid gap-2 md:grid-cols-2 lg:grid-cols-3">
          {actions.map((a) => (
            <div key={a.id} className="rounded-lg border border-slate-200 p-3">
              <Btn
                tone={a.id === "reset" ? "default" : "primary"}
                disabled={!!busy}
                onClick={() => run(a.id, a.go)}
                className="w-full"
              >
                {busy === a.id ? "Working…" : a.label}
              </Btn>
              <p className="mt-1.5 text-xs text-slate-500">{a.hint}</p>
            </div>
          ))}
        </div>
      </Card>

      {result && (
        <Card title={`Result: ${result.title}`}>
          {result.kind === "inject" && (
            <ul className="list-disc space-y-1 pl-5 text-sm">
              {result.res.effects.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
              <li className="text-slate-500">
                event {result.res.event_id}. Open the Copilot drawer and run the
                agents to plan the maintenance.
              </li>
            </ul>
          )}
          {result.kind === "reset" && (
            <p className="text-sm">Scenario reset.</p>
          )}
          {result.kind === "replan" && <ReplanView r={result.res} />}
        </Card>
      )}

      <Card
        title="Scenario comparison"
        right={
          <Btn onClick={runCompare} disabled={!!busy}>
            {busy === "compare" ? "Comparing…" : "Run comparison"}
          </Btn>
        }
      >
        {!cmp ? (
          <p className="text-sm text-slate-500">
            Compares the current plan with what happens under each disruption,
            on the same snapshot.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-xs text-slate-500">
                <tr>
                  <th className="py-1.5">Scenario</th>
                  <th>Feasible</th>
                  <th className="text-right">Downtime</th>
                  <th className="text-right">Flights</th>
                  <th className="text-right">Logistics</th>
                  <th className="text-right">Changes</th>
                  <th className="pl-4">Notes</th>
                </tr>
              </thead>
              <tbody>
                {cmp.map((r) => (
                  <tr key={r.name} className="border-t border-slate-100">
                    <td className="py-1.5 font-medium">{r.name}</td>
                    <td>
                      {r.feasible ? (
                        "yes"
                      ) : (
                        <span className="text-red-600">no</span>
                      )}
                    </td>
                    <td className="text-right tabular-nums">
                      {fmtH(r.metrics.downtime_h)}
                    </td>
                    <td className="text-right tabular-nums">
                      {r.metrics.flights_affected}
                    </td>
                    <td className="text-right tabular-nums">
                      {fmtUsd(r.metrics.logistics_cost_usd)}
                    </td>
                    <td className="text-right tabular-nums">
                      {r.metrics.schedule_changes}
                    </td>
                    <td className="pl-4 text-xs text-slate-600">
                      {(r.notes ?? []).join("; ")}
                    </td>
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

function ReplanView({ r }: { r: ReplanResult }) {
  const rows: [string, (m: PlanMetrics) => string][] = [
    ["Downtime", (m) => fmtH(m.downtime_h)],
    ["Flights affected", (m) => String(m.flights_affected)],
    ["Logistics", (m) => fmtUsd(m.logistics_cost_usd)],
    ["Schedule changes", (m) => String(m.schedule_changes)],
  ];
  return (
    <div className="space-y-3 text-sm">
      {!r.after.feasible && (
        <div className="rounded-lg border border-red-300 bg-red-50 p-3 text-red-900">
          <b>No feasible plan after this event.</b>{" "}
          {r.after.infeasible_reasons.join(" · ")}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Chip tone="blue">{r.event.type}</Chip>
        <span className="text-slate-600">
          replanned in <b>{r.solve_seconds.toFixed(2)} s</b> · plan{" "}
          {r.before.id} → {r.after.id}
        </span>
      </div>
      <table className="w-full max-w-lg text-left">
        <thead className="text-xs text-slate-500">
          <tr>
            <th className="py-1">Metric</th>
            <th className="text-right">Before</th>
            <th className="text-right">After</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([n, f]) => (
            <tr key={n} className="border-t border-slate-100">
              <td className="py-1">{n}</td>
              <td className="text-right tabular-nums">{f(r.before.metrics)}</td>
              <td className="text-right tabular-nums font-medium">
                {f(r.after.metrics)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div>
        <div className="mb-1 text-xs font-semibold text-slate-500">
          What changed
        </div>
        {r.diff.moved.length + r.diff.added.length + r.diff.dropped.length ===
          0 && (
          <p className="text-slate-500">
            Nothing moved: the existing plan still holds.
          </p>
        )}
        <ul className="space-y-2">
          {r.diff.moved.map((m, i) => {
            const mv = m as Record<string, any>;
            return (
              <li key={i} className="rounded-lg bg-slate-50 p-2">
                <b>{String(mv.task_id)}</b>: {mv.from?.station}{" "}
                {fmtZ(mv.from?.start)} →{" "}
                <b>
                  {mv.to?.station} {fmtZ(mv.to?.start)}
                </b>
                <div className="text-xs text-slate-600">
                  {String(mv.why ?? "")}
                </div>
              </li>
            );
          })}
          {r.diff.added.map((t) => (
            <li key={t} className="text-emerald-700">
              added: {t}
            </li>
          ))}
          {r.diff.dropped.map((t) => (
            <li key={t} className="text-red-700">
              dropped: {t}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
