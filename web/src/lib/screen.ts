/** What the person is looking at, sent with every copilot question and agent run so the AI can see the screen (see app/copilot/screen.py). */
export type ScreenContext = {
  page: string;
  path: string;
  role: string;
  tail: string | null;
  selected: string | null;
  sim_now: string | null;
  text: string;
};

const PAGE_NAMES: [string, string][] = [
  ["/aircraft", "Aircraft twin"],
  ["/planning", "Maintenance plan"],
  ["/lab", "Simulation lab"],
  ["/control", "Control room"],
  ["/workorders", "Work orders and resources"],
  ["/brief", "Aircraft readiness brief"],
  ["/departures", "Departures and maintenance windows"],
  ["/admin", "System and audit"],
];

// Buttons that are view controls rather than "the selected thing"
const VIEW_CONTROLS = /^(3\/4 view|top|side|rotate|both|optimized|baseline|pending|approved|rejected|all|sensor trends|health history)\b/i;

export function collectScreen(pathname: string, search: string, role: string, simNow?: string): ScreenContext {
  const base = import.meta.env.BASE_URL.replace(/\/$/, "");
  const path = base && pathname.startsWith(base) ? pathname.slice(base.length) || "/" : pathname;
  const main = document.querySelector("main.page") as HTMLElement | null;
  const text = (main?.innerText ?? "").replace(/[ \t]+/g, " ").replace(/\n{2,}/g, "\n").trim().slice(0, 3000);
  const pressed = [...(main?.querySelectorAll('button[aria-pressed="true"]') ?? [])]
    .map((b) => ((b as HTMLElement).innerText ?? b.textContent ?? "").replace(/\s+/g, " ").trim())
    .filter((t) => t && !VIEW_CONTROLS.test(t));
  const tail = path.match(/\/aircraft\/([A-Za-z0-9-]+)/)?.[1] ?? new URLSearchParams(search).get("tail");
  return {
    page: PAGE_NAMES.find(([p]) => path.startsWith(p))?.[1] ?? "Fleet overview",
    path,
    role,
    tail: tail ? tail.toUpperCase() : null,
    selected: pressed.length ? pressed.join(" · ") : null,
    sim_now: simNow ?? null,
    text,
  };
}
