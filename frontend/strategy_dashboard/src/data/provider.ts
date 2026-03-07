import type {
  AccountAggregate,
  BacktestRunItem,
  BacktestTableRow,
  InstanceHistoryPoint,
  InstanceItem,
  ResearchMarketItem,
  StrategyItem,
  OrderRecord,
  FillRecord,
} from "./types";

export interface DashboardProvider {
  getHealth(): Promise<{ ok: boolean; now: string }>;
  listStrategies(limit?: number): Promise<StrategyItem[]>;
  listInstances(params?: Record<string, string | number | undefined>): Promise<{ items: InstanceItem[]; total: number }>;
  getInstance(instanceId: string): Promise<InstanceItem>;
  getInstanceHistory(instanceId: string, limit?: number): Promise<InstanceHistoryPoint[]>;
  getTradeOrders(instanceId: string, limit?: number): Promise<OrderRecord[]>;
  getTradeFills(orderId: string): Promise<FillRecord[]>;
  listAccounts(limit?: number, offset?: number): Promise<{ items: AccountAggregate[]; total: number }>;
  listResearchMarkets(page?: number, pageSize?: number): Promise<{ items: ResearchMarketItem[]; total: number }>;
  getResearchMarket(marketId: string): Promise<Record<string, unknown>>;
  runResearchAction(action: "filter" | "parse" | "prompt" | "run_all", payload: Record<string, unknown>): Promise<Record<string, unknown>>;
  listBacktestRuns(): Promise<BacktestRunItem[]>;
  getBacktestTable(run: string, q?: string): Promise<{ rows: BacktestTableRow[]; summary: Record<string, unknown> }>;
  getBacktestScenario(run: string, scenarioRunId: string): Promise<Record<string, unknown>>;
  getOpsStatus(): Promise<Record<string, unknown>>;
  getOpsLogs(name?: string, lines?: number): Promise<Record<string, unknown>>;
  getSupervisorSessions(limit?: number): Promise<Record<string, unknown>>;
}

export function toQuery(params: Record<string, string | number | undefined>): string {
  const usp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v === undefined || v === null || String(v) === "") return;
    usp.set(k, String(v));
  });
  const q = usp.toString();
  return q ? `?${q}` : "";
}
