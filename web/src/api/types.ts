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
/** Risk view of one condition-driven task in a plan (GET /plan `exposure`; see CONTRACTS.md). Unscheduled tasks only carry `scheduled: false`. */
export type Exposure = {
  task_id: string;
  tail: string;
  component_id: string;
  scheduled: boolean;
  station?: string | null;
  hours_to_fix?: number | null;
  flights_before_fix?: number | null;
  slack_to_deadline_h?: number | null;
};
export type PlanBundle = {
  current: Plan;
  baseline: Plan;
  compare: Comparison;
  exposure?: { baseline: Exposure[]; optimized: Exposure[] };
};
export type InjectResult = { event_id: string; effects: string[]; twin_changes: string[] };
export type HealthInfo = {
  ok: boolean; mock: boolean; version: string; sim_now: string; data_source: string;
  modules: Record<string, string>; errors: Record<string, string>; llm_key_present: boolean; llm_model: string;
};
export type Status = "ok" | "watch" | "alert" | "in_maintenance" | "aog";
