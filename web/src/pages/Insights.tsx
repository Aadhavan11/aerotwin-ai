import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { del, get, post } from "../api/client";
import type { InsightsDataQuality, InsightsDisruptions, InsightsReliability } from "../api/insights";
import type { FleetOverview } from "../api/types";
import { Btn, Card, Chip, Loading } from "../components/ui";
import { weibullCurve } from "../lib/insights";
import { useRole } from "../lib/role";
import { ApiError } from "../api/client";

/** Each section loads independently and ignores responses after cleanup. */
function useAvailable<T>(path: string, revision = 0) {
  const [state, setState] = useState<{ data?: T; loading: boolean; error: boolean }>({ loading: true, error: false });
  useEffect(() => {
    let cancelled = false;
    setState({ loading: true, error: false });
    const load = async () => {
      try {
        const { data } = await get<T>(path);
        if (!cancelled) setState({ data, loading: false, error: false });
      } catch {
        if (!cancelled) setState({ loading: false, error: true });
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [path, revision]);
  return state;
}

function Simulated({ simulated }: { simulated?: boolean }) {
  return simulated ? <span className="sim-tag">SIMULATED</span> : null;
}

export default function Insights() {
  const { role } = useRole();
  const canEdit = role.id === "ops" || role.id === "admin";
  const quality = useAvailable<InsightsDataQuality>("/insights/data-quality");
  const reliability = useAvailable<InsightsReliability>("/insights/reliability");
  const [revision, setRevision] = useState(0);
  const disruptions = useAvailable<InsightsDisruptions>("/insights/disruptions", revision);
  const fleet = useAvailable<FleetOverview>("/fleet");
  const [station, setStation] = useState("");
  const [start, setStart] = useState("");
  const [duration, setDuration] = useState("1");
  const [severity, setSeverity] = useState("1");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const items = (quality.data?.items ?? []).filter((item) => item.status !== "ok");
  const kinds = reliability.data?.kinds ?? [];
  const events = disruptions.data?.events ?? [];
  const legs = disruptions.data?.delayed_legs ?? [];
  const stations = fleet.data?.stations ?? [];

  const mutate = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setMessage("");
    try {
      await action();
      setRevision((value) => value + 1);
    } catch (e) {
      setMessage(e instanceof ApiError && e.status === 403
        ? "Your role may not change weather events. Ask operations control."
        : "Weather changes are not available. Please try again.");
    } finally {
      setBusy(false);
    }
  };
  const addWeather = async () => {
    const date = new Date(start);
    const duration_h = Number(duration);
    const level = Number(severity);
    if (!stations.some((s) => s.iata === station) || !start || !Number.isFinite(date.getTime()) ||
      !duration.trim() || !Number.isFinite(duration_h) || duration_h < 0 ||
      !severity.trim() || !Number.isFinite(level) || level < 1 || level > 5) {
      setMessage("Choose a station, valid start time, duration of at least 0 hours and severity from 1 to 5.");
      return;
    }
    await mutate(() => post<unknown>("/insights/weather", {
      station, start: date.toISOString(), duration_h, severity: level,
    }));
  };

  return (
    <div className="space-y-5">
      <div className="page-heading"><div><h1>Insights</h1><p>Data quality, component reliability and weather disruptions.</p></div></div>
      <Card title="Data quality" right={<Simulated simulated={quality.data?.simulated} />}>
        {quality.loading ? <Loading what="data quality" /> : quality.error || !quality.data ? (
          <p className="muted">Data quality is not available.</p>
        ) : (
          <>
            <div className="flex flex-wrap gap-2">
              <Chip>ok {quality.data.counts?.ok ?? 0}</Chip>
              <Chip tone="amber">degraded {quality.data.counts?.degraded ?? 0}</Chip>
              <Chip tone="amber">bad {quality.data.counts?.bad ?? 0}</Chip>
            </div>
            {items.length === 0 ? <p className="muted">No non-ok data quality items.</p> : (
              <div className="overflow-auto"><table className="data-table">
                <thead><tr><th>Tail</th><th>component_id</th><th>Kind</th><th>Status</th><th>Reasons</th></tr></thead>
                <tbody>{items.map((item, index) => (
                  <tr key={`${item.tail}-${item.component_id}-${index}`}>
                    <td><Link to={`/aircraft/${item.tail}`}>{item.tail}</Link></td>
                    <td>{item.component_id}</td><td>{item.kind}</td><td>{item.status}</td><td>{(item.reasons ?? []).join(", ")}</td>
                  </tr>
                ))}</tbody>
              </table></div>
            )}
          </>
        )}
      </Card>
      <Card title="Reliability" right={<Simulated simulated={reliability.data?.simulated} />}>
        {reliability.loading ? <Loading what="reliability" /> : reliability.error || !reliability.data ? (
          <p className="muted">Reliability is not available.</p>
        ) : kinds.length === 0 ? <p className="muted">No reliability kinds available.</p> : (
          <>
            <div className="overflow-auto"><table className="data-table">
              <thead><tr><th>Kind</th><th>Components</th><th>Samples</th><th>beta</th><th>eta_cycles</th><th>mtbur_cycles</th><th>b10_cycles</th><th>Basis</th></tr></thead>
              <tbody>{kinds.map((kind, index) => (
                <tr key={`${kind.kind}-${index}`}><td>{kind.kind}</td><td>{kind.components}</td><td>{kind.samples}</td><td>{kind.beta}</td><td>{kind.eta_cycles}</td><td>{kind.mtbur_cycles}</td><td>{kind.b10_cycles}</td><td>{kind.basis}</td></tr>
              ))}</tbody>
            </table></div>
            <div className="flex flex-wrap gap-4">
              {kinds.map((kind, index) => {
                const curve = weibullCurve(kind.eta_cycles, kind.beta, kind.b10_cycles);
                if (!curve) return null;
                return (
                  <figure key={`${kind.kind}-${index}`} className="weibull" style={{ margin: 0 }}>
                    <figcaption>{kind.kind} · Weibull CDF</figcaption>
                    <svg width={240} height={140} viewBox="0 0 240 140" role="img" aria-label={`${kind.kind} Weibull cumulative failure probability`}>
                      <title>{kind.kind} Weibull CDF</title>
                      <path d="M 30 15 L 30 110 L 230 110" fill="none" stroke="currentColor" strokeOpacity={0.6} />
                      <path d={curve.path} fill="none" stroke="var(--warn)" strokeWidth={2} />
                      <text x={10} y={20} fontSize={10}>1</text><text x={10} y={113} fontSize={10}>0</text>
                      <text x={30} y={128} fontSize={10}>0</text><text x={230} y={128} fontSize={10} textAnchor="end">{curve.extent.toFixed(0)} cycles</text>
                    </svg>
                  </figure>
                );
              })}
            </div>
          </>
        )}
      </Card>
      <Card title="Disruptions" right={<Simulated simulated={disruptions.data?.simulated} />}>
        {disruptions.loading ? <Loading what="disruptions" /> : disruptions.error || !disruptions.data ? (
          <p className="muted">Disruptions are not available.</p>
        ) : (
          <>
            <h3 className="insights-h3">Weather events</h3>
            {events.length === 0 ? <p className="muted">No weather events.</p> : (
              <div className="overflow-auto"><table className="data-table">
                <thead><tr><th>id</th><th>Station</th><th>Start</th><th>duration_h</th><th>Severity</th><th>Action</th></tr></thead>
                <tbody>{events.map((event) => (
                  <tr key={event.id}><td>{event.id}</td><td>{event.station}</td><td>{event.start}</td><td>{event.duration_h}</td><td>{event.severity}</td>
                    <td>{canEdit ? <Btn disabled={busy} onClick={() => { void mutate(() => del<unknown>(`/insights/weather/${encodeURIComponent(event.id)}`)); }}>Delete</Btn> : <span className="muted">-</span>}</td>
                  </tr>
                ))}</tbody>
              </table></div>
            )}
            <h3 className="insights-h3">Delayed legs</h3>
            {legs.length === 0 ? <p className="muted">No delayed legs.</p> : (
              <div className="overflow-auto"><table className="data-table">
                <thead><tr><th>leg_id</th><th>Tail</th><th>sched_dep</th><th>est_dep</th><th>sched_arr</th><th>est_arr</th><th>delay_min</th><th>Cause</th></tr></thead>
                <tbody>{legs.map((leg) => (
                  <tr key={leg.leg_id}><td>{leg.leg_id}</td><td><Link to={`/aircraft/${leg.tail}`}>{leg.tail}</Link></td><td>{leg.sched_dep}</td><td>{leg.est_dep}</td><td>{leg.sched_arr}</td><td>{leg.est_arr}</td><td>{leg.delay_min}</td><td>{leg.cause}</td></tr>
                ))}</tbody>
              </table></div>
            )}
          </>
        )}
        <h3 className="insights-h3">Add weather event</h3>
        {!canEdit ? <p className="muted">Only operations control and administrators can add or remove weather events.</p> : fleet.loading ? <Loading what="fleet stations" /> : fleet.error || !fleet.data ? (
          <p className="muted">Fleet stations are not available.</p>
        ) : stations.length === 0 ? <p className="muted">No fleet stations available.</p> : (
          <form className="insights-form flex flex-wrap items-end gap-2" onSubmit={(event) => { event.preventDefault(); void addWeather(); }}>
            <label>Station<select className="select" required value={station} onChange={(event) => setStation(event.target.value)}>
              <option value="">Choose station</option>
              {stations.map((s) => <option key={s.iata} value={s.iata}>{s.iata}</option>)}
            </select></label>
            <label>Start<input className="search" type="datetime-local" required value={start} onChange={(event) => setStart(event.target.value)} /></label>
            <label>Duration (h)<input className="search" type="number" min={0} step={0.1} required value={duration} onChange={(event) => setDuration(event.target.value)} /></label>
            <label>Severity<input className="search" type="number" min={1} max={5} required value={severity} onChange={(event) => setSeverity(event.target.value)} /></label>
            <button className="app-agents" type="submit" disabled={busy}>Add weather</button>
          </form>
        )}
        {message && <p className="muted" role="status">{message}</p>}
      </Card>
    </div>
  );
}
