import { Suspense, lazy, useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { usePoll } from "./api/client";
import type { HealthInfo } from "./api/types";
import { fmtZ } from "./lib/format";
import CopilotDrawer from "./components/CopilotDrawer";
import { ROLES, useRole, type RoleId } from "./lib/role";
import Fleet from "./pages/Fleet";
const Aircraft = lazy(() => import("./pages/Aircraft"));
const Maintenance = lazy(() => import("./pages/Maintenance"));
const Planning = lazy(() => import("./pages/Planning"));
const Lab = lazy(() => import("./pages/Lab"));
const Control = lazy(() => import("./pages/Control"));
const WorkOrders = lazy(() => import("./pages/WorkOrders"));
const FlightBrief = lazy(() => import("./pages/FlightBrief"));
const Dispatch = lazy(() => import("./pages/Dispatch"));
const Admin = lazy(() => import("./pages/Admin"));
const Insights = lazy(() => import("./pages/Insights"));

// Short names for the top bar; the role's own longer labels are used as tooltips.
const SHORT: Record<string, string> = {
  "/": "Fleet",
  "/control": "Control room",
  "/aircraft/SIM-017": "Aircraft",
  "/maintenance": "Maintenance",
  "/planning": "Planning",
  "/lab": "Scenarios",
  "/workorders": "Work orders",
  "/brief": "Readiness brief",
  "/departures": "Departures",
  "/admin": "System & audit",
  "/insights": "Insights",
};

export default function App() {
  const [open, setOpen] = useState(false);
  const health = usePoll<HealthInfo>("/health", 4000);
  const location = useLocation();
  const navigate = useNavigate();
  const { role, setRole } = useRole();
  // Land on the role's own home the first time the console opens at "/".
  useEffect(() => {
    if (location.pathname === "/" && role.home !== "/") navigate(role.home, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    const openIt = () => setOpen(true);
    window.addEventListener("aerotwin:copilot", openIt);
    window.addEventListener("aerotwin:open-agents", openIt);
    return () => {
      window.removeEventListener("aerotwin:copilot", openIt);
      window.removeEventListener("aerotwin:open-agents", openIt);
    };
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
          {[...role.nav, { to: "/insights", label: "Insights" }].map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/"}
              title={n.label}
              className={({ isActive }) =>
                isActive || (n.to.startsWith("/aircraft") && location.pathname.startsWith("/aircraft")) || (n.to === "/maintenance" && location.pathname.startsWith("/maintenance"))
                  ? "active"
                  : ""
              }
            >
              {SHORT[n.to] ?? n.label}
            </NavLink>
          ))}
        </nav>
        <div className="app-status">
          <label className="sr-only" htmlFor="role-select">
            Viewing as
          </label>
          <select
            id="role-select"
            className="select role-select"
            value={role.id}
            title={role.blurb}
            onChange={(e) => {
              const next = ROLES.find((r) => r.id === (e.target.value as RoleId));
              setRole(e.target.value as RoleId);
              if (next) navigate(next.home);
            }}
          >
            {ROLES.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
          </select>
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
            <Route path="/control" element={<Control />} />
            <Route path="/workorders" element={<WorkOrders />} />
            <Route path="/brief" element={<FlightBrief />} />
            <Route path="/departures" element={<Dispatch />} />
            <Route path="/admin" element={<Admin />} />
            <Route path="/insights" element={<Insights />} />
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
