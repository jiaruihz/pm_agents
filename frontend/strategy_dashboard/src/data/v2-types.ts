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
  narrative_md: string | null;
  doc_path: string | null;
}

export interface LiveBookRow {
  fill_id: string;
  config_id: string | null;
  strategy_id: string | null;
  strategy_name: string | null;
  city: string | null;
  city_pool: string | null;
  target_date: string | null;
  bracket: string | null;
  side: string | null;
  forecast_source: string | null;
  model_version: string | null;
  snapshot_ts_utc: string | null;
  edge: number | null;
  market_price: number | null;
  val_mid: number | null;
  fill_price: number | null;
  fill_qty: number | null;
  cost_usd: number | null;
  notional: number | null;
  settlement_status: string | null;
  settled: number;
  final_yes: number | null;
  pnl_usd_at_fill: number | null;
  unrealized_pnl_mid: number | null;
  val_snapshot_ts_utc: string | null;
  fill_ts_utc: string | null;
  condition_id: string | null;
  market_id: string | null;
  poly_url: string | null;
  stale_unsettled: boolean;
}

export interface LiveBookResponse { rows: LiveBookRow[]; }

export interface LiveBookStrategy {
  strategy_name: string;
  n: number;
  settled: number;
  open_count: number;
  cost_usd: number;
  open_cost_usd: number;
  realized_pnl_usd: number;
  last_fill_ts_utc: string | null;
}

export interface LiveBookStrategiesResponse { strategies: LiveBookStrategy[]; }

export interface ForecastSource {
  forecast_source: string;
  cities: number;
  models: number;
  rows: number;
  latest_snapshot_ts_utc: string | null;
  first_target_date: string | null;
  last_target_date: string | null;
}

export interface MarketSnapshot {
  file: string;
  mtime_utc: string | null;
  ts_utc: string | null;
  ts_beijing: string | null;
  total_records: number | null;
  trading_cities: number | null;
  research_cities: number | null;
}

export interface ObservationSource {
  canonical: string;
  aliases: string[];
  description: string;
  kind: string;
}

export interface DataSourcesResponse {
  forecast_sources: ForecastSource[];
  observation_sources: ObservationSource[];
  market_snapshots: MarketSnapshot[];
  market_snapshot_cadence_min: number | null;
}

export interface GlossaryEntry {
  zh: string;
  definition: string;
  source_doc: string;
}

export interface GlossaryResponse {
  fields: Record<string, GlossaryEntry>;
}
