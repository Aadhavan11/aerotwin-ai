import { get } from "./client";

type Provenance = { simulated?: boolean | null; provenance?: string | null };
type Payload = Provenance & { disclaimer?: string | null; as_of?: string | null; basis?: string | null; tail?: string | null; count?: number | null };
export type LlpPart = Provenance & {
  tail?: string | null; position?: string | null; part_key?: string | null; label?: string | null; ata?: string | null;
  scope?: "engine_1" | "engine_2" | "airframe" | null; part_no?: string | null; serial?: string | null;
  cycle_limit?: number | null; calendar_limit_years?: number | null; csn?: number | null;
  remaining_cycles?: number | null; remaining_days?: number | null; cycles_per_day?: number | null;
  limit_date?: string | null; limit_driver?: "cycles" | "calendar" | null; installed_date?: string | null;
  replaced?: boolean | null; status?: "ok" | "watch" | "alert" | "expired" | null;
};
export type LlpPayload = Payload & {
  summary?: { ok?: number | null; watch?: number | null; alert?: number | null; expired?: number | null } | null;
  parts?: LlpPart[] | null;
};
export type LlpTailPayload = LlpPayload & {
  aircraft?: { cycles?: number | null; cycles_per_day?: number | null; location?: string | null } | null;
};
type Flights = { p10_flights?: number | null; p50_flights?: number | null; p90_flights?: number | null };
export type ShopVisitEngine = Provenance & Flights & {
  rank?: number | null; tail?: string | null; engine?: string | null; component_id?: string | null;
  driver?: "egt_margin" | "llp" | "rul" | null; driver_llp_part?: string | null; overdue?: boolean | null;
  p10_date?: string | null; p50_date?: string | null; p90_date?: string | null; cycles_per_day?: number | null;
  drivers?: Partial<Record<"egt_margin" | "llp" | "rul", Flights | null>> | null;
  egt_margin?: { margin_c?: number | null; loss_c_per_cycle?: number | null; basis?: string | null; floor_c?: number | null } | null;
  rul_censored?: boolean | null;
};
export type PipelineMonth = {
  month?: number | null; days_from?: number | null; days_to?: number | null; expected?: number | null;
  cumulative_expected?: number | null; p50_count?: number | null; cumulative_p50_count?: number | null;
};
export type ShopVisitPayload = Payload & {
  horizon_months?: number | null; drivers?: { egt_margin?: number | null; llp?: number | null; rul?: number | null } | null;
  engines?: ShopVisitEngine[] | null; pipeline?: PipelineMonth[] | null;
};

export async function getLlp(tail?: string): Promise<LlpPayload> {
  return (await get<LlpPayload>(`/ops/llp${tail ? `?tail=${encodeURIComponent(tail)}` : ""}`)).data;
}
export async function getLlpTail(tail: string): Promise<LlpTailPayload> {
  return (await get<LlpTailPayload>(`/ops/llp/${encodeURIComponent(tail)}`)).data;
}
export async function getShopVisits(tail?: string): Promise<ShopVisitPayload> {
  return (await get<ShopVisitPayload>(`/ops/shop-visits${tail ? `?tail=${encodeURIComponent(tail)}` : ""}`)).data;
}
