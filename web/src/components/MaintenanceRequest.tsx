import { useState } from "react";
import { Link } from "react-router-dom";
import { get, post, usePoll } from "../api/client";
import type { TwinView, WorkOrder } from "../api/types";
import { niceComponent } from "../lib/derive";
import { fmtH, fmtUsd, fmtZ, hoursBetween } from "../lib/format";
import { actorName, useRole } from "../lib/role";
import { Btn, Chip, StatusPill } from "./ui";

type Item = {
  task_id: string; title: string; component_id: string; component: string; basis: string; deadline: string; duration_h: number; skill: string;
  part: string | null; part_qty: number; card?: { id?: string; ata?: string; note?: string; trigger?: string } | null;
  assignment?: { reason: string; technician_id: string | null; window_hours: number; extension_h: number; part_source: string | null } | null;
  baseline?: { station: string; start: string; end: string } | null;
  exposure?: { hours_to_fix?: number | null; flights_before_fix?: number | null; slack_to_deadline_h?: number | null } | null;
  logistics?: { station: string; technician_assigned: string | null; technicians_covering: number; part?: { name: string; needed: number; on_hand: number; eta_hours: number; ship_from: string | null; ship_hours: number; ship_cost_usd: number } } | null;
  cost?: { parts: number; labor: number; bay: number; overhead?: number; shipping: number; delay: number; total: number; hours: number; crew: number } | null;
};
type Options = {
  editable: boolean; max_extension_h: number; duration_h: number | null; deadline: string | null; windows: { start: string; end: string; hours: number }[];
  technician: { id: string; shift_start: string; shift_end: string } | null;
  part: { name: string; needed: number; on_hand: number; eta_hours: number; ship_from: string | null; ship_hours: number; ship_cost_usd: number } | null;
};

const STATUS: Record<string, [string, string]> = {
  pending_approval: ["Waiting for a decision", "text-amber-600"],
  approved: ["Approved", "text-emerald-600"],
  rejected: ["Denied", "text-red-600"],
};
const PARTS = ["BORESCOPE-KIT", "BRAKE-ASSY", "HYD-PUMP", "AVIONICS-LRU"];
const inputOf = (iso: string) => iso.slice(0, 16);
const isoOf = (v: string) => `${v}:00Z`;

/**
 * One maintenance request, whole: its name, what is damaged or being looked at, when and where, why it is needed, the part and whether it has to be
 * ordered, and an estimate of the cost. People who may decide can change the name, the work, the part, the ordering and the timing (the server checks
 * the deadline, the aircraft's ground time and the technician's shift) and then approve or deny. Every change and decision is in the audit trail.
 * `compact` shows the headline and opens the full request on click (for lists and answers); the Maintenance plan page shows it open.
 */
