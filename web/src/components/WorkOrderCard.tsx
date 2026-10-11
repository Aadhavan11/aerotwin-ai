import { useState } from "react";
import { Link } from "react-router-dom";
import { post } from "../api/client";
import type { WorkOrder } from "../api/types";
import { fmtH, fmtZ, hoursBetween } from "../lib/format";
import { actorName, useRole } from "../lib/role";
import { Btn, Chip } from "./ui";

const TONE: Record<string, string> = {
  pending_approval: "text-amber-600",
  approved: "text-emerald-600",
  rejected: "text-red-600",
};

/** One work order with its evidence. Only roles that may decide get the buttons; the API records who decided and writes the audit log. */
export default function WorkOrderCard({ w: fresh, onDone }: { w: WorkOrder; onDone: () => void }) {
  // Show a decision straight away, even where nothing refreshes the list (an answer in the ask bar); the server copy wins once it has one.
  const [local, setLocal] = useState<WorkOrder | null>(null);
  const w = fresh.status !== "pending_approval" ? fresh : (local ?? fresh);
  const { role } = useRole();
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");
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
  const pending = w.status === "pending_approval";
  return (
    <div className="rounded-lg border border-slate-200 p-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold text-slate-800">{w.id}</span>
        <span className={`text-xs font-semibold uppercase tracking-wide ${TONE[w.status] ?? ""}`}>{w.status.replace("_", " ")}</span>
      </div>
      <div className="mt-1 text-slate-700">{w.description}</div>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
        <Link className="text-blue-600 hover:underline" to={`/aircraft/${w.tail}`}>
          {w.tail}
        </Link>
        <span>{w.station}</span>
        <span>
          {fmtZ(w.start)} to {fmtZ(w.end)} ({fmtH(hoursBetween(w.start, w.end))})
        </span>
        <span>plan {w.plan_id}</span>
      </div>
      {w.evidence.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1">
          {w.evidence.map((e, i) => (
            <Chip key={i} tone="blue" title={e.note}>
              {e.kind}:{e.ref}
            </Chip>
          ))}
        </div>
      )}
      {!pending && w.decided_by && (
        <div className="mt-2 text-xs text-slate-500">
          {w.status === "approved" ? "Approved" : "Rejected"} by {w.decided_by} {w.decided_at ? `at ${fmtZ(w.decided_at)}` : ""}
          {w.note ? ` · "${w.note}"` : ""}
        </div>
      )}
      {pending && role.canDecide && (
        <div className="mt-3 space-y-2">
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Optional note for the audit log"
            aria-label="Decision note"
            className="w-full rounded-md border border-slate-300 bg-transparent px-2 py-1 text-xs text-slate-800"
          />
          <div className="flex items-center gap-2">
            <Btn tone="primary" disabled={busy} onClick={() => decide("approve")}>
              Approve
            </Btn>
            <Btn disabled={busy} onClick={() => decide("reject")}>
              Reject
            </Btn>
            <span className="text-xs text-slate-500">as {actorName()}</span>
          </div>
        </div>
      )}
      {pending && !role.canDecide && (
        <div className="mt-2 text-xs text-slate-500">Waiting for maintenance control. Approval is limited to operations control and maintenance planners.</div>
      )}
      {err && <div className="mt-2 text-xs text-red-600">{err}</div>}
    </div>
  );
}
