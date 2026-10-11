import { Suspense, lazy, useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { usePoll } from "./api/client";
import type { HealthInfo } from "./api/types";
import { fmtZ } from "./lib/format";
import CopilotDrawer from "./components/CopilotDrawer";
import Icon from "./components/Icon";
import { ROLES, useRole, type RoleId } from "./lib/role";
import Fleet from "./pages/Fleet";
const Aircraft = lazy(() => import("./pages/Aircraft"));
const Planning = lazy(() => import("./pages/Planning"));
const Lab = lazy(() => import("./pages/Lab"));
const Control = lazy(() => import("./pages/Control"));
const Maintenance = lazy(() => import("./pages/Maintenance"));
const FlightBrief = lazy(() => import("./pages/FlightBrief"));
const Dispatch = lazy(() => import("./pages/Dispatch"));
const Admin = lazy(() => import("./pages/Admin"));
export default function App() {
  const [open, setOpen] = useState(false);
  const health = usePoll<HealthInfo>("/health", 4000);
  const location = useLocation();
  const navigate = useNavigate();
  const { role, setRole } = useRole();
  const h = health.data;
  // Land on the role's own home the first time the console opens at "/".
  useEffect(() => {
    if (location.pathname === "/" && role.home !== "/") navigate(role.home, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Any page can open the agent workspace with window.dispatchEvent(new Event("aerotwin:open-agents")).
  useEffect(() => {
    const openIt = () => setOpen(true);
    window.addEventListener("aerotwin:open-agents", openIt);
    return () => window.removeEventListener("aerotwin:open-agents", openIt);
  }, []);
  const modules = h ? Object.entries(h.modules) : [];
  const mocked = modules.filter(([, value]) => value.startsWith("mock"));
  const broken = h ? Object.keys(h.errors) : [];
  const titles: [string, string][] = [
    ["/aircraft", "Aircraft digital twin"],
    ["/planning", "Maintenance planning"],
    ["/lab", "Scenario simulation"],
    ["/control", "Control room"],
    ["/workorders", "Work orders and resources"],
    ["/brief", "Aircraft readiness brief"],
    ["/departures", "Departures and impact"],
    ["/admin", "System and audit"],
  ];
  const title = titles.find(([prefix]) => location.pathname.startsWith(prefix))?.[1] ?? "Fleet operations";
  return (
    <div className="console-shell">
      <aside className="console-sidebar">
        <NavLink to={role.home} className="console-brand">
          <div className="brand-mark">
            <Icon name="aircraft" size={28} />
          </div>
          <div>
            AEROTWIN<span>INTELLIGENCE SYSTEMS</span>
          </div>
        </NavLink>
        <div className="role-switch">
          <label htmlFor="role-select" className="sidebar-label">
            VIEWING AS
          </label>
          <select
            id="role-select"
            value={role.id}
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
          <p>{role.blurb}</p>
        </div>
        <nav className="console-navigation" aria-label={role.label}>
          {role.nav.map((n, idx) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/"}
              className={({ isActive }) =>
                `console-nav ${isActive || (n.to.startsWith("/aircraft") && location.pathname.startsWith("/aircraft")) ? "active" : ""}`
              }
            >
              <Icon name={n.icon} />
              <span>{n.label}</span>
              <small>{String(idx + 1).padStart(2, "0")}</small>
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
            {role.label.toUpperCase()} <span>/</span> <strong>{title}</strong>
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
                <Route path="/control" element={<Control />} />
                <Route path="/workorders" element={<Maintenance />} />
                <Route path="/brief" element={<FlightBrief />} />
                <Route path="/departures" element={<Dispatch />} />
                <Route path="/admin" element={<Admin />} />
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
