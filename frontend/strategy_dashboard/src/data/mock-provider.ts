import { type DashboardProvider } from "./provider";
import type {
  AccountAggregate,
  BacktestRunItem,
  BacktestTableRow,
  InstanceHistoryPoint,
  InstanceItem,
  ResearchMarketItem,
  StrategyItem,
} from "./types";

const strategies: StrategyItem[] = [
  {
    strategy_key: "multi_level_v1",
    strategy_name: "多档做市",
    strategy_group: "market_making",
    strategy_family: "maker",
    domain: "pmm",
    is_active: true,
    description: "盘口锚定 + 多层报价",
    running_instances: 2,
    total_instances: 7,
  },
  {
    strategy_key: "rule_lawyer",
    strategy_name: "规则律师",
    strategy_group: "rule_intelligence",
    strategy_family: "advisory",
    domain: "research",
    is_active: true,
    description: "规则解析 + 争议风险评估",
    running_instances: 1,
    total_instances: 3,
  },
  {
    strategy_key: "smart_money_follow_v1",
    strategy_name: "聪明钱跟随",
    strategy_group: "smart_money",
    strategy_family: "directional",
    domain: "pmm",
    is_active: true,
    description: "信号驱动报价倾斜",
    running_instances: 1,
    total_instances: 4,
  },
];

const instances: InstanceItem[] = [
  {
    instance_id: "ins_mm_001",
    strategy_key: "multi_level_v1",
    strategy_name: "多档做市",
    strategy_group: "market_making",
    label: "ETH ETF Odds / paper",
    status: "running",
    runtime_status: "running",
    execution_mode: "paper",
    market_data_source: "http",
    account_id: "acct-demo-1",
    wallet_address: "",
    token_ids: ["yes_1", "no_1"],
    heartbeat_age_sec: 12,
    last_pnl: 42.18,
    last_equity: 1042.18,
    last_usdc: 653.1,
    open_orders: 8,
    fills_total: 220,
    placed_total: 392,
    canceled_total: 172,
    errors_total: 0,
    started_at_utc: new Date(Date.now() - 3 * 3600 * 1000).toISOString(),
  },
  {
    instance_id: "ins_rl_007",
    strategy_key: "rule_lawyer",
    strategy_name: "规则律师",
    strategy_group: "rule_intelligence",
    label: "US election rule parse",
    status: "running",
    runtime_status: "stale",
    execution_mode: "backtest",
    market_data_source: "file",
    account_id: "",
    wallet_address: "0xabc999",
    token_ids: ["r_yes", "r_no"],
    heartbeat_age_sec: 188,
    last_pnl: 8.55,
    last_equity: 208.55,
    last_usdc: 140.22,
    open_orders: 0,
    fills_total: 0,
    placed_total: 0,
    canceled_total: 0,
    errors_total: 1,
    started_at_utc: new Date(Date.now() - 11 * 3600 * 1000).toISOString(),
  },
];

function history(instanceId: string): InstanceHistoryPoint[] {
  const base = instanceId === "ins_mm_001" ? 1000 : 200;
  return Array.from({ length: 120 }).map((_, i) => ({
    ts_utc: new Date(Date.now() - (120 - i) * 60_000).toISOString(),
    tick: i,
    pnl: Math.sin(i / 11) * 10 + i * 0.1,
    equity: base + Math.sin(i / 11) * 10 + i * 0.1,
    usdc: base * 0.65 + Math.cos(i / 13) * 5,
    open_orders: Math.max(0, Math.floor(10 + Math.sin(i / 7) * 3)),
  }));
}

const accounts: AccountAggregate[] = [
  {
    group_key: "acct-demo-1",
    account_id: "acct-demo-1",
    wallet_address: "",
    equity_total: 1042.18,
    usdc_total: 653.1,
    pnl_total: 42.18,
    open_orders_total: 8,
    running_instances: 1,
    instances: [instances[0]],
  },
  {
    group_key: "0xabc999",
    account_id: "",
    wallet_address: "0xabc999",
    equity_total: 208.55,
    usdc_total: 140.22,
    pnl_total: 8.55,
    open_orders_total: 0,
    running_instances: 1,
    instances: [instances[1]],
  },
];

const researchMarkets: ResearchMarketItem[] = [
  {
    market_id: "m_1",
    slug: "will-eth-etf-approve-q2",
    question: "Will ETH ETF be approved in Q2?",
    category: "Crypto",
    active: true,
    resolved: false,
    status: "READY_TO_PARSE",
    updated_at_utc: new Date().toISOString(),
    volume: 120000,
    liquidity: 45000,
  },
  {
    market_id: "m_2",
    slug: "will-us-rate-cut-before-sep",
    question: "Will Fed cut rates before Sep?",
    category: "Macro",
    active: true,
    resolved: false,
    status: "PARSED",
    updated_at_utc: new Date().toISOString(),
    volume: 300000,
    liquidity: 180000,
  },
];

