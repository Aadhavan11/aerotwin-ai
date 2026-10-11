import { useEffect, useMemo, useRef, useState } from "react";
import { post } from "../api/client";
import type { AircraftSummary, Leg, PlanBundle, WorkOrder } from "../api/types";
import { fmtZ } from "../lib/format";
import { actorName } from "../lib/role";
import { Btn } from "./ui";

const HOUR_H = 42;
const DAY_MS = 86_400_000;
const ms = (iso: string) => new Date(iso).getTime();
const dayOf = (t: number) => Math.floor(t / DAY_MS) * DAY_MS;
const inputOf = (t: number) => new Date(t).toISOString().slice(0, 16);
const dayLabel = (d: number) => `${["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][new Date(d).getUTCDay()]} ${fmtZ(new Date(d).toISOString()).slice(0, 6)}`;

export type CalItem = {
  key: string;
  kind: "opt" | "base" | "manual";
  tail: string;
  station: string;
  start: number;
  end: number;
  title: string;
  status: "proposed" | "pending_approval" | "approved" | "baseline";
  taskId: string;
  wo?: WorkOrder;
};
export type Prefill = { tail: string; station: string; start: number; end: number };

/** The aircraft's station at a moment: where its last leg landed, or where its next leg leaves from. */
function stationAt(legs: Leg[], tail: string, t: number, home: string): string {
  const mine = legs.filter((l) => l.tail === tail).sort((a, b) => a.sched_dep.localeCompare(b.sched_dep));
  let at = home;
  for (const l of mine) {
    if (ms(l.sched_arr) <= t) at = l.arr;
    else if (ms(l.sched_dep) > t) return mine.indexOf(l) === 0 ? l.dep : at;
  }
  return at;
}

/**
 * Maintenance as a day calendar, by the hour: one column per aircraft, flights in grey, jobs in colour (blue: proposed by the plan, amber: waiting for a
 * decision, green: approved, violet outline: entered by hand). Click a job to open its request; click an empty hour to start a new request there.
 */
