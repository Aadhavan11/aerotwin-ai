import { useEffect, useMemo, useRef, useState } from "react";
import { exportRun, getRun, getRuns, resumeRun, runExportUrl, startRun, type SimulationRun } from "../api/runs";
import { Btn, Card, Chip, Loading } from "../components/ui";
import { metric, runActive, runProgress, runResumable, validRunInput } from "../lib/operations";
import { useRole } from "../lib/role";
import { unavailableMessage, useAvailable } from "../lib/useAvailable";

const activeRuns = (runs: SimulationRun[]): boolean => runs.some(runActive);

function RunStatus({ run }: { run: SimulationRun }) {
  return <Chip tone={run.status === "failed" || run.status === "interrupted" ? "amber" : runActive(run) ? "blue" : "slate"}>{run.status ?? "—"}</Chip>;
}

function Progress({ run }: { run: SimulationRun }) {
  const fraction = runProgress(run);
  return <div>
    <div className="distribution-bar" style={{ background: "var(--surface-2)" }} role="progressbar" aria-label={`Run ${run.id} progress`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={fraction === null ? undefined : Math.round(fraction * 100)} aria-valuetext={`${metric(run.cycles_done)} of ${metric(run.total_cycles)} cycles`}>
      <span style={{ width: `${(fraction ?? 0) * 100}%`, background: "var(--accent)" }} />
    </div>
    <span className="muted">{metric(run.cycles_done)} / {metric(run.total_cycles)} cycles · {fraction === null ? "—" : `${metric(fraction * 100, 1)}%`}</span>
  </div>;
}

function eta(run: SimulationRun): string {
  return run.eta_seconds == null || !Number.isFinite(run.eta_seconds) ? "—" : `${metric(run.eta_seconds, 1)} s`;
}

export default function Runs() {
  const { role } = useRole();
  const canEdit = role.id === "ops" || role.id === "admin";
  const [revision, setRevision] = useState(0);
  const runs = useAvailable(getRuns, revision, activeRuns);
  const [selected, setSelected] = useState("");
  const loadSelected = useMemo(() => selected ? () => getRun(selected) : null, [selected]);
  const detail = useAvailable(loadSelected, revision, runActive);
  const [years, setYears] = useState("1");
  const [seed, setSeed] = useState("1234");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const downloads = useRef<Map<string, { url: string; filename: string }>>(new Map());
  const alive = useRef(true);
  const list = runs.data ?? [];

  useEffect(() => {
    if (!selected && list.length) setSelected(list[0].id);
  }, [runs.data, selected]);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      for (const download of downloads.current.values()) URL.revokeObjectURL(download.url);
      downloads.current.clear();
    };
  }, []);

  const mutate = async (action: () => Promise<SimulationRun>) => {
    setBusy(true);
    setMessage("");
    try {
      const run = await action();
      if (!run?.id) throw new Error("Missing run ID");
      if (alive.current) {
        setSelected(run.id);
        setRevision((value) => value + 1);
      }
    } catch (error) {
      if (alive.current) setMessage(unavailableMessage(error));
    } finally { if (alive.current) setBusy(false); }
  };
  const create = async () => {
    if (!validRunInput(years, seed)) {
      setMessage("Enter years greater than 0 and at most 20, and a whole-number seed.");
      return;
    }
    await mutate(() => startRun(Number(years), Number(seed)));
  };
  const download = async (id: string) => {
    setBusy(true);
    setMessage("");
    try {
      let file = downloads.current.get(id);
      if (!file) {
        const result = await exportRun(id);
        if (!alive.current) return;
        file = { url: URL.createObjectURL(result.blob), filename: result.filename };
        downloads.current.set(id, file);
      }
      const anchor = document.createElement("a");
      anchor.href = file.url;
      anchor.download = file.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    } catch (error) {
      if (alive.current) setMessage(unavailableMessage(error));
    } finally { if (alive.current) setBusy(false); }
  };
  const actions = (run: SimulationRun) => canEdit ? <div className="flex flex-wrap items-center gap-2">
    {runResumable(run) && <Btn disabled={busy} onClick={() => { void mutate(() => resumeRun(run.id)); }}>Resume</Btn>}
    {run.status === "done" && <a href={runExportUrl(run.id)} download aria-disabled={busy} onClick={(event) => { event.preventDefault(); if (!busy) void download(run.id); }}>Export</a>}
  </div> : <span className="muted">Not available for your role.</span>;

  return (
    <div className="space-y-5">
      <div className="page-heading"><div><h1>Simulation runs</h1><p>Start a backfill, track its progress and export simulated telemetry.</p></div><span className="sim-tag">SIMULATED</span></div>
      <Card title="Start a run">
        {!canEdit ? <p className="muted">Not available for your role.</p> : <form className="insights-form flex flex-wrap items-end gap-2" onSubmit={(event) => { event.preventDefault(); if (!busy) void create(); }}>
          <label>Years<input className="search" type="number" min={0.001} max={20} step="any" required value={years} onChange={(event) => setYears(event.target.value)} /></label>
          <label>Seed<input className="search" type="number" step={1} required value={seed} onChange={(event) => setSeed(event.target.value)} /></label>
          <button className="app-agents" type="submit" disabled={busy}>{busy ? "Working…" : "Start run"}</button>
        </form>}
        {message && <p className="muted" role="status">{message}</p>}
      </Card>
      <Card title="Runs" right={<Btn disabled={busy} onClick={() => setRevision((value) => value + 1)}>Refresh</Btn>}>
        {runs.loading ? <Loading what="runs" /> : runs.error ? <p className="muted" role="status">{runs.error}</p> : !list.length ? <p className="muted">No simulation runs.</p> : <div className="overflow-auto"><table className="data-table">
          <thead><tr><th>Run</th><th>Status</th><th>Progress</th><th>ETA</th><th>Actions</th></tr></thead>
          <tbody>{list.map((run) => <tr key={run.id}>
            <td><Btn disabled={busy} onClick={() => setSelected(run.id)} title="Select run">{run.id}</Btn></td><td><RunStatus run={run} /></td>
            <td><Progress run={run} /></td><td>{eta(run)}</td><td>{actions(run)}</td>
          </tr>)}</tbody>
        </table></div>}
      </Card>
      <Card title="Selected run" right={<span className="sim-tag">SIMULATED</span>}>
        {!selected ? <p className="muted">Select a run to view its progress.</p> : detail.loading ? <Loading what="run status" /> : detail.error || !detail.data ? <p className="muted" role="status">{detail.error ?? "Run is not available."}</p> : <>
          <div className="flex flex-wrap items-center gap-2"><b>{detail.data.id}</b><RunStatus run={detail.data} /></div>
          <Progress run={detail.data} />
          <p className="muted">ETA {eta(detail.data)} · Years {metric(detail.data.years, 2)} · Seed {metric(detail.data.seed)} · Rows {metric(detail.data.rows)}</p>
          {detail.data.error && <p className="muted" role="status">{detail.data.error}</p>}
          {actions(detail.data)}
          <p className="muted">{detail.data.provenance ?? "—"}</p>
        </>}
      </Card>
    </div>
  );
}
