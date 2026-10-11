import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

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
  brief: { to: "/brief", label: "Aircraft readiness brief", icon: "shield" },
  departures: { to: "/departures", label: "Departures & impact", icon: "fleet" },
  admin: { to: "/admin", label: "System & audit", icon: "search" },
} satisfies Record<string, NavItem>;

export const ROLES: Role[] = [
  {
    id: "ops",
    label: "Operations control",
    blurb: "Fleet health, decisions waiting, the plan, and the risk of waiting.",
    home: "/",
    canDecide: true,
    nav: [N.fleet, N.control, N.twin, N.plan, N.lab],
  },
  {
    id: "maintenance",
    label: "Maintenance planner",
    blurb: "Work orders to approve, technicians, parts and hangar slots, and the jobs planned at each station.",
    home: "/workorders",
    canDecide: true,
    nav: [N.workorders, N.plan, N.twin, N.control],
  },
  {
    id: "pilot",
    label: "Pilot / flight crew",
    blurb: "A readiness brief for one aircraft: what the twin sees, planned maintenance, and the next legs.",
    home: "/brief",
    canDecide: false,
    nav: [N.brief, N.twin],
  },
  {
    id: "dispatch",
    label: "Crew & dispatch",
    blurb: "Upcoming departures, which flights touch a maintenance window, and aircraft that need attention.",
    home: "/departures",
    canDecide: false,
    nav: [N.departures, N.plan, N.fleet],
  },
  {
    id: "admin",
    label: "Administrator",
    blurb: "Service health, data and model quality, the full audit trail, and scenario controls.",
    home: "/admin",
    canDecide: false,
    nav: [N.admin, N.lab, N.fleet],
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
  const [id, setId] = useState<RoleId>(readRole);
  const setRole = useCallback((next: RoleId) => {
    setId(next);
    try {
      localStorage.setItem(KEY, next);
    } catch {
      /* ignore */
    }
  }, []);
  const value = useMemo(() => ({ role: ROLES.find((r) => r.id === id) ?? ROLES[0], setRole }), [id, setRole]);
  return <RoleContext.Provider value={value}>{children}</RoleContext.Provider>;
}

export function useRole(): Ctx {
  const c = useContext(RoleContext);
  if (!c) throw new Error("useRole must be used inside RoleProvider");
  return c;
}

/** The name shown as "Acting as" in the agent workspace; used as the approver on work orders. */
export const actorName = (): string => {
  try {
    return localStorage.getItem("aerotwin.user") || "Shamit";
  } catch {
    return "Shamit";
  }
};

/** Open the agent / copilot workspace drawer from any page. */
export const openAgentWorkspace = () => window.dispatchEvent(new Event("aerotwin:open-agents"));
