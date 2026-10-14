import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import { useAuth } from "./auth";

/**
 * Who is looking at the console. A role changes the landing page, the navigation, and whether the person may approve maintenance.
 * It is a view preference for this prototype, not authentication: the API does not check it.
 */
export type RoleId = "ops" | "maintenance" | "pilot" | "dispatch" | "admin";
export type NavItem = { to: string; label: string; icon: string };
export type Role = {
  id: RoleId;
  label: string;
  blurb: string;
  home: string;
  /** may approve or reject work orders (maintenance control and maintenance planners only) */
  canDecide: boolean;
  nav: NavItem[];
};

const N = {
  fleet: { to: "/", label: "Fleet overview", icon: "fleet" },
  control: { to: "/control", label: "Control room", icon: "pulse" },
  twin: { to: "/aircraft/SIM-017", label: "Aircraft twin", icon: "aircraft" },
  plan: { to: "/planning", label: "Maintenance plan", icon: "planning" },
  lab: { to: "/lab", label: "Simulation lab", icon: "lab" },
  workorders: { to: "/workorders", label: "Work orders & resources", icon: "agents" },
  brief: { to: "/brief", label: "Readiness brief", icon: "shield" },
  departures: { to: "/departures", label: "Departures", icon: "fleet" },
  admin: { to: "/admin", label: "System & audit", icon: "search" },
  trips: { to: "/trips", label: "My trips", icon: "fleet" },
  duty: { to: "/duty", label: "Duty & rest", icon: "pulse" },
  weather: { to: "/weather", label: "Weather", icon: "pulse" },
  report: { to: "/report", label: "Report a defect", icon: "shield" },
  crew: { to: "/crew", label: "Crew roster", icon: "agents" },
  release: { to: "/release", label: "Flight release", icon: "shield" },
  messages: { to: "/messages", label: "Messages", icon: "search" },
  insights: { to: "/insights", label: "Insights", icon: "search" },
  reliability: { to: "/reliability", label: "Reliability", icon: "pulse" },
  runs: { to: "/runs", label: "Runs", icon: "lab" },
  llp: { to: "/llp", label: "Life-limited parts", icon: "pulse" },
  mel: { to: "/mel", label: "MEL register", icon: "shield" },
  risk: { to: "/risk", label: "Plan risk", icon: "search" },
  copilot: { to: "/copilot", label: "Copilot", icon: "agents" },
} satisfies Record<string, NavItem>;

export const ROLES: Role[] = [
  {
    id: "ops",
    label: "Operations control",
    blurb: "Fleet health, decisions waiting, the plan, and the risk of waiting.",
    home: "/",
    canDecide: true,
    nav: [N.fleet, N.control, N.twin, N.plan, N.insights, N.reliability, N.runs, N.copilot, N.lab, N.llp, N.mel, N.risk],
  },
  {
    id: "maintenance",
    label: "Maintenance planner",
    blurb: "Work orders to approve, technicians, parts and hangar slots, and the jobs planned at each station.",
    home: "/workorders",
    canDecide: true,
    nav: [N.workorders, N.plan, N.twin, N.control, N.insights, N.reliability, N.copilot, N.llp, N.mel, N.risk],
  },
  {
    id: "pilot",
    label: "Pilot / flight crew",
    blurb: "A readiness brief for one aircraft: what the twin sees, planned maintenance, and the next legs.",
    home: "/brief",
    canDecide: false,
    nav: [N.brief, N.trips, N.duty, N.weather, N.twin, N.report, N.copilot],
  },
  {
    id: "dispatch",
    label: "Crew & dispatch",
    blurb: "Upcoming departures, which flights touch a maintenance window, and aircraft that need attention.",
    home: "/departures",
    canDecide: false,
    nav: [N.departures, N.release, N.crew, N.weather, N.messages, N.plan, N.copilot, N.mel],
  },
  {
    id: "admin",
    label: "Administrator",
    blurb: "Service health, data and model quality, the full audit trail, and scenario controls.",
    home: "/admin",
    canDecide: false,
    nav: [N.admin, N.lab, N.fleet, N.insights, N.reliability, N.runs, N.copilot, N.llp, N.mel, N.risk],
  },
];

const KEY = "aerotwin.role";
const readRole = (): RoleId => {
  try {
    const v = localStorage.getItem(KEY) as RoleId | null;
    if (v && ROLES.some((r) => r.id === v)) return v;
  } catch {
    /* storage can be unavailable */
  }
  return "ops";
};

type Ctx = { role: Role; setRole: (id: RoleId) => void };
const RoleContext = createContext<Ctx | null>(null);

export function RoleProvider({ children }: { children: ReactNode }) {
  const auth = useAuth();
  const [id, setId] = useState<RoleId>(readRole);
  const signedIn = auth.status === "user" && auth.user;
  // With sign-in on, the position comes from the server (a master account may switch it); otherwise it is a view preference.
  const effective: RoleId = signedIn ? (auth.user!.role as RoleId) : id;
  const switchRole = auth.switchRole;
  const setRole = useCallback(
    (next: RoleId) => {
      if (signedIn) {
        void switchRole(next);
        return;
      }
      setId(next);
      try {
        localStorage.setItem(KEY, next);
      } catch {
        /* ignore */
      }
    },
    [signedIn, switchRole],
  );
  const value = useMemo(() => ({ role: ROLES.find((r) => r.id === effective) ?? ROLES[0], setRole }), [effective, setRole]);
  return <RoleContext.Provider value={value}>{children}</RoleContext.Provider>;
}

export function useRole(): Ctx {
  const c = useContext(RoleContext);
  if (!c) throw new Error("useRole must be used inside RoleProvider");
  return c;
}

export { actorName, setActor } from "./actor";

/** Open the top-bar ask box from anywhere, optionally with a question typed in (and sent). */
export const openAsk = (text?: string, send = false) => window.dispatchEvent(new CustomEvent("aerotwin:open-ask", { detail: { text, send } }));
