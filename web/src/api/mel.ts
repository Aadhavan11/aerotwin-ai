import { get, post } from "./client";

export type MelStatus = "open" | "rectified" | "expired";
export type MelItem = {
  id?: string | null; tail?: string | null; ata_chapter?: string | null; ata_section?: string | null; title?: string | null;
  category?: "A" | "B" | "C" | "D" | null; reason?: string | null; deferred_at?: string | null;
  expires_at?: string | null; limit_days?: number | null; status?: MelStatus | null; restrictions?: string[] | null;
  rectified_at?: string | null; deferred_by?: string | null; notes?: string | null; extensions_used?: number | null;
  simulated?: boolean | null;
};
export type MelList = { simulated?: boolean | null; items?: MelItem[] | null };
export type MelSummary = {
  simulated?: boolean | null; by_category?: Partial<Record<"A" | "B" | "C" | "D", number | null>> | null;
  expiring_within_72h?: number | null; aircraft_with_open_restrictions?: string[] | null;
};
export type CreateMelBody = { tail: string; component_kind: string; fault_mode: string; reason: string; limit_days?: number };

export async function getMel({ tail, status }: { tail?: string; status?: MelStatus } = {}): Promise<MelList> {
  const query = new URLSearchParams();
  if (tail) query.set("tail", tail);
  if (status) query.set("status", status);
  return (await get<MelList>(`/ops/mel${query.size ? `?${query}` : ""}`)).data;
}
export async function getMelSummary(): Promise<MelSummary> {
  return (await get<MelSummary>("/ops/mel/summary")).data;
}
export async function closeMel(id: string): Promise<MelItem> {
  return (await post<MelItem>(`/ops/mel/${encodeURIComponent(id)}/close`, {})).data;
}
export async function extendMel(id: string, justification: string): Promise<MelItem> {
  return (await post<MelItem>(`/ops/mel/${encodeURIComponent(id)}/extend`, { justification })).data;
}
export async function createMel(body: CreateMelBody): Promise<MelItem> {
  return (await post<MelItem>("/ops/mel", body)).data;
}
