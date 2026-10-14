import { useState } from "react";
import { usePoll } from "../api/client";
import type { AuditEntry } from "../api/types";
import { downloadCsv } from "../lib/derive";
import { fmtZ } from "../lib/format";
import { Btn } from "./ui";

/** Every decision and action, newest first, with a filter and a CSV export. */
export default function AuditTrail({ maxHeight = "28rem" }: { maxHeight?: string }) {
  const audit = usePoll<AuditEntry[]>("/audit", 4000);
  const [filter, setFilter] = useState("");
  const entries = [...(audit.data ?? [])].reverse();
  const f = filter.trim().toLowerCase();
  const rows = f ? entries.filter((e) => `${e.actor} ${e.action} ${e.ref} ${e.detail}`.toLowerCase().includes(f)) : entries;
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter by who, what or reference"
          aria-label="Filter the audit trail"
          className="w-64 rounded-md border border-slate-300 bg-transparent px-2 py-1 text-xs text-slate-800"
        />
        <Btn onClick={() => downloadCsv("aerotwin-audit.csv", [["time", "actor", "action", "reference", "detail"], ...entries.map((e) => [e.ts, e.actor, e.action, e.ref, e.detail])])}>Export CSV</Btn>
      </div>
      {rows.length === 0 ? (
        <p className="text-sm text-slate-500">{entries.length === 0 ? "No audit entries yet." : "No entries match the filter."}</p>
      ) : (
        <div className="overflow-auto" style={{ maxHeight }}>
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
    </div>
  );
}
