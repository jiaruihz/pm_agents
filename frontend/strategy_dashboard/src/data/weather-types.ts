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

export interface LiveSummary {
  by_target_date: {
    target_date: string;
    orders: number;
    cities: number;
    clob_orders: number;
    paper_orders: number;
    submitted_orders: number;
    notional_usd: number | null;
    first_order_at_utc: string | null;
    last_order_at_utc: string | null;
  }[];
  strategy_versions: {
    config_id: string;
    name: string;
    params: Record<string, unknown>;
    runs: number;
    orders: number;
    notional_usd: number | null;
  }[];
  today_account: {
    order_date_utc: string;
    orders: number;
    clob_orders: number;
    paper_orders: number;
    submitted_orders: number;
    notional_usd: number | null;
    cities: number;
    target_dates: number;
  } | null;
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
