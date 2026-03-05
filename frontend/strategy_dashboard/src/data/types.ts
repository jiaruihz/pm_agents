export type RuntimeStatus = "running" | "stopped" | "error" | "stale" | "unknown";

export interface StrategyItem {
  strategy_key: string;
  strategy_name: string;
  strategy_group: string;
  strategy_family: string;
  domain: string;
  is_active: boolean;
  description: string;
  running_instances: number;
  total_instances: number;
  meta?: Record<string, unknown>;
}

export interface InstanceItem {
  instance_id: string;
  strategy_key: string;
  strategy_name?: string;
  strategy_group?: string;
  label: string;
  status: string;
  runtime_status?: RuntimeStatus;
  execution_mode: string;
  market_data_source: string;
  account_id: string;
  wallet_address: string;
  token_ids: string[];
  heartbeat_age_sec?: number;
  started_at_utc?: string;
  updated_at_utc?: string;
  heartbeat_at_utc?: string;
  last_pnl?: number;
  last_equity?: number;
  last_usdc?: number;
  open_orders?: number;
  fills_total?: number;
  placed_total?: number;
  canceled_total?: number;
  errors_total?: number;
  run_params?: Record<string, unknown>;
  state?: Record<string, unknown>;
}

export interface InstanceHistoryPoint {
  ts_utc: string;
  tick: number;
  pnl: number;
  equity: number;
  usdc: number;
  open_orders: number;
}

export interface AccountAggregate {
  group_key: string;
  account_id: string;
  wallet_address: string;
  equity_total: number;
  usdc_total: number;
  pnl_total: number;
  open_orders_total: number;
  running_instances: number;
  instances: InstanceItem[];
}

export interface ResearchMarketItem {
  market_id: string;
  slug: string;
  question: string;
  category: string;
  active: boolean;
  resolved: boolean;
  status: string;
  updated_at_utc: string;
  volume?: number;
  liquidity?: number;
}

export interface BacktestRunItem {
  name: string;
  type: string;
  rows: number;
}

export interface BacktestTableRow {
  scenario_id: string;
  scenario_run_id: string;
  profile_name: string;
  strategy_key: string;
  fill_model: string;
  pnl_end: number;
  max_drawdown: number;
  total_fills: number;
  total_placed: number;
  fill_rate_per_order: number;
}

export interface ApiError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
  request_id?: string;
  timestamp_utc?: string;
}