export default function MaintenanceCalendar({
  plan, legs, orders, aircraft, simNow, show, selected, onSelect, onCreate,
}: {
  plan: PlanBundle; legs: Leg[]; orders: WorkOrder[]; aircraft: AircraftSummary[]; simNow: string; show: "both" | "opt" | "base";
  selected: string | null; onSelect: (i: CalItem) => void; onCreate: (p: Prefill) => void;
}) {
  const now = ms(simNow);
  const items = useMemo<CalItem[]>(() => {
    const live = orders.filter((w) => w.status !== "rejected");
    const woFor = (taskId: string) => live.filter((w) => w.task_id === taskId).sort((a, b) => (a.status === "approved" ? -1 : 1) - (b.status === "approved" ? -1 : 1))[0];
    const out: CalItem[] = [];
    for (const a of plan.current.assignments) {
      const wo = woFor(a.task_id);
      out.push({
        key: "o:" + a.task_id, kind: "opt", tail: a.tail, station: wo?.station ?? a.station, start: ms(wo?.start ?? a.start), end: ms(wo?.end ?? a.end),
        title: wo?.title || a.task_id.replace(/^T-SIM-\d+-/, "").replace(/-\d+$/, "").replace("_", " "), status: wo ? (wo.status as CalItem["status"]) : "proposed", taskId: a.task_id, wo,
      });
    }
    for (const w of live.filter((x) => x.task_id.startsWith("M-")))
      out.push({ key: "m:" + w.id, kind: "manual", tail: w.tail, station: w.station, start: ms(w.start), end: ms(w.end), title: w.title || "Manual request", status: w.status as CalItem["status"], taskId: w.task_id, wo: w });
    if (show !== "opt")
      for (const a of plan.baseline.assignments)
        out.push({ key: "b:" + a.task_id, kind: "base", tail: a.tail, station: a.station, start: ms(a.start), end: ms(a.end), title: "baseline", status: "baseline", taskId: a.task_id });
    return show === "base" ? out.filter((i) => i.kind === "base") : out;
  }, [plan, orders, show]);

  const firstDay = dayOf(now);
  const days = Array.from({ length: 6 }, (_, i) => firstDay + i * DAY_MS);
  const firstJob = items.filter((i) => i.kind !== "base").sort((a, b) => a.start - b.start)[0];
  const [day, setDay] = useState(dayOf(firstJob?.start ?? now));
  const [extra, setExtra] = useState<string[]>([]);
  const scroller = useRef<HTMLDivElement>(null);

  const tails = useMemo(() => {
    const t = new Set<string>(extra);
    for (const i of items) if (i.end > day && i.start < day + DAY_MS) t.add(i.tail);
    if (t.size === 0) for (const i of items) t.add(i.tail);
    return [...t].sort();
  }, [items, day, extra]);

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const here = items.filter((i) => i.kind !== "base" && i.end > day && i.start < day + DAY_MS).sort((a, b) => a.start - b.start)[0];
    const t = here ? Math.max(day, here.start) : day <= now && now < day + DAY_MS ? now : day + 8 * 3_600_000;
    el.scrollTo({ top: Math.max(0, ((t - day) / 3_600_000 - 1.5) * HOUR_H), behavior: "smooth" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [day]);

  const homeOf = (tail: string) => aircraft.find((a) => a.tail === tail)?.home ?? "ATL";
  const clickColumn = (tail: string, e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return;
    const y = e.clientY - e.currentTarget.getBoundingClientRect().top;
    const start = Math.max(Math.ceil(now / 1_800_000) * 1_800_000, day + Math.floor((y / HOUR_H) * 2) * 1_800_000);
    onCreate({ tail, station: stationAt(legs, tail, start, homeOf(tail)), start, end: start + 2 * 3_600_000 });
  };
  const spare = aircraft.map((a) => a.tail).filter((t) => !tails.includes(t)).sort();
  const count = (d: number) => items.filter((i) => i.kind !== "base" && i.end > d && i.start < d + DAY_MS).length;

  return (
    <div className="cal">
      <div className="cal-bar">
        <div className="cal-days" role="tablist" aria-label="Day">
          {days.map((d) => (
            <button key={d} role="tab" aria-selected={d === day} className={d === day ? "on" : ""} onClick={() => setDay(d)}>
              {dayLabel(d)}
              {count(d) > 0 && <i>{count(d)}</i>}
            </button>
          ))}
        </div>
        <label className="cal-add">
          Add an aircraft
          <select value="" onChange={(e) => e.target.value && setExtra((x) => [...x, e.target.value])} aria-label="Add an aircraft to the calendar">
            <option value="">Choose…</option>
            {spare.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="cal-scroll" ref={scroller}>
        <div className="cal-grid" style={{ gridTemplateColumns: `54px repeat(${tails.length}, minmax(158px, 1fr))` }}>
          <div className="cal-corner">UTC</div>
          {tails.map((t) => (
            <div key={t} className="cal-colhead">
              <b>{t}</b>
              <span>{stationAt(legs, t, day + 12 * 3_600_000, homeOf(t))}</span>
            </div>
          ))}
          <div className="cal-hours" style={{ height: 24 * HOUR_H }}>
            {Array.from({ length: 24 }, (_, h) => (
              <div key={h} style={{ height: HOUR_H }}>
                {String(h).padStart(2, "0")}:00
              </div>
            ))}
          </div>
          {tails.map((t) => (
            <div key={t} className="cal-col" style={{ height: 24 * HOUR_H }} onClick={(e) => clickColumn(t, e)} title="Click an empty hour to request maintenance there">
              {legs
                .filter((l) => l.tail === t && ms(l.sched_arr) > day && ms(l.sched_dep) < day + DAY_MS)
                .map((l) => {
                  const s = Math.max(ms(l.sched_dep), day), e = Math.min(ms(l.sched_arr), day + DAY_MS);
                  return (
                    <div key={l.id} className="cal-leg" style={{ top: ((s - day) / 3_600_000) * HOUR_H, height: Math.max(14, ((e - s) / 3_600_000) * HOUR_H - 2) }} title={`${l.dep} to ${l.arr} · ${fmtZ(l.sched_dep)}`}>
                      {l.dep}→{l.arr}
                    </div>
                  );
                })}
              {items
                .filter((i) => i.tail === t && i.end > day && i.start < day + DAY_MS)
                .map((i) => {
                  const s = Math.max(i.start, day), e = Math.min(i.end, day + DAY_MS);
                  return (
                    <button
                      key={i.key}
                      className={`cal-job ${i.kind} ${i.status} ${selected === (i.wo ? `wo:${i.wo.id}` : `task:${i.taskId}`) ? "sel" : ""}`}
                      style={{ top: ((s - day) / 3_600_000) * HOUR_H, height: Math.max(26, ((e - s) / 3_600_000) * HOUR_H - 2) }}
                      onClick={() => (i.kind === "base" ? undefined : onSelect(i))}
                      title={`${i.title} · ${i.station} · ${fmtZ(new Date(i.start).toISOString())} to ${fmtZ(new Date(i.end).toISOString())}`}
                    >
                      <b>{i.title}</b>
                      <span>
                        {i.station} · {i.kind === "base" ? "static baseline" : i.status === "proposed" ? "proposed" : i.status === "pending_approval" ? "waiting" : i.status}
                      </span>
                    </button>
                  );
                })}
              {day <= now && now < day + DAY_MS && <div className="cal-now" style={{ top: ((now - day) / 3_600_000) * HOUR_H }} />}
            </div>
          ))}
        </div>
      </div>
      <div className="cal-legend">
        <span><i className="proposed" /> proposed by the plan</span>
        <span><i className="pending_approval" /> waiting for a decision</span>
        <span><i className="approved" /> approved</span>
        <span><i className="manual" /> entered by hand</span>
        <span><i className="baseline" /> static baseline</span>
        <span><i className="leg" /> flight</span>
        <span className="cal-hint">Click a job to open its request · click an empty hour to add one</span>
      </div>
    </div>
  );
}

/** Enter a maintenance request by hand. The server checks that the aircraft is on the ground at that station for the whole time. */
export function NewRequestForm({
  prefill, aircraft, stations, onClose, onCreated,
}: {
  prefill: Prefill | null; aircraft: AircraftSummary[]; stations: string[]; onClose: () => void; onCreated: (w: WorkOrder) => void;
}) {
  const [f, setF] = useState({
    tail: prefill?.tail ?? aircraft[0]?.tail ?? "", station: prefill?.station ?? stations[0] ?? "", start: prefill ? inputOf(prefill.start) : "", end: prefill ? inputOf(prefill.end) : "",
    title: "", work: "", why: "", part: "", part_qty: 1, order_part: false,
  });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const set = <K extends keyof typeof f>(k: K, v: (typeof f)[K]) => setF((x) => ({ ...x, [k]: v }));
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const { data } = await post<WorkOrder>("/workorders", { user: actorName(), tail: f.tail, station: f.station, start: `${f.start}:00Z`, end: `${f.end}:00Z`, title: f.title, work: f.work, why: f.why, part: f.part, part_qty: f.part ? f.part_qty : 0, order_part: f.order_part });
      onCreated(data);
    } catch (x) {
      setErr(String((x as Error).message).replace(/^\d+ [^:]*: /, "").replace(/^\{"error":"[a-z_]+","detail":"|"\}$/g, ""));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="mreq newreq" onSubmit={submit}>
      <header className="mreq-head">
        <div>
          <div className="mreq-kicker">New maintenance request</div>
          <h3>Enter a request by hand</h3>
        </div>
        <button type="button" className="mreq-toggle" onClick={onClose}>
          Close
        </button>
      </header>
      <div className="mreq-grid">
        <section>
          <h4>What</h4>
          <label className="mreq-field">
            Name
            <input value={f.title} onChange={(e) => set("title", e.target.value)} placeholder="For example: Cabin pressure check" required />
          </label>
          <label className="mreq-field">
            Aircraft
            <select value={f.tail} onChange={(e) => set("tail", e.target.value)}>
              {aircraft.map((a) => (
                <option key={a.tail} value={a.tail}>
                  {a.tail} · {a.type} · {a.status.replace("_", " ")}
                </option>
              ))}
            </select>
          </label>
          <label className="mreq-field">
            Work to be done
            <textarea rows={3} value={f.work} onChange={(e) => set("work", e.target.value)} placeholder="What is damaged or to be inspected, and what will be done" />
          </label>
        </section>
        <section>
          <h4>When and where</h4>
          <label className="mreq-field">
            Station
            <select value={f.station} onChange={(e) => set("station", e.target.value)}>
              {stations.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </label>
          <div className="mreq-times">
            <label className="mreq-field">
              Starts (UTC)
              <input type="datetime-local" value={f.start} onChange={(e) => set("start", e.target.value)} required />
            </label>
            <label className="mreq-field">
              Ends (UTC)
              <input type="datetime-local" value={f.end} onChange={(e) => set("end", e.target.value)} required />
            </label>
          </div>
          <small className="mreq-hint">The aircraft has to be on the ground at that station for the whole time; the calendar shows its flights in grey.</small>
        </section>
        <section>
          <h4>Why it is needed</h4>
          <label className="mreq-field">
            Reason
            <textarea rows={3} value={f.why} onChange={(e) => set("why", e.target.value)} placeholder="What was observed, or what makes this necessary" />
          </label>
        </section>
        <section>
          <h4>Part</h4>
          <span className="mreq-inline">
            <input list="nr-parts" value={f.part} onChange={(e) => set("part", e.target.value)} placeholder="Item being replaced (none)" aria-label="Part" />
            <datalist id="nr-parts">
              {["BORESCOPE-KIT", "BRAKE-ASSY", "HYD-PUMP", "AVIONICS-LRU"].map((p) => (
                <option key={p} value={p} />
              ))}
            </datalist>
            <input type="number" min={1} max={20} value={f.part_qty} onChange={(e) => set("part_qty", Number(e.target.value))} aria-label="Quantity" style={{ width: 70 }} />
          </span>
          <label className="mreq-check">
            <input type="checkbox" checked={f.order_part} onChange={(e) => set("order_part", e.target.checked)} /> needs to be ordered
          </label>
        </section>
      </div>
      {err && (
        <p className="mreq-error" role="alert">
          {err}
        </p>
      )}
      <footer className="mreq-actions">
        <Btn tone="primary" disabled={busy}>
          {busy ? "Sending…" : "Send for approval"}
        </Btn>
        <span className="mreq-muted">as {actorName()} · it waits for approval like any other request</span>
      </footer>
    </form>
  );
}
