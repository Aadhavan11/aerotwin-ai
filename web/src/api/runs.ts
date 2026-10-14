import { ApiError, get, post } from "./client";

export type SimulationRun = {
  id: string;
  status: "queued" | "running" | "interrupted" | "failed" | "done";
  provenance: string;
  years: number;
  seed: number;
  cycles_done: number;
  total_cycles: number;
  progress: number;
  eta_seconds: number | null;
  rows: number;
  error: string | null;
  state_hash: string | null;
  spec: { seed: number; fleet: number | string[] | null; initial_wear: boolean; checkpoint_every: number };
};

export async function getRuns(): Promise<SimulationRun[]> {
  const { data } = await get<SimulationRun[] | { runs?: SimulationRun[] } | null>("/sim/runs");
  const runs = Array.isArray(data) ? data : data?.runs;
  return Array.isArray(runs) ? runs : [];
}

export async function getRun(id: string): Promise<SimulationRun> {
  return (await get<SimulationRun>(`/sim/runs/${encodeURIComponent(id)}`)).data;
}

export async function startRun(years: number, seed: number): Promise<SimulationRun> {
  return (await post<SimulationRun>("/sim/runs", { years, seed })).data;
}

export async function resumeRun(id: string): Promise<SimulationRun> {
  return (await post<SimulationRun>(`/sim/runs/${encodeURIComponent(id)}/resume`)).data;
}

export function runExportUrl(id: string): string {
  const base = (import.meta.env.VITE_API as string | undefined) ?? `${import.meta.env.BASE_URL}api`;
  return `${base}/sim/runs/${encodeURIComponent(id)}/export`;
}

/** Like the JSON client, fetch uses the browser's same-origin session cookie. */
export async function exportRun(id: string): Promise<{ blob: Blob; filename: string }> {
  const response = await fetch(runExportUrl(id));
  if (response.status === 401) window.dispatchEvent(new Event("aerotwin:auth-lost"));
  if (!response.ok) throw new ApiError(response.status, "Run export is not available.");
  const filename = response.headers.get("content-disposition")?.match(/filename="([^"]+)"/)?.[1]
    ?? `sim_run_${id}${response.headers.get("content-type")?.includes("parquet") ? ".parquet" : ".csv.gz"}`;
  return { blob: await response.blob(), filename };
}
