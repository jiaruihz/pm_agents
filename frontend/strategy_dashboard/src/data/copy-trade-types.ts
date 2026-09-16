export type CopyTradeSummary = {
  db_path: string;
  wallets_count: number;
  discoveries_count: number;
  reviews_count: number;
  latest_review_counts: Record<string, number>;
  full_scan_counts: Record<string, number>;
  paper_candidates: number;
  core_candidates: number;
};

export type CopyTradeWallet = {
  wallet_address: string;
  display_name: string;
  status: string;
  reviewed_at: string;
  score: number;
  verdict: "paper_candidate" | "watch" | "reject" | string;
  summary: string;
  reasons: string[];
  closed_positions_count: number;
  open_positions_count: number;
  net_pnl: number;
  non_election_pnl: number;
  profit_factor: number;
  sports_ratio: number;
  arbitrage_like_ratio: number;
  election_dependency: number;
  single_event_dependency: number;
  positive_single_event_dependency: number;
  avg_entry_price: number;
  market_diversity_ratio: number;
  history_truncated: boolean;
  scan_mode: string;
  topic_counts: Record<string, number>;
};

export type CopyTradeWalletList = {
  total: number;
  items: CopyTradeWallet[];
};

export type CopyTradeReview = {
  reviewed_at: string;
  score: number;
  verdict: string;
  summary: string;
  reasons: string[];
  metrics: Record<string, unknown>;
  display_name: string;
  status: string;
};

export type CopyTradeDiscovery = {
  method: string;
  source_ref: string;
  strength: number;
  tags: string[];
  discovered_at: string;
};

export type CopyTradePosition = {
  condition_id?: string;
  token_id?: string;
  title?: string;
  slug?: string;
  outcome?: string;
  size?: number;
  avg_price?: number;
  cur_price?: number;
  initial_value?: number;
  current_value?: number;
  cash_pnl?: number;
  percent_pnl?: number;
  realized_pnl?: number;
  raw: Record<string, unknown>;
};

export type CopyTradeWalletDetail = {
  wallet_address: string;
  reviews: CopyTradeReview[];
  discoveries: CopyTradeDiscovery[];
  open_positions: CopyTradePosition[];
  recent_closed_positions: CopyTradePosition[];
};

export type CapitalEfficiencyPosition = {
  condition_id?: string;
  token_id?: string;
  title?: string;
  slug?: string;
  event_slug?: string;
  outcome?: string;
  size: number;
  avg_price: number;
  mark_price: number;
  mark_value_usd: number;
  cost_basis_usd: number;
  entry_fees_usd: number;
  unrealized_pnl_usd: number;
  redeemable: boolean;
  end_date?: string;
  days_to_end?: number;
  status: "active" | "pending_resolution" | "unknown_end" | "redeemable" | "dust" | string;
  maturity_bucket: string;
  gross_payout_if_win_usd: number;
  remaining_upside_if_win_usd: number;
  gross_return_if_win?: number;
  annualized_simple_return_if_win?: number;
  required_confidence_for_hurdle?: number;
  hurdle_feasible?: boolean;
  best_bid?: number;
  best_ask?: number;
  visible_exit_value?: number;
  visible_exit_vwap?: number;
  exit_coverage_ratio: number;
  full_exit_value_usd?: number;
  exit_slippage_vs_mark_usd?: number;
};

export type CapitalEfficiencyScheduleRow = {
  end_date?: string;
  days_to_end?: number;
  positions_count: number;
  mark_value_usd: number;
  gross_payout_if_all_win_usd: number;
  remaining_upside_if_all_win_usd: number;
  portfolio_share: number;
  gross_return_if_all_win?: number;
  annualized_simple_return_if_all_win?: number;
};

export type CapitalEfficiencyReport = {
  wallet_address: string;
  display_name: string;
  observed_at_utc: string;
  annual_hurdle_rate: number;
  cash_inputs_complete: boolean;
  inputs: {
    available_cash_usd?: number;
    reserved_cash_usd?: number;
  };
  summary: {
    active_positions_count: number;
    active_mark_value_usd: number;
    active_cost_basis_usd: number;
    unrealized_pnl_vs_cost_usd: number;
    redeemable_value_usd: number;
    gross_payout_if_all_win_usd: number;
    remaining_upside_if_all_win_usd: number;
    weighted_avg_days_to_end?: number;
    capital_days_usd: number;
    portfolio_simple_annualized_return_if_all_win?: number;
    below_hurdle_positions_count: number;
    below_hurdle_mark_value_usd: number;
    below_hurdle_portfolio_share: number;
    visible_exit_value_usd: number;
    book_value_coverage_ratio: number;
    fully_quoted_mark_value_usd: number;
    full_exit_value_usd: number;
    full_exit_slippage_usd: number;
    accounted_capital_usd?: number;
    capital_utilization?: number;
  };
  maturity_schedule: CapitalEfficiencyScheduleRow[];
  maturity_buckets: Array<{
    bucket: string;
    positions_count: number;
    mark_value_usd: number;
    remaining_upside_if_all_win_usd: number;
    portfolio_share: number;
  }>;
  positions: CapitalEfficiencyPosition[];
  non_active_positions: CapitalEfficiencyPosition[];
  data_quality: {
    positions_received: number;
    positions_with_books: number;
    unknown_end_date_count: number;
    pending_resolution_count: number;
    positions_truncated: boolean;
    order_book_fetch_complete: boolean;
    cash_and_open_orders_publicly_complete: boolean;
    end_date_is_release_estimate: boolean;
  };
  sources: {
    positions: string;
    order_books: string;
    profile: string;
    books_requested: number;
    books_received: number;
    book_fetch_errors: string[];
    position_pages_fetched: number;
  };
};
