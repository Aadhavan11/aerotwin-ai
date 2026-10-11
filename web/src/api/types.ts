// Types come from the backend's OpenAPI schema:  npm run types   (needs the API running on :8000). Do not edit schema.d.ts by hand.
import type { components } from "./schema";

type S = components["schemas"];
export type FleetOverview = S["FleetOverview"];
export type AircraftSummary = S["AircraftSummary"];
export type TwinView = S["TwinView"];
export type TwinComponent = S["TwinComponent"];
export type Plan = S["Plan"];
export type Assignment = S["Assignment"];
export type PlanMetrics = S["PlanMetrics"];
export type ReplanResult = S["ReplanResult"];
export type Comparison = S["Comparison"];
export type Scenario = S["Scenario"];
export type ScenarioEvent = S["ScenarioEvent"];
export type AgentRun = S["AgentRun"];
export type AgentStep = S["AgentStep"];
export type ChatResponse = S["ChatResponse"];
export type EvidenceRef = S["EvidenceRef"];
export type WorkOrder = S["WorkOrder"];
export type AuditEntry = S["AuditEntry"];
export type Leg = S["Leg"];
export type Station = S["Station"];
export type MetricsBundle = S["MetricsBundle"];

// Responses the backend returns as plain dicts
export type PlanBundle = { current: Plan; baseline: Plan; compare: Comparison };
export type InjectResult = { event_id: string; effects: string[]; twin_changes: string[] };
export type HealthInfo = {
  ok: boolean; mock: boolean; version: string; sim_now: string; data_source: string;
  modules: Record<string, string>; errors: Record<string, string>; llm_key_present: boolean; llm_model: string;
};
export type Status = "ok" | "watch" | "alert" | "in_maintenance" | "aog";

// Maintenance screen (GET /aircraft/{tail}/maintenance and GET /maintenance/queue; additive endpoints)
export type MaintItem = {
  task_id: string;
  title: string;
  component_id: string;
  component: string;
  source: "rul" | "scheduled" | "manual";
  basis: string;
  deadline: string | null;
  earliest: string | null;
  duration_h: number;
  skill: string;
  part: string | null;
  part_qty: number;
  card: { id: string; ata?: string; note?: string; trigger?: string; interval_days?: number | null } | null;
  assignment: Assignment | null;
  baseline: { station: string; start: string; end: string } | null;
  exposure: { scheduled: boolean; station?: string; hours_to_fix?: number; flights_before_fix?: number; slack_to_deadline_h?: number } | null;
  work_order: WorkOrder | null;
  approved_slot_moved: boolean;
  logistics: {
    station: string;
    hangar_slots: number;
    skill: string;
    technician_assigned: string | null;
    technicians_covering: number;
    technicians_qualified: number;
    part?: {
      name: string; needed: number; on_hand: number; eta_hours: number; from_station: string | null;
      ship_from: string | null; ship_hours: number; ship_cost_usd: number;
    };
  } | null;
};
export type MaintSuggestion = {
  component_id: string; component: string; kind: string; status: string; health: number; rul_p10: number; rul_p50: number;
  action: string; card_id: string | null; duration_h: number | null; skill: string | null; part: string | null;
  act_before: string; text: string;
};
export type MaintView = {
  tail: string;
  now: string;
  source: string;
  plan_id: string;
  flights_per_day: number;
  items: MaintItem[];
  suggestions: MaintSuggestion[];
  history: { ts: string; kind: string; summary: string; ref?: string | null }[];
  ground_windows: { station: string; start: string; end: string; hours: number; before_leg: string | null }[];
};
export type QueueRow = {
  task_id: string; tail: string; title: string; component_id: string; component: string; source: string;
  deadline: string | null; duration_h: number; skill: string; part: string | null;
  station: string | null; start: string | null; end: string | null; technician_id: string | null; part_source: string | null;
  scheduled: boolean; slack_to_deadline_h: number | null;
  work_order_id: string | null; work_order_status: string | null; approved_slot_moved: boolean;
};
