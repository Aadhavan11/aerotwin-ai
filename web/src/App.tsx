import { Suspense, lazy, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { usePoll } from "./api/client";
import type { HealthInfo } from "./api/types";
import { fmtZ } from "./lib/format";
import CopilotDrawer from "./components/CopilotDrawer";
import Icon from "./components/Icon";
import Fleet from "./pages/Fleet";
const Aircraft = lazy(() => import("./pages/Aircraft"));
const Planning = lazy(() => import("./pages/Planning"));
const Lab = lazy(() => import("./pages/Lab"));
const nav = [
  { to: "/", label: "Fleet overview", icon: "fleet", code: "01" },
  {
    to: "/aircraft/SIM-017",
    label: "Aircraft twin",
    icon: "aircraft",
    code: "02",
  },
  { to: "/planning", label: "Maintenance", icon: "planning", code: "03" },
  { to: "/lab", label: "Simulation lab", icon: "lab", code: "04" },
];

export default function App() {
  const [open, setOpen] = useState(false);
  const health = usePoll<HealthInfo>("/health", 4000);
  const location = useLocation();
  const h = health.data;
  const modules = h ? Object.entries(h.modules) : [];
  const mocked = modules.filter(([, value]) => value.startsWith("mock"));
  const broken = h ? Object.keys(h.errors) : [];
  const title = location.pathname.startsWith("/aircraft")
    ? "Aircraft digital twin"
    : location.pathname.startsWith("/planning")
      ? "Maintenance planning"
      : location.pathname.startsWith("/lab")
        ? "Scenario simulation"
        : "Fleet operations";
  return (
    <div className="console-shell">
      <aside className="console-sidebar">
        <NavLink to="/" className="console-brand">
          <div className="brand-mark">
            <Icon name="aircraft" size={28} />
          </div>
          <div>
            AEROTWIN<span>INTELLIGENCE SYSTEMS</span>
          </div>
        </NavLink>
        <div className="sidebar-label">OPERATIONS WORKSPACE</div>
        <nav className="console-navigation">
          {nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/"}
              className={({ isActive }) =>
                `console-nav ${isActive || (n.icon === "aircraft" && location.pathname.startsWith("/aircraft")) ? "active" : ""}`
              }
            >
              <Icon name={n.icon} />
              <span>{n.label}</span>
              <small>{n.code}</small>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-module">
          <div className="sidebar-label">SERVICE STATUS</div>
          {modules.map(([key, value]) => (
            <div key={key} title={value}>
              <span
                className={`module-light ${value === "real" ? "real" : "mock"}`}
              />
              <span>{key.replace("whatif", "what-if")}</span>
              <small>{value === "real" ? "ONLINE" : "MOCK"}</small>
            </div>
          ))}
        </div>
        <div className="sidebar-bottom">
          <Icon name="shield" />
          <p>
            SIMULATED ENVIRONMENT<span>Human approval required</span>
          </p>
          <div className="sidebar-version">AEROTWIN / {h?.version ?? "—"}</div>
        </div>
      </aside>
      <div className="console-workspace">
        <header className="console-header">
          <div className="console-breadcrumb">
            OPERATIONS <span>/</span> <strong>{title}</strong>
          </div>
          <div className="header-status">
            <span className="simulation-badge">SIMULATED</span>
            <span
              className={`connection-indicator ${health.error ? "disconnected" : ""}`}
            >
              <i />
              {health.error
                ? "CONNECTION LOST"
                : h
                  ? "API CONNECTED"
                  : "CONNECTING"}
            </span>
            <span className="header-clock">
              <small>SIM TIME / UTC</small>
              {fmtZ(h?.sim_now)}
            </span>
            <button className="agent-trigger" onClick={() => setOpen(!open)}>
              <Icon name="agents" size={18} />
              <span>{open ? "Close agents" : "Agent workspace"}</span>
            </button>
          </div>
        </header>
        {broken.length > 0 && (
          <div className="console-alert">
            Module import errors: {broken.join(", ")}
          </div>
        )}
        {h?.mock && (
          <div className="console-notice">
            Fixture mode · displayed scenarios and plans are mock outputs
          </div>
        )}
        <main className={`console-main ${open ? "agents-open" : ""}`}>
          <Suspense
            fallback={
              <div className="console-loading">
                <Icon name="pulse" />
                Loading workspace…
              </div>
            }
          >
            <div key={location.pathname} className="page-enter">
              <Routes>
                <Route path="/" element={<Fleet />} />
                <Route path="/aircraft/:tail" element={<Aircraft />} />
                <Route path="/planning" element={<Planning />} />
                <Route path="/lab" element={<Lab />} />
              </Routes>
            </div>
          </Suspense>
          <footer className="console-footer">
            <span>ADVISORY PROTOTYPE · SIMULATED DATA</span>
            <span>
              No airworthiness certification · every action requires human
              approval
            </span>
          </footer>
        </main>
      </div>
      <CopilotDrawer open={open} onClose={() => setOpen(false)} />
    </div>
  );
}