const runs: BacktestRunItem[] = [
  { name: "results_all_20260304", type: "results_all", rows: 64 },
  { name: "fill_models_20260304", type: "fill_models", rows: 120 },
];

const rows: BacktestTableRow[] = Array.from({ length: 24 }).map((_, i) => ({
  scenario_id: `sc_${i + 1}`,
  scenario_run_id: `sc_${i + 1}_run`,
  profile_name: i % 2 ? "conservative" : "optimistic",
  strategy_key: i % 3 ? "multi_level_v1" : "single_level_v1",
  fill_model: i % 2 ? "conservative" : "optimistic",
  pnl_end: 10 - i * 0.38,
  max_drawdown: 0.03 + i * 0.002,
  total_fills: 100 + i * 3,
  total_placed: 200 + i * 4,
  fill_rate_per_order: 0.5 + Math.sin(i) * 0.08,
}));

export class MockDashboardProvider implements DashboardProvider {
  async getHealth(): Promise<{ ok: boolean; now: string }> {
    return { ok: true, now: new Date().toISOString() };
  }

  async listStrategies(): Promise<StrategyItem[]> {
    return strategies;
  }

  async listInstances(): Promise<{ items: InstanceItem[]; total: number }> {
    return { items: instances, total: instances.length };
  }

  async getInstance(instanceId: string): Promise<InstanceItem> {
    return instances.find((x) => x.instance_id === instanceId) ?? instances[0];
  }

  async getInstanceHistory(instanceId: string): Promise<InstanceHistoryPoint[]> {
    return history(instanceId);
  }

  async listAccounts(): Promise<{ items: AccountAggregate[]; total: number }> {
    return { items: accounts, total: accounts.length };
  }

  async listResearchMarkets(): Promise<{ items: ResearchMarketItem[]; total: number }> {
    return { items: researchMarkets, total: researchMarkets.length };
  }

  async getResearchMarket(marketId: string): Promise<Record<string, unknown>> {
    return {
      item: {
        market_id: marketId,
        question: "Mock market detail",
        analysis: { alpha_score: 82, clarity_score: 0.86 },
        evidence: { verification_result: "likely_resolvable" },
      },
    };
  }

  async runResearchAction(action: "filter" | "parse" | "prompt" | "run_all", payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    return {
      action,
      result: {
        ok: true,
        mode: "mock",
        payload,
        ts: new Date().toISOString(),
      },
    };
  }

  async listBacktestRuns(): Promise<BacktestRunItem[]> {
    return runs;
  }

  async getBacktestTable(): Promise<{ rows: BacktestTableRow[]; summary: Record<string, unknown> }> {
    return {
      rows,
      summary: {
        avg_pnl_end: 2.77,
        avg_max_drawdown: 0.048,
      },
    };
  }

  async getBacktestScenario(run: string, scenarioRunId: string): Promise<Record<string, unknown>> {
    return {
      run,
      scenario_run_id: scenarioRunId,
      summary: {
        pnl_end: 5.12,
        fill_rate_per_order: 0.61,
      },
    };
  }

  async getOpsStatus(): Promise<Record<string, unknown>> {
    return {
      running: true,
      pid: 12031,
      uptime_text: "03:21:08",
      latest_tick_summary: { tick: 8452, pnl: 42.18, equity: 1042.18 },
      instance_count: 2,
      instance_running: 2,
      log_files: [
        { name: "pmm_paper_live.log", size_bytes: 1024 * 50, mtime: new Date().toISOString() },
      ],
      latest_log: "pmm_paper_live.log",
      commands: {
        start_foreground: "python -m src.strategies.pmm.main",
        stop: "kill $(cat runtime/pmm_run.pid)",
      },
      config_snapshot: { PMM_EXECUTION_MODE: "paper", PMM_STRATEGY_KEY: "multi_level_v1" },
    };
  }

  async getOpsLogs(name = "pmm_paper_live.log", lines = 120): Promise<Record<string, unknown>> {
    return {
      name,
      lines,
      text: "[tick_summary] tick=8452 pnl=42.18 equity=1042.18\n[tick_summary] tick=8453 pnl=42.42 equity=1042.42",
      log_files: [{ name, size_bytes: 1024 * 50, mtime: new Date().toISOString() }],
    };
  }

  async getSupervisorSessions(): Promise<Record<string, unknown>> {
    return {
      sessions: [
        {
          name: "shadow_8h_20260304",
          status: "running",
          cycles_done: 3,
          cycles_planned: 8,
          market_count: 14,
          completed_runs: 21,
          inflight_markets: ["m_1", "m_2"],
          last_update: new Date().toISOString(),
        },
      ],
      now: new Date().toISOString(),
      root: "runtime/supervisor_logs",
    };
  }
}
