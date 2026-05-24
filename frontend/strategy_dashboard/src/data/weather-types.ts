// Weather dashboard API types — mirrors FastAPI schemas

export interface RunMetrics {
  num_trades: number;
  total_pnl_usd: number | null;
  win_rate: number | null;
  loss_rate: number | null;
  avg_pnl_usd: number | null;
  median_pnl_usd: number | null;
  pnl_trimmed_1pct: number | null;
  top1_pnl_share: number | null;
  top5_pnl_share: number | null;
  total_cost_usd: number | null;
  roi: number | null;
  settled_trades: number;
  unsettled_trades: number;
  settled_ratio: number | null;
  worst_loss_usd: number | null;
  best_win_usd: number | null;
  expectancy_usd: number | null;
  max_drawdown_usd: number | null;
  fees_paid_usd: number | null;
  avg_win_usd: number | null;
  avg_loss_usd: number | null;
}

export interface RunSummary {
  run_id: string;
  producer_system?: string | null;
  producer_run_id?: string | null;
  config_id: string;
  universe_id: string | null;
  code_version: string | null;
  execution_mode: "snapshot_replay" | "paper" | "live";
  date_range_start: string | null;
  date_range_end: string | null;
  state: "explore" | "paper" | "live" | "retired";
  repro_key: string | null;
  tags: string[];
  notes: string | null;
  created_at_utc: string;
  started_at_utc: string | null;
  metrics: RunMetrics | null;  // cached from runs.metrics
}

export interface RunDetail extends RunSummary {
  // metrics inherited from RunSummary, always populated on get_run
}

export interface TradeRow {
  signal_id: string;
  target_date: string | null;
  city: string | null;
  bracket: string | null;
  signal_side: string | null;
  model_version: string | null;
  model_p_yes: number | null;
  market_price: number | null;
  edge: number | null;
  order_id: string | null;
  execution_id?: string | null;
  order_side: string | null;
  entry_price: number | null;
  shares: number | null;
  cost_usd: number | null;
  fill_status: string | null;
  filled_at_utc: string | null;
  final_price: number | null;
  settlement_status: string | null;
  pnl_usd: string | null;
  city_pool?: string | null;
  forecast_source?: string | null;
  icao?: string | null;
  hours_to_settle?: number | null;
}

export interface TradeDrilldown {
  run_id: string;
  signal_id: string;
  signal: Record<string, unknown> | null;
  plans: Record<string, unknown>[];
  orders: Record<string, unknown>[];
  fills: Record<string, unknown>[];
  settlement: Record<string, unknown> | null;
  artifacts: Record<string, unknown>[];
}

export interface ConfigRow {
  config_id: string;
  name: string;
  params: Record<string, unknown>;
  created_at_utc: string;
}

export interface UniverseRow {
  universe_id: string;
  name: string;
  description: string | null;
  cities: string[];
  models: string[];
  created_at_utc: string;
  frozen_at_utc: string | null;
  deprecated_at_utc: string | null;
}

/** Panel A: one filled CLOB position with lineage */
export interface LivePosition {
  signal_id: string;
  city: string | null;
  target_date: string | null;
  bracket: string | null;
  condition_id: string | null;
  city_pool: string | null;
  forecast_source: string | null;
  model_p_yes: number | null;
  signal_market_price: number | null;
  signal_edge: number | null;
  execution_id: string;
  order_id: string | null;
  run_id: string;
  order_side: string;
  cost_usd: number | null;
  fill_id: string;
  filled_shares: number | null;
  filled_price: number | null;
  fees_usd: number | null;
  filled_at_utc: string | null;
  final_price: number | null;
  settlement_status: string | null;
  pnl_usd: number | null;
}

/** Panel B: paper vs CLOB execution comparison for one signal */
export interface ExecutionGapRow {
  signal_id: string;
  city: string | null;
  target_date: string | null;
  bracket: string | null;
  city_pool: string | null;
  model_p_yes: number | null;
  signal_market_price: number | null;
  signal_edge: number | null;
  signal_side: string | null;

  paper_fill_price: number | null;
  paper_shares: number | null;
  paper_fill_status: string | null;
  paper_cost_usd: number | null;

  clob_fill_price: number | null;
  clob_shares: number | null;
  clob_fill_status: string | null;
  clob_cost_usd: number | null;
  clob_order_status: string | null;

  final_price: number | null;
  settlement_status: string | null;
  paper_pnl_usd: number | null;
  clob_pnl_usd: number | null;
  pnl_gap_usd: number | null;
  price_slippage: number | null;
  gap_type: "both" | "clob_only" | "paper_only_clob_rejected" | "paper_only";
}

/** /api/live/summary response */
export interface LiveSummary {
  last_cycle_utc: string | null;
  clob: {
    total_positions: number;
    open_count: number;
    settled_count: number;
    capital_deployed_usd: number;
    realized_pnl_usd: number;
  };
  pending_orders: {
    count: number;
    reserved_usd: number;
  };
  paper_baseline: {
    run_id: string | null;
    metrics: RunMetrics | null;
  };
}

/** /api/strategies — per-config aggregated stats */
export interface StrategyRow {
  config_id: string;
  name: string;
  params: Record<string, unknown>;
  created_at_utc: string;
  execution_policy: string | null;
  num_runs: number;
  latest_run_at: string | null;
  live_run_count: number;
  paper_run_count: number;
  total_trades: number;
  settled_trades: number;
  win_trades: number;
  total_pnl_usd: number;
  capital_deployed_usd: number;
  roi: number | null;
  win_rate: number | null;
}

