// Hand-written responses for the Insights endpoints; schema.d.ts is generated.
export type InsightsFault = { key: string; mode: string };
export type InsightsDataQualityItem = {
  tail: string;
  component_id: string;
  kind: string;
  status: "ok" | "degraded" | "bad";
  reasons: string[];
  faults: InsightsFault[];
};
export type InsightsDataQuality = {
  simulated: boolean;
  as_of: string;
  counts: { ok: number; degraded: number; bad: number };
  items: InsightsDataQualityItem[];
};
export type InsightsReliabilityKind = {
  kind: string;
  components: number;
  samples: number;
  beta: number;
  eta_cycles: number;
  mtbur_cycles: number;
  b10_cycles: number;
  basis: string;
};
export type InsightsReliability = {
  simulated: boolean;
  as_of: string;
  kinds: InsightsReliabilityKind[];
};
export type InsightsWeatherEvent = {
  id: string;
  station: string;
  start: string;
  duration_h: number;
  severity: number;
};
export type InsightsDelayedLeg = {
  leg_id: string;
  tail: string;
  dep: string;
  arr: string;
  sched_dep: string;
  est_dep: string;
  sched_arr: string;
  est_arr: string;
  delay_min: number;
  cause: string;
};
export type InsightsTailDelay = { tail: string; legs_affected: number; total_delay_min: number };
export type InsightsDisruptions = {
  simulated: boolean;
  as_of: string;
  events: InsightsWeatherEvent[];
  delayed_legs: InsightsDelayedLeg[];
  by_tail: InsightsTailDelay[];
};
