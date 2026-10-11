import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { post, usePoll } from "../api/client";
import type { FleetOverview, Leg, MaintItem, MaintSuggestion, MaintView, QueueRow, Station, WorkOrder } from "../api/types";
import { Btn, Card, ErrorBox, Loading, StatusPill } from "../components/ui";
import { profileFor } from "../components/aircraftModels";
import { fmtUsd, fmtZ, STATUS_ORDER } from "../lib/format";

const HOUR = 3_600_000;
const SKILL: Record<string, string> = { engine: "engine", gear: "landing gear and brakes", hydraulics: "hydraulics", avionics: "avionics" };

/** "in 14 h", "in 2.3 days", "3 h ago". */
function rel(iso: string | null | undefined, now: string | undefined): string {
  if (!iso || !now) return "-";
  const h = (Date.parse(iso) - Date.parse(now)) / HOUR;
  const a = Math.abs(h);
  const text = a < 1 ? `${Math.round(a * 60)} min` : a < 48 ? `${a.toFixed(a < 10 ? 1 : 0)} h` : `${(a / 24).toFixed(1)} days`;
  return h >= 0 ? `in ${text}` : `${text} ago`;
}
const hours = (h: number | null | undefined) => (h == null ? "-" : `${Math.round(h * 10) / 10} h`);

function userName(): string {
  try {
    return localStorage.getItem("aerotwin.user") ?? "";
  } catch {
    return "";
  }
}

