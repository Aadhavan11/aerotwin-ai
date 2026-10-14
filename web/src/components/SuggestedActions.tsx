import { useState } from "react";
import { usePoll } from "../api/client";
import { fmtZ } from "../lib/format";
import { Btn, Card } from "./ui";
import type { Prefill } from "./MaintenanceCalendar";

type Suggestion = {
  id: string; kind: "inspect" | "routine"; tail: string; component: string; title: string; work: string; why: string; act_before: string; duration_h: number; part: string | null;
  slot: { station: string; start: string; end: string } | null; shares_visit_with: string | null;
};
const KIND: Record<string, string> = { inspect: "Plan ahead", routine: "Routine, due soon" };

/**
 * Work that is not pending or required now but is better planned ahead: worn or trending components with no job yet, and routine checks that fall due in the next three weeks.
 * Each comes with the first ground window that fits (preferring a visit the aircraft already has planned). It is advice: "Plan it" opens a request to review, nothing is created until you send it.
 */
export default function SuggestedActions({ onPlan }: { onPlan: (p: Prefill) => void }) {
  const s = usePoll<{ suggestions: Suggestion[]; total: number }>("/maintenance/suggestions?limit=6", 30000);
  const [hidden, setHidden] = useState<string[]>(() => {
    try {
      return JSON.parse(sessionStorage.getItem("aerotwin.hidden-suggestions") || "[]");
    } catch {
      return [];
    }
  });
  const hide = (id: string) => {
    const next = [...hidden, id];
    setHidden(next);
    try {
      sessionStorage.setItem("aerotwin.hidden-suggestions", JSON.stringify(next));
    } catch {
      /* ignore */
    }
  };
  const rows = (s.data?.suggestions ?? []).filter((x) => !hidden.includes(x.id));
  return (
    <Card title="Suggested actions" right={<span className="text-xs text-slate-500">worth planning ahead; none of these is required yet{s.data ? ` · ${s.data.total} found` : ""}</span>}>
      {rows.length === 0 ? (
        <p className="text-sm text-slate-500">{s.data ? "Nothing to suggest right now." : "Looking for work worth planning ahead…"}</p>
      ) : (
        <ul className="suggest">
          {rows.map((x) => (
            <li key={x.id}>
              <div className="suggest-main">
                <div className="suggest-head">
                  <span className={`suggest-kind ${x.shares_visit_with ? "same" : x.kind}`}>{x.shares_visit_with ? "Same visit" : KIND[x.kind]}</span>
                  <b>{x.title}</b>
                  <span className="mreq-muted">
                    {x.tail} · {x.component}
                  </span>
                </div>
                <p>{x.why}</p>
                <small>
                  {x.slot ? `Fits at ${x.slot.station}, ${fmtZ(x.slot.start)} to ${fmtZ(x.slot.end)} (${x.duration_h} h)` : "No ground window fits before it is due: needs a person to look at the rotation"}
                  {x.part ? ` · part: ${x.part}` : ""}
                </small>
              </div>
              <div className="suggest-actions">
                <Btn
                  tone="primary"
                  disabled={!x.slot}
                  onClick={() =>
                    x.slot &&
                    onPlan({ tail: x.tail, station: x.slot.station, start: new Date(x.slot.start).getTime(), end: new Date(x.slot.end).getTime(), title: x.title, work: x.work, why: x.why, part: x.part ?? "" })
                  }
                >
                  Plan it
                </Btn>
                <Btn tone="ghost" onClick={() => hide(x.id)}>
                  Not now
                </Btn>
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