/** /api/strategies/{config_id}/positions — all fills with settlement status */
export interface PositionRow {
  fill_id: string;
  filled_shares: number;
  filled_price: number;
  filled_at_utc: string | null;
  fill_status: string;
  order_side: string;
  venue: string;
  order_id: string | null;
  target_date: string;
  city: string;
  bracket: string;
  signal_side: string;
  model_version: string | null;
  model_p_yes: number | null;
  signal_price: number | null;
  condition_id: string | null;
  final_price: number | null;
  settlement_status: string | null;
  pnl_usd: number | null;
}

/** /api/strategies/{config_id}/equity — daily cumulative PnL */
export interface EquityPoint {
  date: string;
  pnl: number;
  cumulative_pnl: number;
  trades: number;
  settled: number;
  wins: number;
  capital: number;
}

/** /api/strategies/{config_id}/analytics — breakdown by dimension */
export interface AnalyticsDimension {
  dimension: string;
  trades: number;
  settled: number;
  pnl: number;
  wins: number;
  capital: number;
}

export interface StrategyAnalytics {
  by_side: AnalyticsDimension[];
  by_city: AnalyticsDimension[];
  by_bracket: AnalyticsDimension[];
  by_model: AnalyticsDimension[];
  by_forecast_source: AnalyticsDimension[];
}

/** /api/strategies/{config_id}/funnel — per-day order placement vs fill breakdown */
export interface FunnelRow {
  day: string;
  signals_evaluated: number;
  plans_executed: number;
  plans_skipped: number;
  orders_placed: number;
  orders_filled: number;
  orders_pending: number;
  fill_rate: number | null;        // orders_filled / orders_placed
  avg_limit_price: number | null;
  avg_fill_price: number | null;
  avg_market_price: number | null;
  limit_discount: number | null;   // avg_market_price - avg_limit_price (how far below market we bid)
  filled_capital_usd: number | null;
  pending_capital_usd: number | null;
}

/** /api/strategies/{config_id}/pending-orders — CLOB orders submitted but not yet filled */
export interface PendingOrderRow {
  execution_id: string;
  order_id: string | null;
  city: string;
  target_date: string;
  bracket: string;
  city_pool: string;
  order_side: string;
  limit_price: number | null;
  signal_market_price: number | null;
  limit_discount: number | null;  // signal_market_price - limit_price
  shares: number;
  cost_usd: number;
  placed_at_utc: string | null;
  hours_pending: number | null;
}

export interface StrategyOrderRow {
  run_id: string;
  started_at_utc: string | null;
  producer_system: string | null;
  source_config_id: string;
  signal_id: string;
  target_date: string;
  city: string;
  bracket: string;
  city_pool: string;
  forecast_source: string | null;
  model_version: string | null;
  model_p_yes: number | null;
  signal_market_price: number | null;
  signal_edge: number | null;
  condition_id: string | null;
  plan_id: string;
  execution_policy: string | null;
  skip_reason: string | null;
  execution_id: string;
  order_id: string | null;
  venue: string;
  order_side: string;
  order_status: string;
  limit_price: number | null;
  entry_price: number;
  order_shares: number;
  order_cost_usd: number;
  placed_at_utc: string | null;
  fill_id: string | null;
  fill_status: string | null;
  filled_shares: number | null;
  filled_price: number | null;
  fees_usd: number | null;
  filled_at_utc: string | null;
  final_price: number | null;
  settlement_status: string | null;
  pnl_usd: number | null;
}

export interface CompareRun {
  run_id: string;
  config_id?: string;
  execution_mode?: string;
  state?: string;
  date_range_start?: string | null;
  date_range_end?: string | null;
  tags?: string[];
  metrics: RunMetrics | null;
  error?: string;
}

// Bilingual metric labels: shown as "EN / ZH" in the UI
export const METRIC_LABELS: Record<keyof RunMetrics, { en: string; zh: string }> = {
  num_trades:        { en: "Trades",            zh: "交易数" },
  total_pnl_usd:     { en: "Total PnL",         zh: "总盈亏" },
  win_rate:          { en: "Win Rate",           zh: "胜率" },
  loss_rate:         { en: "Loss Rate",          zh: "亏损率" },
  avg_pnl_usd:       { en: "Avg PnL",           zh: "均值盈亏" },
  median_pnl_usd:    { en: "Median PnL",        zh: "中位盈亏" },
  pnl_trimmed_1pct:  { en: "Trimmed Mean",      zh: "截尾均值" },
  top1_pnl_share:    { en: "Top-1 Share",        zh: "最大单笔占比" },
  top5_pnl_share:    { en: "Top-5 Share",        zh: "前五占比" },
  total_cost_usd:    { en: "Total Cost",        zh: "总成本" },
  roi:               { en: "ROI",               zh: "回报率" },
  settled_trades:    { en: "Settled",           zh: "已结算" },
  unsettled_trades:  { en: "Unsettled",         zh: "未结算" },
  settled_ratio:     { en: "Settled %",         zh: "结算率" },
  worst_loss_usd:    { en: "Worst Loss",        zh: "最大单笔亏损" },
  best_win_usd:      { en: "Best Win",          zh: "最大单笔盈利" },
  expectancy_usd:    { en: "Expectancy",        zh: "期望值" },
  max_drawdown_usd:  { en: "Max Drawdown",      zh: "最大回撤" },
  fees_paid_usd:     { en: "Fees Paid",         zh: "手续费" },
  avg_win_usd:       { en: "Avg Win",           zh: "平均盈利" },
  avg_loss_usd:      { en: "Avg Loss",          zh: "平均亏损" },
};