/** One decision block for an item's work order. A named person approves; the backend refuses anyone else. */
function OrderBlock({ item, now, user, setUser, onChanged }: { item: MaintItem; now?: string; user: string; setUser: (v: string) => void; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const w = item.work_order;
  const decide = async (decision: "approve" | "reject") => {
    if (!w) return;
    setBusy(true);
    setErr("");
    try {
      await post(`/workorders/${w.id}/decision`, { decision, user: user.trim(), note: "" });
      onChanged();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  if (!w)
    return (
      <div className="order">
        <div className="order-head">
          <strong>Work order</strong>
          <span className="muted">none drafted yet</span>
        </div>
        <p className="muted" style={{ margin: "6px 0 10px", fontSize: 13 }}>
          The agents draft work orders from the current plan; a named person approves each one.
        </p>
        <Btn tone="primary" onClick={() => window.dispatchEvent(new CustomEvent("aerotwin:copilot"))}>
          Open the copilot
        </Btn>
      </div>
    );
  return (
    <div className="order">
      <div className="order-head">
        <strong className="mono">{w.id}</strong>
        <span className={`status s-${w.status === "approved" ? "ok" : w.status === "rejected" ? "alert" : "watch"}`}>
          {w.status === "pending_approval" ? "Waiting for approval" : w.status === "approved" ? `Approved by ${w.decided_by ?? "a person"}` : `Rejected by ${w.decided_by ?? "a person"}`}
        </span>
      </div>
      <p className="muted" style={{ margin: "6px 0 0", fontSize: 13, lineHeight: 1.5 }}>
        {w.description}
      </p>
      <p className="muted mono" style={{ margin: "6px 0 0", fontSize: 12 }}>
        Slot {w.station} {fmtZ(w.start)} to {fmtZ(w.end)} · from plan {w.plan_id}
        {w.decided_at ? ` · decided ${rel(w.decided_at, now)}` : ""}
      </p>
      {item.approved_slot_moved && item.assignment && (
        <p className="reasons" style={{ margin: "10px 0 0" }}>
          The plan now schedules this task at {item.assignment.station} on {fmtZ(item.assignment.start)}, not the approved slot. Review it before work starts.
        </p>
      )}
      {w.status === "pending_approval" && (
        <div className="order-actions">
          <label className="muted" style={{ fontSize: 12 }}>
            Approving as{" "}
            <input className="name-input" value={user} placeholder="your name" onChange={(e) => setUser(e.target.value)} aria-label="Your name for the audit log" />
          </label>
          <Btn tone="primary" disabled={busy || !user.trim()} onClick={() => decide("approve")} title={user.trim() ? "" : "Enter your name first"}>
            Approve
          </Btn>
          <Btn disabled={busy || !user.trim()} onClick={() => decide("reject")}>
            Reject
          </Btn>
        </div>
      )}
      {err && <p className="reasons" style={{ margin: "10px 0 0" }}>{err}</p>}
    </div>
  );
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/** The job the page is about: where, when, who, which part, how much margin, and why this slot. */
function NextUp({ item, now, user, setUser, onChanged, first }: { item: MaintItem; now?: string; user: string; setUser: (v: string) => void; onChanged: () => void; first: boolean }) {
  const a = item.assignment;
  const lg = item.logistics;
  const part = lg?.part;
  const slack = item.exposure?.slack_to_deadline_h;
  const urgent = item.source === "rul";
  return (
    <Card className="hero">
      <div className="comp-head">
        <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
          <div>
          <div className="eyebrow-line">{first ? "Next planned maintenance" : "Selected task"}</div>
          <h2 style={{ margin: "2px 0 0", fontSize: 22, letterSpacing: "-0.02em" }}>{item.title}</h2>
          <div className="muted" style={{ marginTop: 3 }}>
            {item.component} · {urgent ? "raised by the health model" : "routine check"}
            {item.card?.ata ? ` · ${item.card.ata}` : ""}
          </div>
          </div>
        </div>
        {a ? <span className="when-chip">{rel(a.start, now)}</span> : <span className="when-chip warn">not scheduled</span>}
      </div>

      {a ? (
        <dl className="facts">
          <Fact label="Where">
            <span className="mono">{a.station}</span>
            <small>
              {lg ? `${lg.hangar_slots} hangar bay${lg.hangar_slots === 1 ? "" : "s"}` : ""}
            </small>
          </Fact>
          <Fact label="When (UTC)">
            <span className="mono">{fmtZ(a.start)}</span>
            <small>to {fmtZ(a.end)} · {hours(item.duration_h)} job</small>
          </Fact>
          <Fact label="Ground window">
            <span className="mono">{hours(a.window_hours)}</span>
            <small>{a.extension_h > 0 ? `${hours(a.extension_h)} past the window` : "no flight delayed"}</small>
          </Fact>
          <Fact label="Technician">
            <span className="mono">{a.technician_id ?? "-"}</span>
            <small>
              {SKILL[item.skill] ?? item.skill}
              {lg ? `, ${lg.technicians_covering} qualified on shift` : ""}
            </small>
          </Fact>
          <Fact label="Part">
            {part ? (
              <>
                <span className="mono">{part.name}</span>
                <small>
                  {a.part_source
                    ? `shipped from ${a.part_source}, ${hours(part.ship_hours)}, ${fmtUsd(part.ship_cost_usd)}`
                    : `${part.on_hand} on hand at ${a.station}${part.eta_hours > 0 ? `, available in ${hours(part.eta_hours)}` : ""}`}
                </small>
              </>
            ) : (
              <>
                <span>None needed</span>
                <small>no spare consumed</small>
              </>
            )}
          </Fact>
          <Fact label="Deadline">
            <span className="mono">{fmtZ(item.deadline)}</span>
            <small>{slack == null ? "scheduled routine check" : `${hours(slack)} of margin after the job`}</small>
          </Fact>
          {item.exposure?.flights_before_fix != null && (
            <Fact label="Flights before the fix">
              <span className="mono">{item.exposure.flights_before_fix}</span>
              <small>flown with the condition open</small>
            </Fact>
          )}
          {item.baseline && (
            <Fact label="Home-base-only plan">
              <span className="mono">{item.baseline.station}</span>
              <small>{fmtZ(item.baseline.start)}</small>
            </Fact>
          )}
        </dl>
      ) : (
        <p className="reasons" style={{ margin: "14px 0 0" }}>
          The scheduler found no feasible slot before the deadline ({fmtZ(item.deadline)}). Open Planning to see which constraint blocks it.
        </p>
      )}

      <p className="muted" style={{ margin: "14px 0 0", fontSize: 13, lineHeight: 1.55 }}>
        <strong style={{ color: "var(--ink-2)", fontWeight: 600 }}>Why it is needed. </strong>
        {item.basis}
      </p>
      {a && (
        <p className="muted" style={{ margin: "8px 0 0", fontSize: 13, lineHeight: 1.55 }}>
          <strong style={{ color: "var(--ink-2)", fontWeight: 600 }}>Why this slot. </strong>
          {a.reason}
        </p>
      )}
      {item.card?.note && (
        <p className="muted" style={{ margin: "8px 0 0", fontSize: 12 }}>
          Task card {item.card.id}: {item.card.note}
        </p>
      )}
      <OrderBlock item={item} now={now} user={user} setUser={setUser} onChanged={onChanged} />
    </Card>
  );
}

/** Flights, ground windows and planned work on one time axis for the next five days. */
function Gantt({ now, legs, view, items, focus }: { now: string; legs: Leg[]; view: MaintView; items: MaintItem[]; focus?: string }) {
  const SPAN = 120;
  const t0 = Date.parse(now);
  const x = (iso: string) => Math.min(100, Math.max(0, ((Date.parse(iso) - t0) / HOUR / SPAN) * 100));
  const w = (a: string, b: string) => Math.max(0.4, x(b) - x(a));
  const inRange = (a: string, b: string) => Date.parse(b) > t0 && Date.parse(a) < t0 + SPAN * HOUR;
  const days: { at: number; label: string }[] = [];
  for (let h = 0; h <= SPAN; h += 6) {
    const d = new Date(t0 + h * HOUR);
    if (d.getUTCHours() % 12 === 0) days.push({ at: (h / SPAN) * 100, label: d.getUTCHours() === 0 ? `${d.toUTCString().slice(5, 11)}` : "12:00" });
  }
  const flights = legs.filter((l) => inRange(l.sched_dep, l.sched_arr)).sort((a, b) => Date.parse(a.sched_dep) - Date.parse(b.sched_dep));
  return (
    <Card title="Schedule: flights, ground time and planned work">
      <div className="gantt" role="img" aria-label="Timeline of flights, ground windows and planned maintenance for the next five days">
        <div className="gantt-axis">
          {days.map((d) => (
            <span key={d.at} style={{ left: `${d.at}%` }}>
              {d.label}
            </span>
          ))}
        </div>
        <div className="gantt-row">
          <span className="gantt-label">Flights</span>
          <div className="gantt-track">
            {flights.map((l) => (
              <span key={l.id} className="seg-flight" style={{ left: `${x(l.sched_dep)}%`, width: `${w(l.sched_dep, l.sched_arr)}%` }} title={`${l.dep} to ${l.arr}, ${fmtZ(l.sched_dep)}`}>
                {w(l.sched_dep, l.sched_arr) > 3.2 ? `${l.dep}-${l.arr}` : ""}
              </span>
            ))}
          </div>
        </div>
        <div className="gantt-row">
          <span className="gantt-label">On the ground</span>
          <div className="gantt-track">
            {view.ground_windows
              .filter((g) => inRange(g.start, g.end))
              .map((g) => (
                <span key={g.start} className="seg-ground" style={{ left: `${x(g.start)}%`, width: `${w(g.start, g.end)}%` }} title={`${g.station} ${g.hours} h, ${fmtZ(g.start)}`}>
                  {w(g.start, g.end) > 5 ? `${g.station} ${g.hours} h` : ""}
                </span>
              ))}
          </div>
        </div>
        <div className="gantt-row">
          <span className="gantt-label">Maintenance</span>
          <div className="gantt-track">
            {items
              .filter((i) => i.assignment && inRange(i.assignment.start, i.assignment.end))
              .map((i) => (
                <span
                  key={i.task_id}
                  className={`seg-work ${i.task_id === focus ? "focus" : ""}`}
                  style={{ left: `${x(i.assignment!.start)}%`, width: `${w(i.assignment!.start, i.assignment!.end)}%` }}
                  title={`${i.title} at ${i.assignment!.station}`}
                >
                  {w(i.assignment!.start, i.assignment!.end) > 5 ? i.assignment!.station : ""}
                </span>
              ))}
            {items
              .filter((i) => i.deadline && Date.parse(i.deadline) > t0 && Date.parse(i.deadline) < t0 + SPAN * HOUR)
              .map((i) => (
                <span key={`d-${i.task_id}`} className="seg-deadline" style={{ left: `${x(i.deadline!)}%` }} title={`Deadline ${fmtZ(i.deadline)}`} />
              ))}
          </div>
        </div>
      </div>
      <p className="muted" style={{ fontSize: 12, margin: "10px 0 0" }}>
        Next five days, UTC. Black: planned maintenance. Red line: hard deadline. Grey: time on the ground between flights.
      </p>
    </Card>
  );
}

function Suggestions({ list, now, onFocus }: { list: MaintSuggestion[]; now: string; onFocus: () => void }) {
  return (
    <Card title="Suggested actions">
      {list.length === 0 ? (
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          Nothing to suggest. Every monitored system has at least 100 flights of conservative life and health above 50%.
        </p>
      ) : (
        <ul className="suggest">
          {list.map((s) => (
            <li key={s.component_id}>
              <div className="comp-head">
                <strong>{s.action}</strong>
                <StatusPill status={s.status} />
              </div>
              <div className="muted" style={{ fontSize: 13, margin: "3px 0 6px" }}>
                {s.component} · health {Math.round(s.health * 100)}%
              </div>
              <div className="bar" style={{ marginBottom: 6 }}>
                <span style={{ width: `${Math.round(s.health * 100)}%` }} />
              </div>
              <div style={{ fontSize: 13, lineHeight: 1.5 }}>{s.text}</div>
              <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                Act before {fmtZ(s.act_before)} ({rel(s.act_before, now)})
                {s.duration_h ? ` · ${s.duration_h} h job` : ""}
                {s.skill ? ` · ${SKILL[s.skill] ?? s.skill} technician` : ""}
                {s.part ? ` · needs ${s.part}` : ""}
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="muted" style={{ fontSize: 12, margin: "10px 0 0" }}>
        Advice only. A suggestion becomes a task when the health model's conservative estimate runs low. <button className="link" onClick={onFocus}>Ask the copilot</button>
      </p>
    </Card>
  );
}

/** Parts and people at the station where the focused job is planned, and where else the part is held. */
function Resources({ item, stations, now }: { item?: MaintItem; stations: Station[]; now?: string }) {
  const a = item?.assignment;
  const here = stations.find((s) => s.iata === a?.station);
  const t0 = now ? Date.parse(now) : Date.now();
  const onShift = here ? here.technicians.filter((t) => t.available !== false && Date.parse(t.shift_start) <= t0 && t0 < Date.parse(t.shift_end)) : [];
  const bySkill = onShift.reduce<Record<string, number>>((m, t) => {
    t.skills.forEach((k) => (m[k] = (m[k] ?? 0) + 1));
    return m;
  }, {});
  const part = item?.part;
  const network = part
    ? stations
        .map((s) => ({ s, p: s.parts.find((p) => p.part === part) }))
        .filter((r) => r.p)
        .sort((x, y) => (y.p!.qty > 0 ? 1 : 0) - (x.p!.qty > 0 ? 1 : 0) || x.s.iata.localeCompare(y.s.iata))
    : [];
  return (
    <Card title={a ? `Resources at ${a.station}` : "Resources"}>
      {!a || !here ? (
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          Select a scheduled task to see the station, technicians and parts behind it.
        </p>
      ) : (
        <>
          <h3 className="sub">Technicians</h3>
          <ul className="plain-list">
            <li>
              <span>On shift now</span>
              <span className="mono">
                {onShift.length}
                <span className="muted"> {Object.entries(bySkill).map(([k, n]) => `${k} ${n}`).join(", ")}</span>
              </span>
            </li>
            {item?.logistics && (
              <>
                <li>
                  <span>Qualified for this job ({SKILL[item.skill] ?? item.skill})</span>
                  <span className="mono">{item.logistics.technicians_covering} cover the slot</span>
                </li>
                <li>
                  <span>Assigned</span>
                  <span className="mono">{item.logistics.technician_assigned ?? "-"}</span>
                </li>
                <li>
                  <span>Spare for this slot</span>
                  <span className="mono">{Math.max(item.logistics.technicians_covering - 1, 0)}</span>
                </li>
              </>
            )}
          </ul>
          <h3 className="sub">Parts held at {here.iata}</h3>
          <table className="data-table">
            <tbody>
              {here.parts.map((p) => (
                <tr key={p.part} style={{ cursor: "default" }} className={p.part === part ? "picked" : ""}>
                  <td className="mono">{p.part}</td>
                  <td className="mono" style={{ textAlign: "right" }}>{p.qty}</td>
                  <td className="muted" style={{ textAlign: "right" }}>
                    {p.eta_hours > 0 ? `+${p.eta_hours} h` : p.qty === 0 && p.ship_from ? `ships from ${p.ship_from}` : "on hand"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {part && network.length > 0 && (
        <>
          <h3 className="sub">Where {part} is held</h3>
          <table className="data-table">
            <thead>
              <tr>
                <th>Station</th>
                <th style={{ textAlign: "right" }}>On hand</th>
                <th>Resupply</th>
              </tr>
            </thead>
            <tbody>
              {network.map(({ s, p }) => (
                <tr key={s.iata} style={{ cursor: "default" }} className={s.iata === a?.station ? "picked" : ""}>
                  <td className="mono">{s.iata}</td>
                  <td className="mono" style={{ textAlign: "right" }}>{p!.qty}</td>
                  <td className="muted">
                    {p!.qty > 0 ? "stocked" : p!.ship_from ? `from ${p!.ship_from}, ${p!.ship_hours} h, ${fmtUsd(p!.ship_cost_usd)}` : "none"}
                    {p!.eta_hours > 0 ? ` (+${p!.eta_hours} h)` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Card>
  );
}

function History({ view }: { view: MaintView }) {
  return (
    <Card title="Maintenance history and decisions">
      {view.history.length === 0 ? (
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          Nothing recorded yet. Injected faults, plan changes, decisions and completed work appear here.
        </p>
      ) : (
        <ol className="timeline">
          {view.history.slice(0, 24).map((e, i) => (
            <li key={`${e.ts}-${e.ref}-${i}`}>
              <time>{fmtZ(e.ts)}</time>
              <div>
                <div className="kind">{e.kind.replaceAll("_", " ")}</div>
                <div>{e.summary}</div>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function Queue({ rows, now, tail, onPick }: { rows: QueueRow[]; now?: string; tail: string; onPick: (r: QueueRow) => void }) {
  return (
    <Card title="Fleet maintenance queue">
      {rows.length === 0 ? (
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>
          No open tasks anywhere in the fleet.
        </p>
      ) : (
        <div style={{ overflowX: "auto" }}>
          <table className="data-table">
            <thead>
              <tr>
                <th>Aircraft</th>
                <th>Task</th>
                <th>Slot</th>
                <th style={{ textAlign: "right" }}>Margin</th>
                <th>Order</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.task_id} className={r.tail === tail ? "picked" : ""} onClick={() => onPick(r)}>
                  <td className="mono" style={{ fontWeight: 500 }}>{r.tail}</td>
                  <td>
                    {r.title}
                    <div className="muted" style={{ fontSize: 12 }}>
                      {r.component} · {r.source === "rul" ? "condition" : "routine"}
                    </div>
                  </td>
                  <td className="mono" style={{ whiteSpace: "nowrap" }}>
                    {r.scheduled ? (
                      <>
                        {r.station}
                        <div className="muted" style={{ fontSize: 12 }}>{fmtZ(r.start)} ({rel(r.start, now)})</div>
                      </>
                    ) : (
                      <span className="muted">unscheduled</span>
                    )}
                  </td>
                  <td className="mono" style={{ textAlign: "right" }}>{r.slack_to_deadline_h == null ? "-" : hours(r.slack_to_deadline_h)}</td>
                  <td>
                    {r.work_order_status ? (
                      <span className={`status s-${r.work_order_status === "approved" ? "ok" : r.work_order_status === "rejected" ? "alert" : "watch"}`}>
                        {r.approved_slot_moved ? "Review" : r.work_order_status === "pending_approval" ? "Waiting" : r.work_order_status === "approved" ? "Approved" : "Rejected"}
                      </span>
                    ) : (
                      <span className="muted">none</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="muted" style={{ fontSize: 12, margin: "10px 0 0" }}>
        Sorted by hard deadline. Margin is the time between the end of the job and its deadline.
      </p>
    </Card>
  );
}

export default function Maintenance() {
  const { tail } = useParams();
  const navigate = useNavigate();
  const fleet = usePoll<FleetOverview>("/fleet", 5000);
  const queue = usePoll<{ now: string; plan_id: string; rows: QueueRow[] }>("/maintenance/queue", 5000);
  const view = usePoll<MaintView>(tail ? `/aircraft/${encodeURIComponent(tail)}/maintenance` : null, 4000);
  const stations = usePoll<Station[]>("/stations", 8000);
  const legs = usePoll<Leg[]>(tail ? `/legs?tail=${encodeURIComponent(tail)}` : null, 30000);
  const [focus, setFocus] = useState("");
  const [user, setUser] = useState(userName);
  useEffect(() => {
    try {
      localStorage.setItem("aerotwin.user", user);
    } catch {
      /* private mode: the name is kept for this page only */
    }
  }, [user]);
  useEffect(() => setFocus(""), [tail]);

  const aircraft = fleet.data?.aircraft ?? [];
  const sorted = useMemo(
    () => [...aircraft].sort((a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9) || a.tail.localeCompare(b.tail)),
    [aircraft],
  );
  const urgentTail = queue.data?.rows[0]?.tail ?? sorted[0]?.tail;
  if (!tail) {
    if (urgentTail) return <Navigate to={`/maintenance/${urgentTail}`} replace />;
    return queue.error || fleet.error ? <ErrorBox error={queue.error ?? fleet.error} /> : <Loading what="the maintenance queue" />;
  }
  const v = view.data;
  const items = v?.items ?? [];
  const item = items.find((i) => i.task_id === focus) ?? items[0];
  const now = v?.now ?? queue.data?.now;
  const summary = aircraft.find((a) => a.tail === tail);
  const profile = profileFor(summary?.type ?? "");
  const reload = () => {
    view.reload();
    queue.reload();
  };
  const pending = items.filter((i) => i.work_order?.status === "pending_approval").length;
  const approved = items.filter((i) => i.work_order?.status === "approved").length;
  const margin = items.map((i) => i.exposure?.slack_to_deadline_h).filter((m): m is number => m != null);
  const next = items.filter((i) => i.assignment).sort((a, b) => Date.parse(a.assignment!.start) - Date.parse(b.assignment!.start))[0];

  return (
    <div>
      <div className="page-head">
        <div>
          <h1>Maintenance</h1>
          <p>Planned work, parts and technicians, with the reasoning behind every slot.</p>
        </div>
        <div className="head-controls">
          <label className="muted" style={{ fontSize: 12 }}>
            Aircraft{" "}
            <select value={tail} onChange={(e) => navigate(`/maintenance/${e.target.value}`)} className="select" aria-label="Aircraft">
              {sorted.map((a) => (
                <option key={a.tail} value={a.tail}>
                  {a.tail} · {profileFor(a.type)?.key === "b738" ? "737-800" : "A320"} · {a.status === "ok" ? "OK" : a.status}
                </option>
              ))}
            </select>
          </label>
          <div className="seg" role="tablist" aria-label="Section">
            <Link to={`/aircraft/${tail}`} role="tab" aria-selected={false}>
              Aircraft
            </Link>
            <span role="tab" aria-selected={true} className="on">
              Maintenance
            </span>
          </div>
        </div>
      </div>
      <ErrorBox error={view.error ?? queue.error} />

      <dl className="figures">
        <div>
          <dt>Aircraft</dt>
          <dd style={{ fontSize: 20, paddingTop: 4 }}>{tail}</dd>
          <small className="muted">
            {profile?.label ?? summary?.type ?? ""} {summary ? <StatusPill status={summary.status} /> : null}
          </small>
        </div>
        <div>
          <dt>Open tasks</dt>
          <dd>{items.length}</dd>
        </div>
        <div>
          <dt>Next slot</dt>
          <dd style={{ fontSize: 20, paddingTop: 4 }}>{next?.assignment ? rel(next.assignment.start, now) : "none"}</dd>
          <small className="muted">{next?.assignment ? `${next.assignment.station}, ${fmtZ(next.assignment.start)}` : "nothing scheduled"}</small>
        </div>
        <div>
          <dt>Least margin</dt>
          <dd>{margin.length ? Math.round(Math.min(...margin)) : "-"}{margin.length ? <small>h</small> : null}</dd>
        </div>
        <div>
          <dt>Work orders</dt>
          <dd style={{ fontSize: 20, paddingTop: 4 }}>
            {approved} approved
          </dd>
          <small className="muted">{pending} waiting</small>
        </div>
      </dl>

      {!v ? (
        view.error ? null : <Loading what={`${tail} maintenance`} />
      ) : (
        <div className="maint-grid">
          <div className="space-y-4">
            {item ? (
              <NextUp item={item} now={now} user={user} setUser={setUser} onChanged={reload} first={item === next || items[0] === item} />
            ) : (
              <Card>
                <h2 style={{ margin: 0, fontSize: 20 }}>No maintenance planned for {tail}</h2>
                <p className="muted" style={{ margin: "8px 0 0", lineHeight: 1.55 }}>
                  No task is open: nothing is due inside the planning horizon and no system has a low conservative life. Suggestions below show what is worn
                  but not yet urgent.
                </p>
              </Card>
            )}

            {items.length > 1 && (
              <Card title={`All open tasks for ${tail}`}>
                <div style={{ overflowX: "auto" }}>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Task</th>
                        <th>System</th>
                        <th>Slot (UTC)</th>
                        <th>Technician</th>
                        <th>Deadline</th>
                        <th>Order</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((i) => (
                        <tr key={i.task_id} className={i.task_id === item?.task_id ? "picked" : ""} onClick={() => setFocus(i.task_id)}>
                          <td style={{ fontWeight: 500 }}>{i.title}</td>
                          <td className="muted">{i.component}</td>
                          <td className="mono" style={{ whiteSpace: "nowrap" }}>
                            {i.assignment ? `${i.assignment.station} ${fmtZ(i.assignment.start)}` : "unscheduled"}
                          </td>
                          <td className="mono">{i.assignment?.technician_id ?? "-"}</td>
                          <td className="mono" style={{ whiteSpace: "nowrap" }}>{fmtZ(i.deadline)}</td>
                          <td>
                            {i.work_order ? (
                              <span className={`status s-${i.work_order.status === "approved" ? "ok" : i.work_order.status === "rejected" ? "alert" : "watch"}`}>
                                {i.approved_slot_moved ? "Review" : i.work_order.status === "pending_approval" ? "Waiting" : i.work_order.status === "approved" ? "Approved" : "Rejected"}
                              </span>
                            ) : (
                              <span className="muted">none</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Card>
            )}

            <Gantt now={v.now} legs={legs.data ?? []} view={v} items={items} focus={item?.task_id} />
            <Suggestions list={v.suggestions} now={v.now} onFocus={() => window.dispatchEvent(new CustomEvent("aerotwin:copilot"))} />
            <History view={v} />
          </div>
          <div className="space-y-4">
            <Resources item={item} stations={stations.data ?? []} now={now} />
            <Queue rows={queue.data?.rows ?? []} now={now} tail={tail} onPick={(r) => (r.tail === tail ? setFocus(r.task_id) : navigate(`/maintenance/${r.tail}`))} />
          </div>
        </div>
      )}
    </div>
  );
}
