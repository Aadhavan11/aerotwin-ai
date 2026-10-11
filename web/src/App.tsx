import { Suspense, lazy, useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { usePoll } from "./api/client";
import type { HealthInfo } from "./api/types";
import { fmtZ } from "./lib/format";
import AccountMenu from "./components/AccountMenu";
import AskBar from "./components/AskBar";
import Icon from "./components/Icon";
import Logo from "./components/Logo";
import { useAuth } from "./lib/auth";
import { useUnread } from "./lib/messages";
import { ROLES, useRole, type RoleId } from "./lib/role";
import { setSimNow } from "./lib/screen";
import Fleet from "./pages/Fleet";
const Aircraft = lazy(() => import("./pages/Aircraft"));
const Planning = lazy(() => import("./pages/Planning"));
const Lab = lazy(() => import("./pages/Lab"));
const Control = lazy(() => import("./pages/Control"));
const Maintenance = lazy(() => import("./pages/Maintenance"));
const FlightBrief = lazy(() => import("./pages/FlightBrief"));
const Dispatch = lazy(() => import("./pages/Dispatch"));
const Admin = lazy(() => import("./pages/Admin"));
const Copilot = lazy(() => import("./pages/Copilot"));
const Messages = lazy(() => import("./pages/Messages"));
const Extra = {
  Trips: lazy(() => import("./pages/Extra").then((m) => ({ default: m.Trips }))),
  Duty: lazy(() => import("./pages/Extra").then((m) => ({ default: m.Duty }))),
  Weather: lazy(() => import("./pages/Extra").then((m) => ({ default: m.Weather }))),
  Report: lazy(() => import("./pages/Extra").then((m) => ({ default: m.Report }))),
  CrewRoster: lazy(() => import("./pages/Extra").then((m) => ({ default: m.CrewRoster }))),
  Release: lazy(() => import("./pages/Extra").then((m) => ({ default: m.Release }))),
};
const wallFormat = new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", timeZoneName: "short" });

/** The real time where you are, ticking: the top bar shows it beside the simulation clock so the two are never confused. */
function useWallClock(): string {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return wallFormat.format(now);
}

export default function App() {
  const health = usePoll<HealthInfo>("/health", 4000);
  const location = useLocation();
  const navigate = useNavigate();
  const { role, setRole } = useRole();
  const auth = useAuth();
  const wall = useWallClock();
  const unread = useUnread(auth.status === "user");
  const h = health.data;
  // Land on the role's own home the first time the console opens at "/".
  useEffect(() => {
    if (location.pathname === "/" && role.home !== "/") navigate(role.home, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // The AI sees the simulation clock too: keep the screen-context helper current.
  useEffect(() => setSimNow(h?.sim_now), [h?.sim_now]);
  const modules = h ? Object.entries(h.modules) : [];
  const mocked = modules.filter(([, value]) => value.startsWith("mock"));
  const broken = h ? Object.keys(h.errors) : [];
  const real = modules.filter(([, value]) => value === "real").length;
  return (
    <div className="app">
      <header className="topbar">
        <NavLink to={role.home} className="brand" aria-label="AeroTwin home">
          <Logo />
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
          {auth.status === "user" ? (
            <AccountMenu />
          ) : (
            <>
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
            </>
          )}
          <span className="sim-pill" title="Every aircraft, flight and sensor value is simulated">
            Simulated
          </span>
          <span
            className="clock"
            title={health.error ? "Connection to the API lost" : "Simulation clock (UTC), not the real date. It starts at Oct 12 12:00Z and only moves when the simulation is advanced."}
          >
            <i className={`conn ${health.error ? "down" : ""}`} />
            <span className="clock-wall" title="The real time where you are">{wall}</span>
            <b className="clock-label">SIM</b>
            {fmtZ(h?.sim_now)}
          </span>
          {auth.status === "user" && (
            <NavLink to="/messages" className={`bell ${unread > 0 ? "has" : ""}`} aria-label={unread > 0 ? `${unread} unread messages` : "Messages"} title={unread > 0 ? `${unread} unread` : "Messages"}>
              <Icon name="bell" size={19} />
              {unread > 0 && <i>{unread > 9 ? "9+" : unread}</i>}
            </NavLink>
          )}
          <AskBar />
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
      <main className="page">
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
              <Route path="/copilot" element={<Copilot />} />
              <Route path="/trips" element={<Extra.Trips />} />
              <Route path="/duty" element={<Extra.Duty />} />
              <Route path="/weather" element={<Extra.Weather />} />
              <Route path="/report" element={<Extra.Report />} />
              <Route path="/crew" element={<Extra.CrewRoster />} />
              <Route path="/release" element={<Extra.Release />} />
              <Route path="/messages" element={<Messages />} />
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
    </div>
  );
}
