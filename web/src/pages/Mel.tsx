import { useMemo, useState } from "react";
import { closeMel, createMel, extendMel, getMel, getMelSummary, type MelStatus } from "../api/mel";
import { usePoll } from "../api/client";
import type { HealthInfo } from "../api/types";
import { Btn, Card, Chip, Loading } from "../components/ui";
import { metric } from "../lib/operations";
import { apiDetail, expiryCountdown } from "../lib/ops2";
import { useRole } from "../lib/role";
import { useAvailable } from "../lib/useAvailable";

const templates = [
  { value: "apu|failed-to-start", category: "B" }, { value: "ecs pack 1|fault", category: "C" },
  { value: "anti-skid|fault", category: "C" }, { value: "avionics|fault", category: "A" },
  { value: "electrical|fault", category: "C" }, { value: "tires|fault", category: "D" },
];

export default function Mel() {
  const { role } = useRole();
  const canEdit = role.id === "ops" || role.id === "maintenance" || role.id === "admin";
  const health = usePoll<HealthInfo>("/health", 10000);
  const [tail, setTail] = useState("");
  const [status, setStatus] = useState<MelStatus | "all">("open");
  const [revision, setRevision] = useState(0);
  const load = useMemo(() => async () => {
    try { return { payload: await getMel({ tail: tail.trim() || undefined, status: status === "all" ? undefined : status }), message: "" }; }
    catch (error) { return { payload: null, message: apiDetail(error) }; }
  }, [tail, status]);
  const register = useAvailable(load, revision);
  const summary = useAvailable(getMelSummary, revision);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [justifications, setJustifications] = useState<Record<string, string>>(Object.create(null));
  const [createTail, setCreateTail] = useState("");
  const [template, setTemplate] = useState(templates[0].value);
  const [reason, setReason] = useState("");
  const [limitDays, setLimitDays] = useState("");
  const categoryA = templates.find((item) => item.value === template)?.category === "A";
  const items = (register.data?.payload?.items ?? []).filter((item) => item != null);
  const mutate = async (action: () => Promise<unknown>) => {
    if (!canEdit || busy) return;
    setBusy(true); setMessage("");
    try { await action(); setRevision((value) => value + 1); setMessage("Register updated."); }
    catch (error) { setMessage(apiDetail(error)); }
    finally { setBusy(false); }
  };
  const create = () => {
    if (!createTail.trim() || !reason.trim() || (categoryA && (!Number.isSafeInteger(Number(limitDays)) || Number(limitDays) <= 0))) {
      setMessage("Enter a tail, reason and a positive whole-number limit for category A."); return;
    }
    const [component_kind, fault_mode] = template.split("|");
    void mutate(() => createMel({ tail: createTail.trim(), component_kind, fault_mode, reason: reason.trim(), ...(categoryA ? { limit_days: Number(limitDays) } : {}) }));
  };
  return <div className="space-y-5">
    <div className="page-heading"><div><h1>MEL register</h1><p>Track fictional deferrals, restrictions and expiry on the simulation clock.</p></div><span className="sim-tag">SIMULATED</span></div>
    <Card title="Open restrictions summary">
      {summary.loading ? <Loading what="summary" /> : summary.error || !summary.data ? <p className="muted" role="status">{summary.error ?? "Summary is not available."}</p> : <>
        <div className="flex flex-wrap gap-2">{(["A", "B", "C", "D"] as const).map((category) => <Chip key={category}>{category}: {metric(summary.data?.by_category?.[category] ?? 0)}</Chip>)}<Chip tone="amber">Expiring within 72h: {metric(summary.data.expiring_within_72h)}</Chip></div>
        <p className="muted">Aircraft with open restrictions: {(summary.data.aircraft_with_open_restrictions ?? []).filter((value) => value != null).slice().sort().join(", ") || "None"}</p>
      </>}
    </Card>
    <Card title="Deferrals" right={<Btn disabled={busy} onClick={() => setRevision((value) => value + 1)}>Refresh</Btn>}>
      <div className="insights-form flex flex-wrap items-end gap-2">
        <label>Tail (blank = all)<input className="search" value={tail} onChange={(event) => setTail(event.target.value)} /></label>
        <label>Status<select className="select" value={status} onChange={(event) => setStatus(event.target.value as MelStatus | "all")}>{["open", "rectified", "expired", "all"].map((value) => <option key={value}>{value}</option>)}</select></label>
      </div>
      {health.error && <p className="muted" role="status">Simulation clock is not available; countdowns may be unavailable or out of date.</p>}
      {message && <p className="muted" role="status">{message}</p>}
      {!canEdit && <p className="muted" role="status">Not available for your role.</p>}
      {register.loading ? <Loading what="deferrals" /> : register.error || register.data?.message ? <p className="muted" role="status">{register.error ?? register.data?.message}</p> : !items.length ? <p className="muted" role="status">No deferrals match these filters.</p> : <div className="overflow-auto"><table className="data-table">
        <thead><tr>{["ID", "Tail", "ATA", "Title", "Category", "Reason", "Deferred at", "Expires at", "Extensions used", "Status", "Restrictions", "Actions"].map((label) => <th key={label}>{label}</th>)}</tr></thead>
        <tbody>{items.map((item, index) => {
          const countdown = expiryCountdown(item.expires_at, health.data?.sim_now);
          const id = item.id;
          const justification = id ? justifications[id] ?? "" : "";
          const extendable = (item.category === "B" || item.category === "C") && item.extensions_used != null && item.extensions_used < 1 && item.status === "open";
          return <tr key={id ?? index}>
            <td>{id ?? "—"}</td><td>{item.tail ?? "—"}</td><td>{item.ata_section ?? item.ata_chapter ?? "—"}</td><td>{item.title ?? "—"}</td><td><Chip>{item.category ?? "—"}</Chip></td><td>{item.reason ?? "—"}</td>
            <td>{item.deferred_at ?? "—"}</td><td>{item.expires_at ?? "—"} <Chip tone={countdown.expired ? "amber" : "slate"}>{countdown.label}</Chip></td>
            <td>{metric(item.extensions_used)}</td><td><Chip tone={item.status === "expired" ? "amber" : "slate"}>{item.status ?? "—"}</Chip></td>
            <td><ul className="list-disc pl-4">{(item.restrictions ?? []).map((restriction, i) => <li key={i}>{restriction ?? "—"}</li>)}</ul></td>
            <td>{!canEdit ? <span className="muted">Not available for your role.</span> : <div className="insights-form flex flex-wrap items-end gap-2">
              <Btn disabled={busy || !id || item.status !== "open"} onClick={() => { if (id) void mutate(() => closeMel(id)); }}>Close</Btn>
              {extendable && <form className="flex flex-wrap items-end gap-2" onSubmit={(event) => { event.preventDefault(); if (id && justification.trim()) void mutate(() => extendMel(id, justification.trim())); }}>
                <label>Justification<input className="search" aria-label={`Extension justification for ${id ?? "deferral"}`} required value={justification} onChange={(event) => { if (id) setJustifications((values) => ({ ...values, [id]: event.target.value })); }} /></label>
                <button className="app-agents" type="submit" disabled={busy || !id || !justification.trim()}>Extend</button>
              </form>}
            </div>}</td>
          </tr>;
        })}</tbody>
      </table></div>}
    </Card>
    <Card title="Create deferral">
      {!canEdit ? <p className="muted" role="status">Not available for your role.</p> : <form className="insights-form flex flex-wrap items-end gap-2" onSubmit={(event) => { event.preventDefault(); create(); }}>
        <label>Tail<input className="search" required value={createTail} onChange={(event) => setCreateTail(event.target.value)} /></label>
        <label>Component / fault template<select className="select" value={template} onChange={(event) => setTemplate(event.target.value)}>{templates.map((item) => <option key={item.value} value={item.value}>{item.value.replace("|", " / ")} ({item.category})</option>)}</select></label>
        <label>Reason<input className="search" required value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        {categoryA && <label>Limit days<input className="search" type="number" min={1} step={1} required value={limitDays} onChange={(event) => setLimitDays(event.target.value)} /></label>}
        <button className="app-agents" type="submit" disabled={busy}>{busy ? "Working…" : "Create deferral"}</button>
      </form>}
    </Card>
  </div>;
}
