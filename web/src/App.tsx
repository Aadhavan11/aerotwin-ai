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
  const real = modules.filter(([, value]) => value === "real").length;
  return (
    <div className="app">
      <header className="topbar">
        <NavLink to={role.home} className="brand" aria-label="AeroTwin home">
          <span className="brand-mark">AT</span>
          <span className="brand-name">AeroTwin</span>
        </NavLink>
        <nav className="capsule" aria-label={role.label}>
          {role.nav.map((n) => (
            <NavLink
              key={n.to}
              to={n.to}
              end={n.to === "/"}
              className={({ isActive }) => (isActive || (n.to.startsWith("/aircraft") && location.pathname.startsWith("/aircraft")) ? "active" : "")}
            >
              {n.label}
            </NavLink>
          ))}
        </nav>
        <div className="topbar-right">
          <label className="sr-only" htmlFor="role-select">
            Viewing as
          </label>
          <select
            id="role-select"
            className="role-select"
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
          <span className="sim-pill" title="Every aircraft, flight and sensor value is simulated">
            Simulated
          </span>
          <span className="clock" title={health.error ? "Connection to the API lost" : "Connected to the API"}>
            <i className={`conn ${health.error ? "down" : ""}`} />
            {fmtZ(h?.sim_now)}
          </span>
          <button className="agent-trigger" onClick={() => setOpen(!open)}>
            <Icon name="agents" size={16} />
            <span>{open ? "Close agents" : "Agents"}</span>
          </button>
        </div>
      </header>
      {broken.length > 0 && <div className="console-alert">Module import errors: {broken.join(", ")}</div>}
      {h?.ml_data && !h.ml_data.startsWith("nasa") && (
        <div className="console-alert">
          The NASA engine data is missing on this machine, so remaining-life numbers come from a synthetic stand-in and the demo will not behave as designed. Run
          {" "}python -m app.ml.cmapss --download, then restart the API.
        </div>
      )}
      {h?.mock && <div className="console-notice">Fixture mode: scenarios and plans shown here are mock outputs</div>}
      <main className={`page ${open ? "agents-open" : ""}`}>
        <Suspense
          fallback={
            <div className="console-loading">
              <Icon name="pulse" />
              Loading…
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
        <footer className="page-foot">
          <span>
            Advisory prototype on simulated data. Not certified for operational use; a person approves every maintenance action.
            {import.meta.env.VITE_PUBLIC_DEMO ? " This is a shared public demo: every visitor shares one simulation, so reset it under System if it looks different." : ""}
          </span>
          <span>
            {h ? `${real} of ${modules.length} services running real code${mocked.length ? ` (${mocked.map(([k]) => k).join(", ")} mocked)` : ""}` : "connecting"} ·{" "}
            <NavLink to="/admin">system</NavLink> · v{h?.version ?? "?"}
          </span>
        </footer>
      </main>
      <CopilotDrawer open={open} onClose={() => setOpen(false)} />
    </div>
  );
}
