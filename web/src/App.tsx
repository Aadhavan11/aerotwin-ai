import { Suspense, lazy, useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { usePoll } from "./api/client";
import type { HealthInfo } from "./api/types";
import { fmtZ } from "./lib/format";
import CopilotDrawer from "./components/CopilotDrawer";
import Fleet from "./pages/Fleet";
const Aircraft = lazy(() => import("./pages/Aircraft"));
const Maintenance = lazy(() => import("./pages/Maintenance"));
const Planning = lazy(() => import("./pages/Planning"));
const Lab = lazy(() => import("./pages/Lab"));

const nav = [
  { to: "/", label: "Fleet" },
  { to: "/aircraft/SIM-017", label: "Aircraft", match: "/aircraft" },
  { to: "/maintenance", label: "Maintenance", match: "/maintenance" },
  { to: "/planning", label: "Planning" },
  { to: "/lab", label: "Scenarios" },
];

export default function App() {
  const [open, setOpen] = useState(false);
  const health = usePoll<HealthInfo>("/health", 4000);
  const location = useLocation();
  useEffect(() => {
    const openCopilot = () => setOpen(true);
    window.addEventListener("aerotwin:copilot", openCopilot);
    return () => window.removeEventListener("aerotwin:copilot", openCopilot);
  }, []);
  const h = health.data;
  const modules = h ? Object.entries(h.modules) : [];
  const mocked = modules.filter(([, value]) => value.startsWith("mock")).map(([k]) => k);
  const broken = h ? Object.keys(h.errors) : [];
  return (
    <div className="app">
      <header className="app-header">
        <NavLink to="/" className="app-brand">
          AeroTwin <small>maintenance planning</small>
        </NavLink>
        <nav className="app-nav" aria-label="Main">
          {nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/"}
              className={({ isActive }) => (isActive || (n.match && location.pathname.startsWith(n.match)) ? "active" : "")}
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="app-status">
          <span className="sim-tag" title="All aircraft, telemetry and results are simulated">
            SIMULATED
          </span>
          <span className={`app-conn ${health.error ? "down" : ""}`}>
            {health.error ? "API unreachable" : mocked.length ? `Mock: ${mocked.join(", ")}` : h ? "Live API" : "Connecting"}
          </span>
          <span className="app-clock" title="Simulation time, UTC">
            {fmtZ(h?.sim_now)}
          </span>
          <button className="app-agents" onClick={() => setOpen(!open)} aria-expanded={open}>
            {open ? "Close copilot" : "Copilot"}
          </button>
        </div>
      </header>
      {broken.length > 0 && <div className="app-alert">Module import errors: {broken.join(", ")}</div>}
      {h?.mock && <div className="app-notice">Fixture mode: scenarios and plans shown are mock outputs, not results.</div>}
      <main className={`app-main ${open ? "agents-open" : ""}`}>
        <Suspense fallback={<div className="page-loading">Loading…</div>}>
          <Routes>
            <Route path="/" element={<Fleet />} />
            <Route path="/aircraft/:tail" element={<Aircraft />} />
            <Route path="/maintenance" element={<Maintenance />} />
            <Route path="/maintenance/:tail" element={<Maintenance />} />
            <Route path="/planning" element={<Planning />} />
            <Route path="/lab" element={<Lab />} />
          </Routes>
        </Suspense>
        <footer className="app-footer">
          <span>Advisory prototype on simulated data (NASA C-MAPSS engines, synthetic fleet).</span>
          <span>Not certified for operational use. A person approves every maintenance action.</span>
        </footer>
      </main>
      <CopilotDrawer open={open} onClose={() => setOpen(false)} />
    </div>
  );
}
