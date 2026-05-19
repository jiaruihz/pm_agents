// HTTP client for the weather dashboard FastAPI backend
import type { RunSummary, RunDetail, LiveSummary, TradeDrilldown, TradeRow, ConfigRow, UniverseRow, CompareRun } from "./weather-types";

const BASE = (import.meta.env.VITE_WEATHER_API ?? "http://localhost:8000") + "/api";

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

  listConfigs(): Promise<ConfigRow[]> {
    return get("/configs");
  },

  listUniverses(): Promise<UniverseRow[]> {
    return get("/universes");
  },

  getLiveSummary(): Promise<LiveSummary> {
    return get("/live/summary");
  },

  getRunEquity(runId: string): Promise<{ date: string; cumulative_pnl: number }[]> {
    return get(`/runs/${runId}/equity`);
  },
};