export default function MaintenanceRequest({ w: fresh, onDone, compact = false }: { w: WorkOrder; onDone: () => void; compact?: boolean }) {
  const { role } = useRole();
  const [local, setLocal] = useState<WorkOrder | null>(null);
  const w = local && (fresh.status === "pending_approval" || local.status !== "pending_approval") && (local.edited_at ?? "") >= (fresh.edited_at ?? "") ? local : fresh;
  const [open, setOpen] = useState(!compact || fresh.status === "pending_approval"); // a request waiting for a decision opens in full; decided ones collapse in lists
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [note, setNote] = useState("");
  const [opts, setOpts] = useState<Options | null>(null);
  const [form, setForm] = useState({ title: "", work: "", part: "", part_qty: 0, order_part: false, start: "", end: "" });

  const mt = usePoll<{ items: Item[] }>(open ? `/aircraft/${w.tail}/maintenance` : null, 10000);
  const twin = usePoll<TwinView>(open ? `/aircraft/${w.tail}` : null, 10000);
  const item = mt.data?.items.find((i) => i.task_id === w.task_id);
  const comp = twin.data?.components.find((c) => c.component_id === item?.component_id);
  const pending = w.status === "pending_approval";
  const [statusText, statusTone] = STATUS[w.status] ?? [w.status, ""];
  const name = w.title || item?.title || "Maintenance request";
  const partName = w.part ?? item?.part ?? null;
  const stock = opts?.part ?? item?.logistics?.part ?? null;
  const workText = w.work || item?.card?.note || "";

  const startEdit = async () => {
    setErr("");
    setForm({ title: name, work: workText, part: w.part ?? item?.part ?? "", part_qty: w.part_qty || item?.part_qty || 0, order_part: !!w.order_part, start: inputOf(w.start), end: inputOf(w.end) });
    setEditing(true);
    try {
      const { data } = await get<Options>(`/workorders/${w.id}/options`);
      setOpts(data);
    } catch {
      /* the limits are a hint; the server still checks */
    }
  };
  const save = async () => {
    setBusy(true);
    setErr("");
    try {
      const body: Record<string, unknown> = { user: actorName(), title: form.title, work: form.work, part: form.part, part_qty: form.part_qty, order_part: form.order_part, note };
      if (form.start !== inputOf(w.start)) body.start = isoOf(form.start);
      if (form.end !== inputOf(w.end)) body.end = isoOf(form.end);
      const { data } = await post<WorkOrder>(`/workorders/${w.id}`, body, "PATCH");
      setLocal(data);
      setEditing(false);
      setNote("");
      onDone();
    } catch (e) {
      setErr(String((e as Error).message).replace(/^\d+ [^:]*: /, "").replace(/^\{"error":"[a-z_]+","detail":"|"\}$/g, ""));
    } finally {
      setBusy(false);
    }
  };
  const decide = async (decision: "approve" | "reject") => {
    setBusy(true);
    setErr("");
    try {
      const { data } = await post<WorkOrder>(`/workorders/${w.id}/decision`, { decision, user: actorName(), note });
      if (data?.status) setLocal(data);
      setNote("");
      onDone();
    } catch (e) {
      setErr(String((e as Error).message));
    } finally {
      setBusy(false);
    }
  };

  const set = <K extends keyof typeof form>(k: K, v: (typeof form)[K]) => setForm((f) => ({ ...f, [k]: v }));
  const hours = hoursBetween(w.start, w.end);
  const ex = item?.exposure;

  return (
    <article className="mreq">
      <header className="mreq-head">
        <div className="min-w-0">
          <div className="mreq-kicker">
            Maintenance request · {w.id} · <span className={statusTone}>{statusText}</span>
          </div>
          {editing ? (
            <input className="mreq-title-input" value={form.title} onChange={(e) => set("title", e.target.value)} aria-label="Name of the job" />
          ) : (
            <h3>{name}</h3>
          )}
          <div className="mreq-sub">
            <Link to={`/aircraft/${w.tail}`} className="text-blue-600 hover:underline">
              {w.tail}
            </Link>
            <span>
              {item ? niceComponent(item.component_id) : w.task_id.replace(/^T-SIM-\d+-/, "").replace(/-\d+$/, "").replace("_", " ")}
            </span>
            {comp && <StatusPill status={comp.status} />}
            {w.edited_by && <Chip title={`Edited ${fmtZ(w.edited_at)}`}>edited by {w.edited_by}</Chip>}
          </div>
        </div>
        {compact && (
          <button className="mreq-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
            {open ? "Hide the request" : "Show the request"}
          </button>
        )}
      </header>

      {!open ? (
        <p className="mreq-line">
          {w.station} · {fmtZ(w.start)} to {fmtZ(w.end)} ({fmtH(hours)}){partName ? ` · ${partName}${w.order_part ? " (to be ordered)" : ""}` : ""}
        </p>
      ) : (
        <div className="mreq-grid">
          <section>
            <h4>What is being looked at</h4>
            <p className="mreq-big">
              {item ? niceComponent(item.component_id) : "The component"}
              {comp && (
                <span className="mreq-muted">
                  {" "}
                  · health {Math.round(comp.health * 100)}%
                  {comp.rul_cycles ? ` · conservative life ${Math.round(comp.rul_cycles.p10)} flights` : ""}
                </span>
              )}
            </p>
            <label className="mreq-field">
              Work to be done
              {editing ? (
                <textarea rows={3} value={form.work} onChange={(e) => set("work", e.target.value)} placeholder="What will be inspected, repaired or replaced" />
              ) : (
                <span>{workText || `${name}. ${item?.skill ? `Skill needed: ${item.skill}.` : ""}`}</span>
              )}
            </label>
            <div className="mreq-field">
              Part
              {editing ? (
                <span className="mreq-inline">
                  <input list="mreq-parts" value={form.part} onChange={(e) => set("part", e.target.value)} placeholder="Item being replaced (none)" aria-label="Part being replaced" />
                  <datalist id="mreq-parts">
                    {PARTS.map((p) => (
                      <option key={p} value={p} />
                    ))}
                  </datalist>
                  <input type="number" min={0} max={20} value={form.part_qty} onChange={(e) => set("part_qty", Number(e.target.value))} aria-label="Quantity" style={{ width: 70 }} />
                  <label className="mreq-check">
                    <input type="checkbox" checked={form.order_part} onChange={(e) => set("order_part", e.target.checked)} /> needs to be ordered
                  </label>
                </span>
              ) : partName ? (
                <span>
                  {w.part_qty || item?.part_qty || 1} × {partName}
                  {w.order_part ? <b className="text-amber-600"> · needs to be ordered</b> : <span className="mreq-muted"> · from stock</span>}
                </span>
              ) : (
                <span className="mreq-muted">No part is replaced</span>
              )}
              {stock && (
                <small>
                  {stock.on_hand > 0
                    ? `${stock.on_hand} on hand at ${w.station}`
                    : stock.ship_from
                      ? `none at ${w.station}: ships from ${stock.ship_from} in ${stock.ship_hours} h (${fmtUsd(stock.ship_cost_usd)})`
                      : `none on hand at ${w.station}${stock.eta_hours > 0 ? `, arrives in ${stock.eta_hours} h` : ""}`}
                </small>
              )}
            </div>
          </section>

          <section>
            <h4>When and where</h4>
            <p className="mreq-big">
              {w.station}
              <span className="mreq-muted">
                {" "}
                · {item?.logistics?.technician_assigned ?? item?.assignment?.technician_id ?? "technician to be assigned"}
              </span>
            </p>
            {editing ? (
              <div className="mreq-times">
                <label className="mreq-field">
                  Starts (UTC)
                  <input type="datetime-local" value={form.start} onChange={(e) => set("start", e.target.value)} />
                </label>
                <label className="mreq-field">
                  Ends (UTC)
                  <input type="datetime-local" value={form.end} onChange={(e) => set("end", e.target.value)} />
                </label>
              </div>
            ) : (
              <p>
                {fmtZ(w.start)} to {fmtZ(w.end)} <span className="mreq-muted">({fmtH(hours)})</span>
              </p>
            )}
            {editing && opts && (
              <small className="mreq-hint">
                {opts.windows.length > 0 ? `${w.tail} is on the ground at ${w.station}: ${opts.windows.map((x) => `${fmtZ(x.start)} to ${fmtZ(x.end)}`).join(" · ")}. ` : ""}
                {opts.duration_h ? `Needs ${opts.duration_h} h. ` : ""}
                {opts.deadline ? `Must finish by ${fmtZ(opts.deadline)}. ` : ""}
                {opts.technician ? `Technician ${opts.technician.id} is on shift ${fmtZ(opts.technician.shift_start)} to ${fmtZ(opts.technician.shift_end)}.` : ""}
              </small>
            )}
            {item?.baseline ? (
              <small className="mreq-hint">The static baseline would do it at {item.baseline.station}, {fmtZ(item.baseline.start)}.</small>
            ) : (
              item && <small className="mreq-hint">The static baseline cannot fit this job before its deadline.</small>
            )}
          </section>

          <section>
            <h4>Why it is needed</h4>
            <p>{item?.basis ?? "The twin raised this task from the component's condition."}</p>
            {item && (
              <ul className="mreq-facts">
                <li>
                  Must finish by <b>{fmtZ(item.deadline)}</b>
                  {ex?.slack_to_deadline_h != null && <span className="mreq-muted"> · {fmtH(ex.slack_to_deadline_h)} to spare</span>}
                </li>
                {ex?.flights_before_fix != null && (
                  <li>
                    {ex.flights_before_fix} {ex.flights_before_fix === 1 ? "flight" : "flights"} before it is fixed
                    {ex.hours_to_fix != null && <span className="mreq-muted"> · {fmtH(ex.hours_to_fix)} from now</span>}
                  </li>
                )}
              </ul>
            )}
            {w.evidence.length > 0 && (
              <div className="mreq-chips">
                {w.evidence.map((e, i) => (
                  <Chip key={i} tone="blue" title={e.note}>
                    {e.kind}:{e.ref}
                  </Chip>
                ))}
              </div>
            )}
            {item?.assignment?.reason && (
              <details className="mreq-hint">
                <summary>Why this slot</summary>
                <p>{item.assignment.reason}</p>
              </details>
            )}
          </section>

          <section>
            <h4>Estimated cost</h4>
            {item?.cost ? (
              <>
                <p className="mreq-big">{fmtUsd(item.cost.total)}</p>
                <small className="mreq-hint">
                  Parts {fmtUsd(item.cost.parts)} · labor {fmtUsd(item.cost.labor)} ({item.cost.crew} technicians, {fmtH(item.cost.hours)}) · bay time {fmtUsd(item.cost.bay)}{item.cost.overhead ? ` · overhead ${fmtUsd(item.cost.overhead)}` : ""}
                  {item.cost.shipping > 0 ? ` · shipping ${fmtUsd(item.cost.shipping)}` : ""}
                  {item.cost.delay > 0 ? ` · delay ${fmtUsd(item.cost.delay)}` : ""}. From a simulated price list.
                </small>
              </>
            ) : (
              <p className="mreq-muted">{mt.data ? "Not in the current plan." : "Loading…"}</p>
            )}
          </section>
        </div>
      )}

      {err && (
        <p className="mreq-error" role="alert">
          {err}
        </p>
      )}

      {pending && role.canDecide && (open || !compact) && (
        <footer className="mreq-actions">
          <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Optional note for the audit trail" aria-label="Note" className="mreq-note" />
          {editing ? (
            <>
              <Btn tone="primary" disabled={busy} onClick={save}>
                {busy ? "Saving…" : "Save changes"}
              </Btn>
              <Btn disabled={busy} onClick={() => (setEditing(false), setErr(""))}>
                Cancel
              </Btn>
            </>
          ) : (
            <>
              <Btn tone="primary" disabled={busy} onClick={() => decide("approve")}>
                Approve
              </Btn>
              <Btn disabled={busy} onClick={() => decide("reject")}>
                Deny
              </Btn>
              <Btn tone="ghost" disabled={busy} onClick={startEdit}>
                Edit request
              </Btn>
            </>
          )}
          <span className="mreq-muted">as {actorName()}</span>
        </footer>
      )}
      {pending && !role.canDecide && open && <p className="mreq-muted mt-3 text-xs">Waiting for maintenance control. Only operations control and maintenance planners can approve, deny or change a request.</p>}
      {!pending && w.decided_by && (
        <p className="mreq-muted mt-3 text-xs">
          {w.status === "approved" ? "Approved" : "Denied"} by {w.decided_by}
          {w.decided_at ? ` at ${fmtZ(w.decided_at)}` : ""}
          {w.note ? ` · “${w.note}”` : ""}
        </p>
      )}
    </article>
  );
}
