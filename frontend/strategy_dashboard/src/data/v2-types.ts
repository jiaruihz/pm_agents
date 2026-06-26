// Types for the redesigned (v2) dashboard endpoints.

export type Freshness = "fresh" | "aging" | "stale" | "unknown";

export interface ProbeHealthRow {
  strategy_instance: string;
  lifecycle_status: string | null;
  health_status: string | null;
  heartbeat_age_min: number | null;
  status: string;
  snapshot_age_min: number | null;
  snapshot_ts_utc: string | null;
  freshness: Freshness;
  candidate_rows: number | null;
  execution_eligible: number | null;
  top_audit: string | null;
  caps: Record<string, unknown> | null;
}

export interface ProbeHealthResponse {
  probes: ProbeHealthRow[];
  generated_at_utc: string;
}

export interface ProbeDetail {
  row: ProbeHealthRow;
  history: Record<string, unknown>[];
  candidates: Record<string, unknown>[];
  live_orders: Record<string, unknown>[];
}

export interface ResearchLineRow {
  line_id: string;
  title: string;
  status: string;
  verdict_reason: string | null;
  holdout_roi: number | null;
  forward_roi: number | null;
  repr_roi: number | null;
  ci_low: number | null;
  ci_high: number | null;
  ci_crosses_zero: boolean;
  excess_roi_vs_baseline: number | null;
  gate_ready: boolean;
  variant_count: number;
  generated_at_utc: string | null;
  summary_path: string;
}

export interface ResearchLinesResponse {
  lines: ResearchLineRow[];
}

export interface ResearchLineDetail {
  line_id: string;
  summary: Record<string, unknown>;
  summary_path: string;
}

export interface GlossaryEntry {
  zh: string;
  definition: string;
  source_doc: string;
}

export interface GlossaryResponse {
  fields: Record<string, GlossaryEntry>;
}
