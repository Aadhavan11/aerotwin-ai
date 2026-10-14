import { useState } from "react";
import { getAtaTable } from "../api/reliability";
import ReliabilityTabs from "../components/ReliabilityTabs";
import { Card, Chip, Loading } from "../components/ui";
import { useAvailable } from "../lib/useAvailable";

export default function ReliabilityAta() {
  const table = useAvailable(getAtaTable);
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const chapters = table.data?.chapters ?? [];
  const toggle = (chapter: string) => setOpen((previous) => {
    const next = new Set(previous);
    if (next.has(chapter)) next.delete(chapter); else next.add(chapter);
    return next;
  });
  return (
    <div className="space-y-5">
      <ReliabilityTabs />
      <Card title="ATA table" right={<span className="sim-tag">SIMULATED</span>}>
        {table.loading ? <Loading what="ATA taxonomy" /> : table.error || !table.data ? <p className="muted" role="status">{table.error ?? "ATA table is not available."}</p> : <>
          <p className="muted">{table.data.label}</p>
          {!chapters.length ? <p className="muted">No chapters available.</p> : <div className="space-y-4">{chapters.map((chapter, index) => <section key={`${chapter.chapter}-${index}`}>
            <button className="rounded-full border border-slate-300 px-4 py-1.5 text-slate-800" aria-expanded={open.has(chapter.chapter)} aria-controls={`ata-sections-${index}`} onClick={() => toggle(chapter.chapter)}>
              {chapter.chapter} · {chapter.title} · {open.has(chapter.chapter) ? "Collapse" : "Expand"}
            </button>
            {open.has(chapter.chapter) && <div id={`ata-sections-${index}`} className="space-y-4 mt-3">
              {(chapter.sections ?? []).length === 0 ? <p className="muted">No sections available.</p> : (chapter.sections ?? []).map((section, sectionIndex) => <div key={`${section.section}-${sectionIndex}`}>
                <h3 className="insights-h3">{section.section} · {section.title}</h3>
                <p className="muted">Kinds</p><div className="flex flex-wrap gap-2">{(section.kinds ?? []).map((kind) => <Chip key={kind}>{kind}</Chip>)}{!(section.kinds ?? []).length && <span className="muted">—</span>}</div>
                <p className="muted">Fault modes</p><div className="flex flex-wrap gap-2">{(section.fault_modes ?? []).map((mode) => <Chip key={mode} tone="blue">{mode}</Chip>)}{!(section.fault_modes ?? []).length && <span className="muted">—</span>}</div>
              </div>)}
            </div>}
          </section>)}</div>}
          <p className="muted">{table.data.provenance ?? "—"}</p>
        </>}
      </Card>
    </div>
  );
}
