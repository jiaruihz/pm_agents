import { getJson, postJson } from "./http";
import { toQuery, type DashboardProvider } from "./provider";
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

export class HttpDashboardProvider implements DashboardProvider {
  async getHealth(): Promise<{ ok: boolean; now: string }> {
    return getJson<{ ok: boolean; now: string }>("/api/v1/health");
  }

  async listStrategies(limit = 200): Promise<StrategyItem[]> {
    const data = await getJson<{ strategies: StrategyItem[] }>(`/api/v1/strategies${toQuery({ limit })}`);
    return data.strategies ?? [];
  }

  async listInstances(params: Record<string, string | number | undefined> = {}): Promise<{ items: InstanceItem[]; total: number }> {
    const data = await getJson<{ instances: InstanceItem[]; total: number }>(`/api/v1/instances${toQuery(params)}`);
    return { items: data.instances ?? [], total: Number(data.total ?? 0) };
  }

  async getInstance(instanceId: string): Promise<InstanceItem> {
    const data = await getJson<{ item: InstanceItem }>(`/api/v1/instances/${encodeURIComponent(instanceId)}`);
    return data.item;
  }

  async getInstanceHistory(instanceId: string, limit = 240): Promise<InstanceHistoryPoint[]> {
    const data = await getJson<{ history: InstanceHistoryPoint[] }>(
      `/api/v1/instances/${encodeURIComponent(instanceId)}/history${toQuery({ limit })}`
    );
    return data.history ?? [];
  }

  async getTradeOrders(instanceId: string, limit = 200): Promise<OrderRecord[]> {
    const data = await getJson<{ orders: OrderRecord[] }>(
      `/api/v1/orders/${encodeURIComponent(instanceId)}${toQuery({ limit })}`
    );
    return data.orders ?? [];
  }

  async getTradeFills(orderId: string): Promise<FillRecord[]> {
    const data = await getJson<{ fills: FillRecord[] }>(
      `/api/v1/fills/${encodeURIComponent(orderId)}`
    );
    return data.fills ?? [];
  }

  async listAccounts(limit = 200, offset = 0): Promise<{ items: AccountAggregate[]; total: number }> {
    const data = await getJson<{ items: AccountAggregate[]; total: number }>(`/api/v1/accounts${toQuery({ limit, offset })}`);
    return { items: data.items ?? [], total: Number(data.total ?? 0) };
  }

  async listResearchMarkets(page = 1, pageSize = 40): Promise<{ items: ResearchMarketItem[]; total: number }> {
    const data = await getJson<{ items: ResearchMarketItem[]; total: number }>(
      `/api/v1/research/markets${toQuery({ page, page_size: pageSize })}`
    );
    return { items: data.items ?? [], total: Number(data.total ?? 0) };
  }

  async getResearchMarket(marketId: string): Promise<Record<string, unknown>> {
    return getJson<Record<string, unknown>>(`/api/v1/research/markets/${encodeURIComponent(marketId)}`);
  }

  async runResearchAction(action: "filter" | "parse" | "prompt" | "run_all", payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    return postJson<Record<string, unknown>>(`/api/v1/research/actions/${action}`, payload);
  }

  async listBacktestRuns(): Promise<BacktestRunItem[]> {
    const data = await getJson<{ runs: BacktestRunItem[] }>("/api/v1/backtests/runs");
    return data.runs ?? [];
  }

  async getBacktestTable(run: string, q = ""): Promise<{ rows: BacktestTableRow[]; summary: Record<string, unknown> }> {
    return getJson<{ rows: BacktestTableRow[]; summary: Record<string, unknown> }>(
      `/api/v1/backtests/table${toQuery({ run, q })}`
    );
  }

  async getBacktestScenario(run: string, scenarioRunId: string): Promise<Record<string, unknown>> {
    return getJson<Record<string, unknown>>(`/api/v1/backtests/scenario${toQuery({ run, scenario_run_id: scenarioRunId })}`);
  }

  async getOpsStatus(): Promise<Record<string, unknown>> {
    return getJson<Record<string, unknown>>("/api/v1/ops/status");
  }

  async getOpsLogs(name = "", lines = 120): Promise<Record<string, unknown>> {
    return getJson<Record<string, unknown>>(`/api/v1/ops/logs${toQuery({ name, lines })}`);
  }

  async getSupervisorSessions(limit = 20): Promise<Record<string, unknown>> {
    return getJson<Record<string, unknown>>(`/api/v1/supervisor/sessions${toQuery({ limit })}`);
  }
}
