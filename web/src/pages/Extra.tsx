import { useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { usePoll } from "../api/client";
import type { FleetOverview, HealthInfo, Leg, PlanBundle } from "../api/types";
import AskPanel from "../components/AskPanel";
import { Btn, Card, Chip, ErrorBox, Kpi, Loading, StatusPill } from "../components/ui";
import { hoursText, ms } from "../lib/derive";
import { fmtZ } from "../lib/format";

/**
 * Pages for the crew and dispatch positions beyond the maintenance views. "My trips" and "Flight release" run on the live simulation
 * (legs, aircraft status, the plan). The rest are previews of where the product goes: they show sample data and are not connected
 * to the planner, and each one says so.
 */
const Preview = ({ what = "Sample data" }: { what?: string }) => (
  <span className="preview-chip" title="This page shows sample data; it is not connected to the planner">
    Preview · {what}
  </span>
);

function Heading({ eyebrow, title, children, preview }: { eyebrow: string; title: string; children?: ReactNode; preview?: string }) {
  return (
    <div className="page-heading">
      <div>
        <div className="eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        {children && <p>{children}</p>}
      </div>
      {preview && <Preview what={preview} />}
    </div>
  );
}

const Bar = ({ value, max, tone = "ok" }: { value: number; max: number; tone?: "ok" | "warn" | "bad" }) => (
  <div className="meter" role="img" aria-label={`${value} of ${max}`}>
    <span className={tone} style={{ width: `${Math.min(100, (value / max) * 100)}%` }} />
  </div>
);

const STATIONS = ["ATL", "DEN", "DFW", "JFK", "LAX", "MIA", "ORD", "SFO"];

// ---------------------------------------------------------------- My trips (live)
export function Trips() {
  const legs = usePoll<Leg[]>("/legs", 15000);
  const fleet = usePoll<FleetOverview>("/fleet", 5000);
  const health = usePoll<HealthInfo>("/health", 5000);
  const [tail, setTail] = useState("");
  const now = health.data?.sim_now;
  if (!legs.data || !fleet.data || !now) return legs.error || fleet.error ? <ErrorBox error={legs.error ?? fleet.error} /> : <Loading what="your trips" />;
  const aircraft = [...fleet.data.aircraft].sort((a, b) => a.tail.localeCompare(b.tail));
  const mine = tail || aircraft.find((a) => legs.data!.some((l) => l.tail === a.tail && ms(l.sched_dep) > ms(now)))?.tail || aircraft[0]?.tail || "";
  const a = aircraft.find((x) => x.tail === mine);
  const t0 = ms(now);
  const trips = legs.data.filter((l) => l.tail === mine && ms(l.sched_arr) > t0 && ms(l.sched_dep) < t0 + 72 * 3_600_000).sort((x, y) => ms(x.sched_dep) - ms(y.sched_dep));
  const block = trips.reduce((s, l) => s + (ms(l.sched_arr) - ms(l.sched_dep)) / 3_600_000, 0);
  return (
    <div className="space-y-5">
      <Heading eyebrow="My trips" title="Your next 72 hours">
        The legs assigned to the aircraft you are flying, from the simulation clock. Pick another aircraft to see its rotation.
      </Heading>
      <AskPanel placeholder="Ask about your trips or your aircraft…" suggestions={["Is my aircraft ready to fly?", "What is the next departure for this aircraft?", "Does maintenance touch any of my legs?"]} />
      <div className="facts">
        <Kpi label="Aircraft" value={mine} hint={a ? `${a.type} · ${a.location === "AIR" ? "in flight" : `at ${a.location}`}` : ""} />
        <Kpi label="Legs, next 72 h" value={trips.length} />
        <Kpi label="Block time" value={hoursText(block)} hint="Scheduled, gate to gate" />
        <Kpi label="Aircraft status" value={a ? a.status.replace("_", " ") : "-"} tone={a?.status === "ok" ? "emerald" : "amber"} hint="From the twin" />
      </div>
      <Card
        title="Legs"
        right={
          <label className="flex items-center gap-2 text-xs text-slate-500">
            Aircraft
            <select value={mine} onChange={(e) => setTail(e.target.value)} className="rounded-md border border-slate-300 bg-transparent px-2 py-1 text-sm text-slate-800">
              {aircraft.map((x) => (
                <option key={x.tail} value={x.tail}>
                  {x.tail} · {x.status.replace("_", " ")}
                </option>
              ))}
            </select>
          </label>
        }
      >
        {trips.length === 0 ? (
          <p className="text-sm text-slate-500">No legs for this aircraft in the next 72 hours (it may be in maintenance).</p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr>
                <th className="py-2">Flight</th>
                <th>Route</th>
                <th>Departs (UTC)</th>
                <th>Arrives (UTC)</th>
                <th className="text-right">Block</th>
              </tr>
            </thead>
            <tbody>
              {trips.map((l) => (
                <tr key={l.id} className="border-t border-slate-200">
                  <td className="py-2 font-mono">{l.id}</td>
                  <td className="font-mono">
                    {l.dep} → {l.arr}
                  </td>
                  <td className="tabular-nums">{fmtZ(l.sched_dep)}</td>
                  <td className="tabular-nums">{fmtZ(l.sched_arr)}</td>
                  <td className="text-right tabular-nums">{hoursText((ms(l.sched_arr) - ms(l.sched_dep)) / 3_600_000)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="mt-3 text-xs text-slate-500">
          <Link className="text-blue-600 hover:underline" to={`/brief?tail=${mine}`}>
            Open the readiness brief for {mine}
          </Link>
        </p>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- Duty & rest (preview)
const DUTY_DAYS: [string, number, number, string][] = [
  ["Mon", 5.1, 9.4, "ATL–DEN–ATL"],
  ["Tue", 6.3, 10.8, "ATL–DFW–MIA"],
  ["Wed", 0, 0, "Off"],
  ["Thu", 4.2, 8.1, "MIA–ATL"],
  ["Fri", 7.4, 12.2, "ATL–JFK–ATL"],
  ["Sat", 5.8, 10.1, "ATL–ORD–ATL"],
  ["Sun", 3.1, 6.2, "ATL–DFW"],
];
export function Duty() {
  const flown = DUTY_DAYS.reduce((s, d) => s + d[1], 0);
  return (
    <div className="space-y-5">
      <Heading eyebrow="Duty and rest" title="Your hours against the limits" preview="Sample crew member">
        Flight time, duty time and rest before your next duty. A real deployment reads these from the crew system and checks every assignment against the rules.
      </Heading>
      <div className="facts">
        <Kpi label="Flight time, last 24 h" value="3.1 h" hint="Limit 9 h" tone="emerald" />
        <Kpi label="Duty today" value="6.2 h" hint="Limit 14 h" tone="emerald" />
        <Kpi label="Rest before next duty" value="11.5 h" hint="Minimum 10 h" tone="emerald" />
        <Kpi label="Flight time, 7 days" value={`${flown.toFixed(1)} h`} hint="Limit 30 h" tone="amber" />
      </div>
      <Card title="Last seven days">
        <table className="w-full text-left text-sm">
          <thead>
            <tr>
              <th className="py-2">Day</th>
              <th>Pairing</th>
              <th className="text-right">Flight</th>
              <th className="text-right">Duty</th>
              <th className="w-1/3 pl-6">Against the 9 h flight limit</th>
            </tr>
          </thead>
          <tbody>
            {DUTY_DAYS.map(([d, f, du, p]) => (
              <tr key={d} className="border-t border-slate-200">
                <td className="py-2 font-medium">{d}</td>
                <td className="font-mono text-slate-600">{p}</td>
                <td className="text-right tabular-nums">{f ? `${f.toFixed(1)} h` : "-"}</td>
                <td className="text-right tabular-nums">{du ? `${du.toFixed(1)} h` : "-"}</td>
                <td className="pl-6">
                  <Bar value={f} max={9} tone={f > 7 ? "warn" : "ok"} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- Weather & NOTAMs (preview)
const WX: Record<string, [string, number, number, string, string]> = {
  // station: [wind, visibility sm, ceiling ft, category, remark]
  ATL: ["230° 9 kt", 10, 4500, "VFR", "Scattered clouds"],
  DEN: ["320° 18 kt G27", 10, 9000, "VFR", "Gusty crosswind runway 17"],
  DFW: ["170° 12 kt", 6, 2200, "MVFR", "Light haze"],
  JFK: ["040° 14 kt", 3, 800, "IFR", "Rain, low ceiling"],
  LAX: ["260° 7 kt", 8, 1800, "MVFR", "Marine layer clearing by 16Z"],
  MIA: ["110° 10 kt", 10, 3500, "VFR", "Isolated showers vicinity"],
  ORD: ["290° 16 kt", 7, 3000, "VFR", "Winds picking up"],
  SFO: ["280° 11 kt", 5, 1200, "MVFR", "Fog dissipating"],
};
const NOTAMS: [string, string, string][] = [
  ["JFK", "RWY 04R/22L closed 0600–1000Z for resurfacing", "Plan 04L/22R"],
  ["DEN", "ILS RWY 16R out of service", "Use RNAV approach"],
  ["ORD", "Taxiway K closed between K3 and K5", "Expect extended taxi"],
  ["LAX", "Crane 190 ft AGL 1.2 nm south of RWY 25L", "Obstacle lit"],
];
const CAT_TONE: Record<string, string> = { VFR: "text-emerald-600", MVFR: "text-blue-600", IFR: "text-red-600" };
export function Weather() {
  const worst = STATIONS.filter((s) => WX[s][3] === "IFR");
  return (
    <div className="space-y-5">
      <Heading eyebrow="Weather and NOTAMs" title="Conditions at your stations" preview="Sample weather">
        Current conditions and notices for the eight stations. This is where the climate-aware routing reads its inputs; here the values are samples, not a live feed.
      </Heading>
      <div className="facts">
        <Kpi label="Stations" value={STATIONS.length} />
        <Kpi label="IFR" value={worst.length} tone={worst.length ? "red" : "emerald"} hint={worst.join(", ") || "none"} />
        <Kpi label="Active NOTAMs" value={NOTAMS.length} tone="amber" />
      </div>
      <div className="grid gap-4 xl:grid-cols-[1.3fr_1fr]">
        <Card title="Station conditions">
          <table className="w-full text-left text-sm">
            <thead>
              <tr>
                <th className="py-2">Station</th>
                <th>Wind</th>
                <th className="text-right">Vis (sm)</th>
                <th className="text-right">Ceiling (ft)</th>
                <th>Category</th>
                <th>Remark</th>
              </tr>
            </thead>
            <tbody>
              {STATIONS.map((s) => (
                <tr key={s} className="border-t border-slate-200">
                  <td className="py-2 font-mono font-semibold">{s}</td>
                  <td className="tabular-nums">{WX[s][0]}</td>
                  <td className="text-right tabular-nums">{WX[s][1]}</td>
                  <td className="text-right tabular-nums">{WX[s][2].toLocaleString()}</td>
                  <td className={`font-semibold ${CAT_TONE[WX[s][3]]}`}>{WX[s][3]}</td>
                  <td className="text-slate-500">{WX[s][4]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="NOTAMs">
          <ul className="space-y-3 text-sm">
            {NOTAMS.map(([s, t, n]) => (
              <li key={t} className="border-l-2 border-slate-200 pl-3">
                <b className="font-mono">{s}</b> <span className="text-slate-700">{t}</span>
                <div className="text-xs text-slate-500">{n}</div>
              </li>
            ))}
          </ul>
        </Card>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Report a defect (preview)
export function Report() {
  const fleet = usePoll<FleetOverview>("/fleet", 20000);
  const [form, setForm] = useState({ tail: "", system: "Engine", severity: "Advisory", text: "" });
  const [sent, setSent] = useState(false);
  const aircraft = [...(fleet.data?.aircraft ?? [])].sort((a, b) => a.tail.localeCompare(b.tail));
  const set = (k: keyof typeof form, v: string) => setForm((f) => ({ ...f, [k]: v }));
  return (
    <div className="space-y-5">
      <Heading eyebrow="Report a defect" title="Tell maintenance what you saw" preview="Not connected">
        A pilot report goes to maintenance control with the aircraft, the system and what you observed. In this prototype the form shows the flow; it does not send anything.
      </Heading>
      <Card title="New report">
        {sent ? (
          <div className="space-y-3 text-sm">
            <p className="text-emerald-600">
              <b>Report recorded in this preview.</b>
            </p>
            <p className="text-slate-600">
              {form.severity} · {form.system} · {form.tail || "no aircraft"}: “{form.text || "no details"}”. Nothing was sent to maintenance control: this form is not connected to the planner.
            </p>
            <Btn onClick={() => (setSent(false), setForm({ tail: "", system: "Engine", severity: "Advisory", text: "" }))}>Write another</Btn>
          </div>
        ) : (
          <form
            className="grid max-w-2xl gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              setSent(true);
            }}
          >
            <div className="grid gap-4 sm:grid-cols-3">
              <label className="field-label">
                Aircraft
                <select value={form.tail} onChange={(e) => set("tail", e.target.value)} required>
                  <option value="" disabled>
                    Choose…
                  </option>
                  {aircraft.map((a) => (
                    <option key={a.tail} value={a.tail}>
                      {a.tail}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field-label">
                System
                <select value={form.system} onChange={(e) => set("system", e.target.value)}>
                  {["Engine", "Hydraulics", "Brakes", "Landing gear", "Avionics", "Cabin", "Other"].map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
              </label>
              <label className="field-label">
                Severity
                <select value={form.severity} onChange={(e) => set("severity", e.target.value)}>
                  {["Advisory", "Needs attention before next flight", "Ground the aircraft"].map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
              </label>
            </div>
            <label className="field-label">
              What did you observe?
              <textarea value={form.text} onChange={(e) => set("text", e.target.value)} rows={5} placeholder="For example: vibration on engine 2 at climb power, oil pressure fluctuating…" required />
            </label>
            <div>
              <Btn tone="primary">Submit report</Btn>
            </div>
          </form>
        )}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- Crew roster (preview)
type Member = [string, string, string, "On duty" | "Rest" | "Reserve" | "Off", string, string];
const CREW: Member[] = [
  ["Ava Martin", "Captain", "ATL", "On duty", "UA-4821 ATL–DEN", "Legal until 21:40Z"],
  ["Noah Patel", "First officer", "ATL", "On duty", "UA-4821 ATL–DEN", "Legal until 21:40Z"],
  ["Lena Ortiz", "Captain", "DFW", "Rest", "Next duty 11:30Z", "Rest ends 09:15Z"],
  ["Mateo Chen", "First officer", "DFW", "Reserve", "On call until 18:00Z", "Legal"],
  ["Priya Nair", "Captain", "JFK", "On duty", "UA-5107 JFK–MIA", "Legal until 19:05Z"],
  ["Jonas Weber", "First officer", "JFK", "On duty", "UA-5107 JFK–MIA", "Near limit (8.4 of 9 h)"],
  ["Imani Brooks", "Captain", "LAX", "Off", "Back Tue", "Legal"],
  ["Omar Haddad", "First officer", "LAX", "Reserve", "On call until 20:00Z", "Legal"],
  ["Sofia Rossi", "Captain", "ORD", "Rest", "Next duty 14:20Z", "Rest ends 12:00Z"],
  ["Kai Tanaka", "First officer", "ORD", "On duty", "UA-3390 ORD–SFO", "Legal until 23:10Z"],
  ["Grace Okafor", "Lead flight attendant", "ATL", "On duty", "UA-4821 ATL–DEN", "Legal until 22:00Z"],
  ["Diego Alvarez", "Flight attendant", "MIA", "Reserve", "On call until 19:00Z", "Legal"],
  ["Hannah Kim", "Flight attendant", "SFO", "Rest", "Next duty 13:40Z", "Rest ends 11:20Z"],
  ["Ravi Singh", "Flight attendant", "DEN", "Off", "Back Wed", "Legal"],
];
const DUTY_TONE: Record<string, string> = { "On duty": "bg-emerald-100 text-emerald-800", Rest: "bg-blue-100 text-blue-800", Reserve: "bg-amber-100 text-amber-800", Off: "bg-slate-100 text-slate-700" };
export function CrewRoster() {
  const [base, setBase] = useState("ALL");
  const [q, setQ] = useState("");
  const rows = CREW.filter((m) => (base === "ALL" || m[2] === base) && (!q || `${m[0]} ${m[1]} ${m[4]}`.toLowerCase().includes(q.toLowerCase())));
  const count = (s: string) => CREW.filter((m) => m[3] === s).length;
  return (
    <div className="space-y-5">
      <Heading eyebrow="Crew roster" title="Who is on, resting and on call" preview="Sample roster">
        Crew by base with duty status and legality. A real deployment reads the crew system and flags assignments that would break a limit.
      </Heading>
      <div className="facts">
        <Kpi label="On duty" value={count("On duty")} tone="emerald" />
        <Kpi label="In rest" value={count("Rest")} />
        <Kpi label="Reserve available" value={count("Reserve")} tone="amber" hint="On call" />
        <Kpi label="Near a limit" value={CREW.filter((m) => /Near limit/.test(m[5])).length} tone="red" />
      </div>
      <Card
        title="Roster"
        right={
          <div className="flex items-center gap-2">
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search name or pairing" aria-label="Search the roster" className="w-52 rounded-md border border-slate-300 bg-transparent px-2 py-1 text-sm text-slate-800" />
            <select value={base} onChange={(e) => setBase(e.target.value)} aria-label="Base" className="rounded-md border border-slate-300 bg-transparent px-2 py-1 text-sm text-slate-800">
              <option value="ALL">All bases</option>
              {STATIONS.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </div>
        }
      >
        <table className="w-full text-left text-sm">
          <thead>
            <tr>
              <th className="py-2">Name</th>
              <th>Position</th>
              <th>Base</th>
              <th>Status</th>
              <th>Assignment</th>
              <th>Legality</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((m) => (
              <tr key={m[0]} className="border-t border-slate-200">
                <td className="py-2 font-medium">{m[0]}</td>
                <td className="text-slate-600">{m[1]}</td>
                <td className="font-mono">{m[2]}</td>
                <td>
                  <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${DUTY_TONE[m[3]]}`}>{m[3]}</span>
                </td>
                <td className="font-mono text-xs text-slate-600">{m[4]}</td>
                <td className={/Near limit/.test(m[5]) ? "text-amber-600" : "text-slate-500"}>{m[5]}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <p className="p-6 text-center text-sm text-slate-500">Nobody matches.</p>}
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------- Flight release (live aircraft status; crew, weather, fuel sample)
export function Release() {
  const legs = usePoll<Leg[]>("/legs", 15000);
  const fleet = usePoll<FleetOverview>("/fleet", 5000);
  const plan = usePoll<PlanBundle>("/plan", 6000);
  const health = usePoll<HealthInfo>("/health", 5000);
  const [released, setReleased] = useState<Record<string, boolean>>({});
  const now = health.data?.sim_now;
  const rows = useMemo(() => {
    if (!legs.data || !fleet.data || !now) return [];
    const status = new Map(fleet.data.aircraft.map((a) => [a.tail, a.status]));
    const jobs = plan.data?.current.assignments ?? [];
    return legs.data
      .filter((l) => ms(l.sched_dep) >= ms(now) && ms(l.sched_dep) < ms(now) + 12 * 3_600_000)
      .sort((a, b) => ms(a.sched_dep) - ms(b.sched_dep))
      .slice(0, 24)
      .map((l) => {
        const st = status.get(l.tail) ?? "ok";
        const job = jobs.find((a) => a.tail === l.tail && ms(a.start) < ms(l.sched_arr) && ms(a.end) > ms(l.sched_dep));
        const wx = WX[l.dep]?.[3] ?? "VFR";
        const hold = st === "alert" || st === "aog" || st === "in_maintenance" || !!job;
        const review = !hold && (st === "watch" || wx === "IFR");
        return { l, st, job, wx, verdict: hold ? "Hold" : review ? "Review" : "Ready" };
      });
  }, [legs.data, fleet.data, plan.data, now]);
  if (!legs.data || !fleet.data || !now) return legs.error || fleet.error ? <ErrorBox error={legs.error ?? fleet.error} /> : <Loading what="departures" />;
  const n = (v: string) => rows.filter((r) => r.verdict === v).length;
  return (
    <div className="space-y-5">
      <Heading eyebrow="Flight release" title="Is each departure clear to go?">
        Each departure in the next 12 hours is checked against the aircraft's status and the maintenance plan (live), plus crew, weather and fuel (sample). A person releases the flight.
      </Heading>
      <AskPanel placeholder="Ask about a departure…" suggestions={["Which departures should be held?", "Which aircraft need attention before they fly?", "Does any departure touch a maintenance window?"]} />
      <div className="facts">
        <Kpi label="Departures, next 12 h" value={rows.length} />
        <Kpi label="Ready" value={n("Ready")} tone="emerald" />
        <Kpi label="Needs review" value={n("Review")} tone="amber" hint="Watch status or IFR departure" />
        <Kpi label="Hold" value={n("Hold")} tone={n("Hold") ? "red" : "emerald"} hint="Alert, in maintenance or in a window" />
      </div>
      <Card title="Departures" right={<Preview what="crew, weather and fuel are sample" />}>
        {rows.length === 0 ? (
          <p className="text-sm text-slate-500">No departures in the next 12 hours.</p>
        ) : (
          <table className="w-full text-left text-sm">
            <thead>
              <tr>
                <th className="py-2">Flight</th>
                <th>Route</th>
                <th>Departs</th>
                <th>Aircraft</th>
                <th>Crew</th>
                <th>Weather</th>
                <th>Fuel</th>
                <th>Decision</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ l, st, job, wx, verdict }) => (
                <tr key={l.id} className="border-t border-slate-200">
                  <td className="py-2 font-mono">{l.id}</td>
                  <td className="font-mono">
                    {l.dep} → {l.arr}
                  </td>
                  <td className="tabular-nums">{fmtZ(l.sched_dep)}</td>
                  <td>
                    <Link to={`/aircraft/${l.tail}`} className="mr-2 text-blue-600 hover:underline">
                      {l.tail}
                    </Link>
                    <StatusPill status={st} />
                    {job && <div className="text-xs text-amber-600">maintenance {fmtZ(job.start)}</div>}
                  </td>
                  <td className="text-emerald-600">Assigned</td>
                  <td className={CAT_TONE[wx]}>{wx}</td>
                  <td className="text-slate-500">Planned</td>
                  <td>
                    {released[l.id] ? (
                      <Chip tone="blue">Released</Chip>
                    ) : verdict === "Hold" ? (
                      <span className="font-semibold text-red-600">Hold</span>
                    ) : (
                      <Btn onClick={() => setReleased((r) => ({ ...r, [l.id]: true }))} tone={verdict === "Ready" ? "primary" : "default"}>
                        {verdict === "Ready" ? "Release" : "Review and release"}
                      </Btn>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <p className="mt-3 text-xs text-slate-500">Releasing marks the flight released in this view only; it does not change the simulation.</p>
      </Card>
    </div>
  );
}
