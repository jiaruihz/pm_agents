// Types for the redesigned (v2) dashboard endpoints.

export type Freshness = "fresh" | "aging" | "stale" | "unknown";

export interface ProbeHealthRow {
  strategy_instance: string;
  display_name: string | null;
  lifecycle_status: string | null;
  execution_mode: string | null;
  desired_status: string | null;
  process_status: string;
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

export type DataSourceHealthStatus =
  | "fresh"
  | "stale"
  | "missing"
  | "auth_required"
  | "fetch_failed"
  | "unknown";

export interface DataSourceProfile {
  profile_id: string;
  feed_kind: string;
  city: string;
  source_key: string;
  source_kind: string | null;
  station_or_feed: string | null;
  icao: string | null;
  runway: string | null;
  source_role: string;
  timezone_name: string | null;
  expected_cadence_sec: number | null;
  staleness_max_age_sec: number | null;
  active_window_json: string;
  requires_auth: number;
  auth_ref: string | null;
  strategy_eligible: number;
  live_eligible: number;
  observed_median_lag_sec: number | null;
  observed_p95_lag_sec: number | null;
  notes: string;
  updated_at_utc: string;
}

export interface DataSourceMonitorInstance {
  monitor_instance_id: string;
  display_name: string;
  feed_kind: string;
  sources_json: string;
  cities_json: string;
  scan_interval_sec: number | null;
  active_window_json: string;
  output_dir: string | null;
  latest_path: string | null;
  journal_paths_json: string;
  state_path: string | null;
  proxy_policy: string | null;
  auth_refs_json: string;
  desired_status: string;
  host: string;
  tmux_session: string | null;
  start_command: string | null;
  summary_json: string;
  updated_at_utc: string;
}

export interface DataSourceDynamicHealth {
  monitor_instance_id: string;
  display_name: string;
  feed_kind: string;
  status: DataSourceHealthStatus;
  latest_generated_at_utc: string | null;
  latest_file_mtime_utc: string | null;
  age_sec: number | null;
  rows: number | null;
  source_statuses: Record<string, unknown>;
  source_errors: Record<string, unknown>;
  cities: string[];
  sources: string[];
  sample_keys: string[];
  sample_json: unknown;
}

export interface DataSourcesResponse {
  source_profiles: DataSourceProfile[];
  monitor_instances: DataSourceMonitorInstance[];
  dynamic_health: DataSourceDynamicHealth[];
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

export interface OrderBlotterRow {
  row_kind: "fill" | "order";
  trade_class: string | null;
  execution_mode: string | null;
  venue: string | null;
  config_id: string | null;
  strategy_key: string | null;
  strategy_id: string | null;
  strategy_name: string | null;
  config_name: string | null;
  strategy_instance: string | null;
  run_id: string | null;
  signal_id: string | null;
  plan_id: string | null;
  execution_id: string | null;
  order_id: string | null;
  fill_id: string | null;
  target_date: string | null;
  city: string | null;
  city_pool: string | null;
  bracket: string | null;
  side: string | null;
  order_status: string | null;
  fill_status: string | null;
  order_ts_utc: string | null;
  fill_ts_utc: string | null;
  snapshot_ts_utc: string | null;
  market_price: number | null;
  limit_price: number | null;
  fill_price: number | null;
  fill_qty: number | null;
  cost_usd: number | null;
  notional: number | null;
  fees_usd: number | null;
  settled: number | null;
  final_yes: number | null;
  pnl_usd_at_fill: number | null;
  unrealized_pnl_mid: number | null;
  val_mid: number | null;
  val_snapshot_ts_utc: string | null;
  condition_id: string | null;
  market_id: string | null;
}

export interface OrderBlotterResponse {
  rows: OrderBlotterRow[];
  total: number;
  limit: number;
  offset: number;
  filters: Record<string, string | number | null>;
}
