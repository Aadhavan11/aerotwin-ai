import { useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { usePoll } from "./api/client";
import type { HealthInfo } from "./api/types";
import { fmtZ } from "./lib/format";
import CopilotDrawer from "./components/CopilotDrawer";
import Fleet from "./pages/Fleet";
import Aircraft from "./pages/Aircraft";
import Planning from "./pages/Planning";
import Lab from "./pages/Lab";

const nav = [{ to: "/", label: "Fleet" }, { to: "/planning", label: "Planning" }, { to: "/lab", label: "Simulation Lab" }];

export default function App() {
  const [open, setOpen] = useState(false);
  const health = usePoll<HealthInfo>("/health", 4000);
  const h = health.data;
  const mocked = h ? Object.entries(h.modules).filter(([, v]) => v.startsWith("mock")).map(([k]) => k) : [];
  const broken = h ? Object.keys(h.errors) : [];
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b border-slate-200 bg-white/95">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-2.5">
          <div className="text-lg font-bold tracking-tight">AeroTwin <span className="text-blue-600">AI</span></div>
          <span className="rounded-md bg-amber-100 px-2 py-0.5 text-xs font-bold tracking-wide text-amber-900" title="All data is synthetic: NASA C-MAPSS-style engine data plus a made-up fleet. Advisory only.">SIMULATED</span>
          {h?.mock && <span className="rounded-md bg-fuchsia-100 px-2 py-0.5 text-xs font-bold text-fuchsia-800" title="MOCK=1: every endpoint serves fixtures">MOCK API</span>}
          {!h?.mock && mocked.length > 0 && (
            <span className="rounded-md bg-fuchsia-100 px-2 py-0.5 text-xs font-semibold text-fuchsia-800" title={`Still on mock data: ${mocked.join(", ")}`}>{6 - mocked.length}/6 modules real</span>
          )}
          {broken.length > 0 && <span className="rounded-md bg-red-100 px-2 py-0.5 text-xs font-semibold text-red-800" title={JSON.stringify(h?.errors)}>import error: {broken.join(", ")}</span>}
          {health.error && !h && <span className="rounded-md bg-red-100 px-2 py-0.5 text-xs font-semibold text-red-800">API unreachable</span>}
          <nav className="ml-4 flex gap-1">
            {nav.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.to === "/"}
                className={({ isActive }) => `rounded-lg px-3 py-1.5 text-sm font-medium ${isActive ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-100"}`}>{n.label}</NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-xs text-slate-500">
            <span title="Simulation clock (UTC)">sim time {fmtZ(h?.sim_now)}</span>
            <button onClick={() => setOpen((o) => !o)} className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-semibold text-white hover:bg-blue-500">
              {open ? "Close copilot" : "Copilot & agents"}
            </button>
          </div>
        </div>
      </header>
      <main className={`mx-auto max-w-7xl px-4 py-5 transition-all ${open ? "lg:mr-[26rem]" : ""}`}>
        <Routes>
          <Route path="/" element={<Fleet />} />
          <Route path="/aircraft/:tail" element={<Aircraft />} />
          <Route path="/planning" element={<Planning />} />
          <Route path="/lab" element={<Lab />} />
        </Routes>
        <footer className="mt-8 border-t border-slate-200 pt-3 text-xs text-slate-500">
          Advisory prototype on simulated data. Not an airworthiness assessment; an authorized person approves every action.
        </footer>
      </main>
      <CopilotDrawer open={open} onClose={() => setOpen(false)} />
    </div>
  );
}
