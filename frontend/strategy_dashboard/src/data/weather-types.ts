// Weather dashboard API types — mirrors FastAPI schemas

export interface RunSummary {
  run_id: string;
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
}

export interface RunMetrics {
  num_trades: number;
  total_pnl_usd: number | null;
  win_rate: number | null;
  avg_pnl_usd: number | null;
  median_pnl_usd: number | null;
  pnl_trimmed_1pct: number | null;
  top1_pnl_share: number | null;
  top5_pnl_share: number | null;
  total_cost_usd: number | null;
  roi: number | null;
  settled_trades: number;
  unsettled_trades: number;
}

export interface RunDetail extends RunSummary {
  metrics: RunMetrics | null;
}

export interface TradeRow {
  signal_id: string;
  target_date: string | null;
  city: string | null;
  bracket: string | null;
  signal_side: string | null;
  model_version: string | null;
  model_p_yes: string | null;
  market_price: string | null;
  edge: string | null;
  order_id: string | null;
  order_side: string | null;
  entry_price: string | null;
  shares: string | null;
  cost_usd: string | null;
  fill_status: string | null;
  filled_at_utc: string | null;
  final_yes: number | null;
  settlement_status: string | null;
  pnl_usd: string | null;
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
  num_trades:       { en: "Trades",            zh: "交易数" },
  total_pnl_usd:    { en: "Total PnL",         zh: "总盈亏" },
  win_rate:         { en: "Win Rate",           zh: "胜率" },
  avg_pnl_usd:      { en: "Avg PnL",           zh: "均值盈亏" },
  median_pnl_usd:   { en: "Median PnL",        zh: "中位盈亏" },
  pnl_trimmed_1pct: { en: "Trimmed Mean",      zh: "截尾均值" },
  top1_pnl_share:   { en: "Top-1 PnL Share",   zh: "最大单笔占比" },
  top5_pnl_share:   { en: "Top-5 PnL Share",   zh: "前五占比" },
  total_cost_usd:   { en: "Total Cost",        zh: "总成本" },
  roi:              { en: "ROI",               zh: "投资回报率" },
  settled_trades:   { en: "Settled",           zh: "已结算" },
  unsettled_trades: { en: "Unsettled",         zh: "未结算" },
};
