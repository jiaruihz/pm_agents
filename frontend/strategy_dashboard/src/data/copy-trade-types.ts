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
