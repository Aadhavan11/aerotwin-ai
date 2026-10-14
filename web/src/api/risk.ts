import { post } from "./client";

type Distribution = { mean?: number | null; p50?: number | null; p90?: number | null; p95?: number | null; max?: number | null };
export type CostDistribution = Distribution & { std?: number | null; cvar95?: number | null };
export type RiskHistogram = { metric?: string | null; bin_edges?: number[] | null; counts?: number[] | null };
export type ComparisonPlan = {
  plan_id?: string | null; mode?: string | null; cost_usd?: CostDistribution | null; p_miss_any_deadline?: number | null;
  flights_delayed_mean?: number | null; flights_cancelled_mean?: number | null; aog_h_mean?: number | null; deadline_misses_mean?: number | null;
};
export type MonteCarloResult = {
  simulated?: boolean | null; n?: number | null; seed?: number | null; cached?: boolean | null; elapsed_s?: number | null;
  cost_usd?: CostDistribution | null; p_miss_any_deadline?: number | null; p_unplanned_aog?: number | null;
  plan?: {
    id?: string | null; mode?: string | null; feasible?: boolean | null; plan_hash?: string | null; assignments?: unknown[] | null;
    deterministic?: { total_cost_usd?: number | null; downtime_h?: number | null; flights_affected?: number | null; late_or_unscheduled?: number | null } | null;
  } | null;
  flights_delayed?: Distribution | null; flights_cancelled?: Distribution | null; delay_min?: Distribution | null;
  aog_h?: Distribution | null; deadline_misses?: Distribution | null; unscheduled_tasks?: unknown[] | null;
  risk_ranking?: {
    rank?: number | null; task_id?: string | null; tail?: string | null; station?: string | null; start?: string | null; end?: string | null;
    p_late?: number | null; p_overrun_infeasible?: number | null; p_failure_before_done?: number | null;
    expected_delay_h?: number | null; expected_cost_usd?: number | null; share_of_risk_cost?: number | null;
  }[] | null;
  histogram?: RiskHistogram | null;
  comparison?: {
    optimised?: ComparisonPlan | null; baseline?: ComparisonPlan | null; delta_mean_cost_usd?: number | null;
    delta_cvar95_usd?: number | null; delta_p_miss_any_deadline?: number | null; note?: string | null;
    deterministic?: { name?: string | null; plan_id?: string | null; mode?: string | null; feasible?: boolean | null; total_cost_usd?: number | null; late_or_unscheduled?: number | null }[] | null;
  } | null;
  provenance?: {
    source?: string | null; state_source?: string | null; as_of?: string | null; seed?: number | null; n?: number | null;
    model?: string | null; config?: Record<string, unknown> | null; prior_constants?: Record<string, unknown> | null; synthetic?: boolean | null;
  } | null;
};

export async function runMonteCarlo(body: { plan_id?: string; n: number; seed: number }): Promise<MonteCarloResult> {
  return (await post<MonteCarloResult>("/ops/montecarlo", body)).data;
}
