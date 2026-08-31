// HTTP client for the weather dashboard FastAPI backend
import type { RunSummary, RunDetail, LiveSummary, LivePosition, ExecutionGapRow, TradeDrilldown, TradeRow, ConfigRow, UniverseRow, CompareRun, StrategyRow, EquityPoint, StrategyAnalytics, PositionRow, FunnelRow, PendingOrderRow, StrategyOrderRow, MarkToMarketSummary, WeatherEdgeV2Latest, StrategyRuntimeOverview, StrategyRuntimeDetail, StrategyDefinitionRow, StrategyDefinitionDetail, StrategyInstanceRow, StrategyInstanceDetail } from "./weather-types";
import type { CapitalEfficiencyReport, CopyTradeSummary, CopyTradeWalletDetail, CopyTradeWalletList } from "./copy-trade-types";
import type { ProbeHealthResponse, ProbeDetail, ResearchLinesResponse, ResearchLineDetail, GlossaryResponse, LiveBookResponse, LiveBookStrategiesResponse, DataSourcesResponse, OrderBlotterResponse, OrderBlotterDailySummaryResponse } from "./v2-types";

const BASE = `${import.meta.env.VITE_WEATHER_API ?? ""}/api`;

async function get<T>(path: string, params?: Record<string, string | number | undefined>): Promise<T> {
  const usp = new URLSearchParams();
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null && String(v) !== "") usp.set(k, String(v));
    });
  }
  const qs = usp.toString();
  const res = await fetch(`${BASE}${path}${qs ? `?${qs}` : ""}`, {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status} ${path}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export const weatherApi = {
  listRuns(params?: {
    state?: string;
    execution_mode?: string;
    config_id?: string;
    limit?: number;
    offset?: number;
  }): Promise<RunSummary[]> {
    return get("/runs", params);
  },

  getRun(runId: string, includeMetrics = true): Promise<RunDetail> {
    return get(`/runs/${runId}`, { include_metrics: includeMetrics ? 1 : 0 });
  },

  getRunTrades(
    runId: string,
    params?: {
      city?: string;
      target_date?: string;
      city_pool?: string;
      forecast_source?: string;
      limit?: number;
      offset?: number;
    },
  ): Promise<TradeRow[]> {
    return get(`/runs/${runId}/trades`, params);
  },

  getTradeDrilldown(runId: string, signalId: string): Promise<TradeDrilldown> {
    return get(`/runs/${runId}/trades/${signalId}`);
  },

  getRunMetricsSlice(
    runId: string,
    groupBy: "city_pool" | "forecast_source" | "model_version" | "side" | "city" | "target_date" | "bracket",
  ): Promise<{ group_by: string; slices: { slice_value: string | null; num_trades: number; settled_trades: number; total_pnl_usd: number; win_rate: number | null; total_cost_usd: number; roi: number | null }[] }> {
    return get(`/runs/${runId}/metrics/slice`, { group_by: groupBy });
  },

  compareRuns(runIds: string[]): Promise<{ runs: CompareRun[] }> {
    return get("/compare", { run_ids: runIds.join(",") });
  },

  listStrategies(params?: { state?: "live" | "paper" | "explore" | "all" }): Promise<StrategyRow[]> {
    return get("/strategies", params);
  },

  getStrategy(configId: string, params?: { state?: "live" | "paper" | "explore" | "all" }): Promise<StrategyRow> {
    return get(`/strategies/${encodeURIComponent(configId)}`, params);
  },

  getStrategyEquity(configId: string, params?: { state?: "live" | "paper" | "explore" | "all" }): Promise<EquityPoint[]> {
    return get(`/strategies/${encodeURIComponent(configId)}/equity`, params);
  },

  getStrategyAnalytics(configId: string, params?: { state?: "live" | "paper" | "explore" | "all" }): Promise<StrategyAnalytics> {
    return get(`/strategies/${encodeURIComponent(configId)}/analytics`, params);
  },

  getStrategyPositions(configId: string, params?: { state?: "live" | "paper" | "explore" | "all" }): Promise<PositionRow[]> {
    return get(`/strategies/${encodeURIComponent(configId)}/positions`, params);
  },

  getStrategyMarkToMarket(configId: string, params?: { state?: "live" | "paper" | "explore" | "all" }): Promise<MarkToMarketSummary> {
    return get(`/strategies/${encodeURIComponent(configId)}/mark-to-market`, params);
  },

  getStrategyOrders(
    configId: string,
    params?: {
      state?: "live" | "paper" | "explore" | "all";
      venue?: "paper" | "polymarket_clob" | "snapshot_replay";
      target_date?: string;
      limit?: number;
      offset?: number;
    },
  ): Promise<StrategyOrderRow[]> {
    return get(`/strategies/${encodeURIComponent(configId)}/orders`, params);
  },

  getStrategyFunnel(configId: string): Promise<FunnelRow[]> {
    return get(`/strategies/${encodeURIComponent(configId)}/funnel`);
  },

  getStrategyPendingOrders(configId: string): Promise<PendingOrderRow[]> {
    return get(`/strategies/${encodeURIComponent(configId)}/pending-orders`);
  },

  listConfigs(): Promise<ConfigRow[]> {
    return get("/configs");
  },

  listStrategyDefinitions(): Promise<StrategyDefinitionRow[]> {
    return get("/strategy-definitions");
  },

  getStrategyDefinition(strategyKey: string): Promise<StrategyDefinitionDetail> {
    return get(`/strategy-definitions/${encodeURIComponent(strategyKey)}`);
  },

  listStrategyInstances(params?: { strategy_key?: string; config_id?: string }): Promise<StrategyInstanceRow[]> {
    return get("/strategy-instances", params);
  },

  getStrategyInstance(instanceId: string): Promise<StrategyInstanceDetail> {
    return get(`/strategy-instances/${encodeURIComponent(instanceId)}`);
  },

  listUniverses(): Promise<UniverseRow[]> {
    return get("/universes");
  },

  getLiveSummary(): Promise<LiveSummary> {
    return get("/live/summary");
  },

  getLivePositions(params?: {
    status?: "open" | "settled" | "all";
    limit?: number;
    offset?: number;
  }): Promise<LivePosition[]> {
    return get("/live/positions", params);
  },

  getExecutionGap(params?: {
    limit?: number;
    offset?: number;
  }): Promise<ExecutionGapRow[]> {
    return get("/live/execution-gap", params);
  },

  getWeatherEdgeV2Latest(): Promise<WeatherEdgeV2Latest> {
    return get("/research/weather-edge-v2/latest");
  },

  getStrategyRuntimeOverview(params?: { target_date?: string }): Promise<StrategyRuntimeOverview> {
    return get("/strategy-runtime/overview", params);
  },

  getStrategyRuntimeDetail(strategyInstance: string, params?: { limit?: number }): Promise<StrategyRuntimeDetail> {
    return get(`/strategy-runtime/${encodeURIComponent(strategyInstance)}/detail`, params);
  },

  getRunEquity(runId: string): Promise<{ date: string; cumulative_pnl: number }[]> {
    return get(`/runs/${runId}/equity`);
  },

  getCopyTradeSummary(): Promise<CopyTradeSummary> {
    return get("/copy-trade/summary");
  },

  listCopyTradeWallets(params?: {
    verdict?: string;
    scan_mode?: string;
    limit?: number;
    offset?: number;
  }): Promise<CopyTradeWalletList> {
    return get("/copy-trade/wallets", params);
  },

  getCopyTradeWallet(walletAddress: string): Promise<CopyTradeWalletDetail> {
    return get(`/copy-trade/wallets/${encodeURIComponent(walletAddress)}`);
  },

  getCapitalEfficiency(params: {
    wallet_address: string;
    annual_hurdle_rate?: number;
    available_cash_usd?: number;
    reserved_cash_usd?: number;
  }): Promise<CapitalEfficiencyReport> {
    return get("/copy-trade/capital-efficiency", params);
  },

  // ── v2 redesign endpoints ─────────────────────────────────────────────────
  getProbeHealth(): Promise<ProbeHealthResponse> {
    return get("/probes/health");
  },

  getProbe(instance: string): Promise<ProbeDetail> {
    return get(`/probes/${encodeURIComponent(instance)}`);
  },

  getResearchLines(): Promise<ResearchLinesResponse> {
    return get("/research/lines");
  },

  getResearchLine(lineId: string): Promise<ResearchLineDetail> {
    return get(`/research/lines/${encodeURIComponent(lineId)}`);
  },

  getGlossary(): Promise<GlossaryResponse> {
    return get("/glossary");
  },

  getLiveBook(params?: { status?: string; target_date?: string; limit?: number }): Promise<LiveBookResponse> {
    return get("/live/book", params);
  },

  getLiveBookStrategies(): Promise<LiveBookStrategiesResponse> {
    return get("/live/book/strategies");
  },

  getDataSources(): Promise<DataSourcesResponse> {
    return get("/data-sources");
  },

  getOrderBlotter(params?: {
    trade_class?: string;
    status?: string;
    instance_id?: string;
    config_id?: string;
    strategy_key?: string;
    strategy_id?: string;
    target_date?: string;
    city?: string;
    limit?: number;
    offset?: number;
  }): Promise<OrderBlotterResponse> {
    return get("/order-blotter", params);
  },

  getOrderBlotterDailySummary(params?: {
    trade_class?: string;
    status?: string;
    instance_id?: string;
    config_id?: string;
    strategy_key?: string;
    strategy_id?: string;
    target_date?: string;
    city?: string;
  }): Promise<OrderBlotterDailySummaryResponse> {
    return get("/order-blotter/daily-summary", params);
  },
};
