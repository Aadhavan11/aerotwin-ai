import { get } from "./client";
import type { InsightsReliabilityKind } from "./insights";

export type AlertStatus = "normal" | "warning" | "alert" | "insufficient_data";
export type ReliabilityMetrics = {
  units: number | null;
  flight_hours: number | null;
  flight_cycles: number | null;
  removals: number | null;
  unscheduled_removals: number | null;
  failures: number | null;
  nff_removals: number | null;
  mtbur_fh: number | null;
  mtbur_fc: number | null;
  mtbf_fh: number | null;
  mtbf_fc: number | null;
  nff_rate: number | null;
  premature_removal_rate: number | null;
  removal_rate_per_1000fh: number | null;
  rate_ci_low: number | null;
  rate_ci_high: number | null;
  ci_level: number | null;
};
export type ReliabilityKind = ReliabilityMetrics & {
  kind: string;
  ata_chapter: string;
  ata_section: string;
  alert_status: AlertStatus;
};
export type ReliabilityAta = ReliabilityMetrics & { ata_chapter: string; title: string; kinds: string[]; alert_status: AlertStatus };
export type ParetoItem = {
  ata_chapter: string;
  title: string;
  unscheduled_removals: number | null;
  share: number | null;
  cumulative_share: number | null;
};
export type ReliabilityAlert = {
  kind: string;
  ata_chapter: string;
  status: AlertStatus;
  period_days: number;
  latest_rate_per_1000fh: number | null;
  latest_count: number | null;
  threshold_per_1000fh: number | null;
  warning_per_1000fh: number | null;
  window_mean_per_1000fh: number | null;
  window_sigma_per_1000fh: number | null;
  window_periods: number;
  rule: string;
};
export type RepeatDefect = {
  tail: string; kind: string; position: string; ata_chapter: string;
  count: number; window_days: number; first: string; last: string; reasons: string[]; nff_count: number;
};
export type ReliabilityPack = {
  as_of: string;
  simulated: boolean;
  provenance: string;
  disclaimer: string;
  taxonomy_label: string;
  unscheduled_removals_total: number | null;
  source: {
    basis: string; cycles: number; seed: number | null; tails: number; years?: number;
    engine: string; cache_key?: string; span_days: number | null;
  };
  metrics: { per_kind: ReliabilityKind[]; per_ata: ReliabilityAta[] };
  pareto: ParetoItem[];
  alerts: ReliabilityAlert[];
  repeat_defects: RepeatDefect[];
  top_contributors: {
    kinds: { kind: string; unscheduled_removals: number }[];
    tails: { tail: string; unscheduled_removals: number }[];
  };
  weibull: InsightsReliabilityKind[];
};
export type AtaSection = { section: string; title: string; kinds: string[]; fault_modes: string[] };
export type AtaChapter = { chapter: string; title: string; kinds: string[]; sections: AtaSection[] };
export type AtaTable = {
  simulated: boolean; provenance: string; label: string; chapters: AtaChapter[];
  kind_to_section: Record<string, string>; mode_to_section: Record<string, string>;
};

export async function getReliabilityPack(): Promise<ReliabilityPack> {
  return (await get<ReliabilityPack>("/insights/reliability/pack")).data;
}

export async function getAtaTable(): Promise<AtaTable> {
  return (await get<AtaTable>("/insights/ata")).data;
}
