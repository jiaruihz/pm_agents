import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import type { StrategyRuntimeDetail, StrategyRuntimeOverview, StrategyRuntimeRow, StrategyShadowQueueRow } from "../../data/weather-types";

const DEFAULT_RUNTIME_STRATEGY = "low_price_yes_lottery_tiny_live_v1";

function tomorrowLocal(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function fmtInt(value: number | null | undefined): string {
  if (value == null) return "-";
  return value.toLocaleString();
}

function fmtAge(minutes: number | null): string {
  if (minutes == null) return "-";
  if (minutes < 90) return `${minutes.toFixed(1)}m`;
  if (minutes < 48 * 60) return `${(minutes / 60).toFixed(1)}h`;
  return `${(minutes / 1440).toFixed(1)}d`;
}

function fmtTime(value: string | null | undefined): string {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString();
}

function fmtMoney(value: number | null): string {
  if (value == null) return "-";
  return `$${value.toFixed(2)}`;
}

function shortPath(value: string | null | undefined): string {
  if (!value) return "-";
  const parts = value.split("/");
  return parts.slice(Math.max(0, parts.length - 3)).join("/");
}

function shortId(value: string | null | undefined): string {
  if (!value) return "-";
  return value.length <= 20 ? value : `${value.slice(0, 10)}...${value.slice(-6)}`;
}

function listText(items: unknown[] | undefined, limit = 2): string {
  if (!items || items.length === 0) return "-";
  return items.slice(0, limit).map((item) => String(item)).join("; ");
}

function valueOf(obj: Record<string, unknown> | undefined, key: string): unknown {
  return obj ? obj[key] : undefined;
}

function fmtUnknown(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(3);
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function fmtPrice(value: unknown): string {
  const n = Number(value);
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}c` : "-";
}

function fmtUsd(value: unknown): string {
  const n = Number(value);
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : "-";
}

function fmtShares(value: unknown): string {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(2) : "-";
}

function fmtWeight(value: unknown): string {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(3) : "-";
}

function compactJson(value: unknown): string {
  if (!value || typeof value !== "object") return "-";
  return Object.entries(value as Record<string, unknown>)
    .map(([k, v]) => `${k}: ${fmtUnknown(v)}`)
    .join(" · ") || "-";
}

function statusColor(status: string): string {
  if (["healthy", "target_seen", "live"].includes(status)) return "var(--ok)";
  if (["blocked", "stale", "shelved"].includes(status)) return "var(--bad)";
  if (["collecting", "live_waiting", "shadow_waiting", "monitoring", "telemetry", "shadow"].includes(status)) return "var(--accent)";
  return "var(--muted)";
}

function targetLabel(status: string): string {
  const labels: Record<string, string> = {
    target_seen: "target seen",
    live_waiting: "live waiting",
    shadow_waiting: "shadow waiting",
    collecting: "collecting",
    monitoring: "monitoring",
    blocked: "blocked",
    stale: "stale",
    shelved: "shelved",
    unknown: "unknown",
  };
  return labels[status] ?? status;
}

function policyLabel(value: string): string {
  const labels: Record<string, string> = {
    soft_balanced_live_policy: "live soft",
    soft_wind_only_shadow: "wind only",
    soft_wind_context_shadow: "wind ctx",
    soft_temp_context_light_shadow: "temp light",
    soft_temp_context_medium_shadow: "temp medium",
  };
  return labels[value] ?? value.replace(/_/g, " ");
}

function strategyDescriptionZh(row: StrategyRuntimeRow): string {
  const byInstance: Record<string, string> = {
    low_price_yes_lottery_tiny_live_v1: "低价 YES 彩票型小仓实盘：在 5c-20c 的温度 exact bracket YES 里找高 edge、低 notional 的候选，主要用于前向小额收集证据，不是已确认主力 alpha。",
    theta_current_yes_fade_confirmed_tiny_live_v1: "当前档 YES fade 小仓实盘：寻找高温已经走弱、当前 exact bracket YES 可能被高估的形态；当前心跳陈旧时只代表历史注册，不代表正在新鲜执行。",
    metar_cross_prev_no_live_v1: "METAR 跨越前值 NO 实盘链路：围绕新观测打印后对上一档/相邻档 NO 的机会做反应，偏事件驱动，需重点看最新心跳。",
    regime_routed_no_route_price_disciplined_tiny_live_v1: "Regime-routed NO 小仓实盘：按天气路径状态选择 NO 表达，并用 route price discipline 控制入场价格和订单质量。",
    regime_routed_no_soft_balanced_tiny_live_v1: "Regime-routed NO soft-balanced 小仓实盘：同一 NO 方向，但用软 sizing 平衡温度状态、盘口和风险，上线性质是前向证据收集。",
    regime_routed_no_soft_balanced_shadow_v1: "Regime-routed NO 零 notional shadow：复用实盘表达和 sizing 逻辑，只记录候选与影子结果，不真实下单。",
    tmax_distribution_edge_shadow_v1: "Tmax distribution edge shadow：估计 current/d1/d2/tail 四类最终最高温分布，再选择最有价值的 exact bracket 表达；当前只做 shadow。",
    range_rv_shadow_v0: "Range RV shadow：围绕预测边界内外的 range/reversion 机会做零 notional 记录，用于评估 forecast-bounded 表达是否有执行价值。",
    theta_higher_no_carry_shadow_v1: "Higher NO carry shadow：观察更高温档 NO 是否存在 carry/衰减机会，当前是影子/研究线。",
    theta_current_yes_peak_forming_micro_tiny_live_v1: "Current YES peak-forming telemetry：记录当前档 YES 在峰值形成阶段的微仓/遥测行为；要按 latest summary 判断是否只是 telemetry。",
    source_orderbook_timing_monitor: "数据时序监控：检查 source event、orderbook、snapshot 的延迟和同步质量；这是监控，不是交易策略。",
    weather_runtime_monitor: "运行时监控：只读检查 live/shadow runner 的心跳、快照、executor 和空转状态；这是运维监控。",
    low_price_yes_reheat_reversal_shadow_v1: "低价 YES reheat reversal shadow：研究低价 YES 在重新升温路径下的反转机会，当前 blocked/stale 时只保留方向和历史记录。",
    station_basis_shadow_v1: "Station-basis shadow：用站点/城市 source basis 差异做影子信号，重点验证观测源偏差是否能转成可执行 edge。",
    all_yes_underround_shadow_v0: "All-YES underround shadow：检查同一市场所有 YES 价格合计是否低估，用于 market-structure 方向的影子研究。",
  };
  if (byInstance[row.strategy_instance]) return byInstance[row.strategy_instance];

  const byFamily: Record<string, string> = {
    "forecast_quality.low_price_yes_lottery": "低价 YES 方向：寻找低概率高赔率的温度 bracket YES 候选。",
    "reheat_risk.regime_routed_no": "Regime-routed NO 方向：根据温度路径状态选择 NO 表达并控制执行风险。",
    "reheat_risk.current_yes": "Current YES 方向：围绕当前最高温档 YES 的成败与 overshoot 风险做判断。",
    "reheat_risk.tmax_distribution_edge": "Tmax 分布方向：先估计最终最高温分布，再选择 bracket 表达。",
    data_quality: "数据质量/延迟监控，不是交易策略。",
  };
  return byFamily[row.family] ?? row.notes ?? "该实例缺少中文说明；可先查看英文 notes 和最新 heartbeat。";
}

export function WeatherStrategyRuntimePage(): JSX.Element {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedInstance = searchParams.get("instance_id");
  const [targetDate, setTargetDate] = useState(tomorrowLocal);
  const [data, setData] = useState<StrategyRuntimeOverview | null>(null);
  const [detail, setDetail] = useState<StrategyRuntimeDetail | null>(null);
  const [selectedStrategy, setSelectedStrategy] = useState(requestedInstance || DEFAULT_RUNTIME_STRATEGY);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    weatherApi
      .getStrategyRuntimeOverview({ target_date: targetDate })
      .then((res) => {
        setData(res);
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [targetDate, refreshNonce]);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const id = window.setInterval(() => setRefreshNonce((x) => x + 1), 30000);
    return () => window.clearInterval(id);
  }, [autoRefresh]);

  const activeRows = useMemo(() => data?.strategies ?? [], [data]);
  const queueRows = data?.shadow_queue ?? [];
  const groupedRows = useMemo(() => groupRuntimeRows(activeRows), [activeRows]);

  useEffect(() => {
    if (requestedInstance) setSelectedStrategy(requestedInstance);
  }, [requestedInstance]);

  useEffect(() => {
    if (!data || activeRows.length === 0) return;
    if (activeRows.some((row) => row.strategy_instance === selectedStrategy)) return;
    const preferred = activeRows.find((row) => row.strategy_instance === DEFAULT_RUNTIME_STRATEGY)
      ?? activeRows.find((row) => row.lifecycle_status === "live" && row.health_status === "healthy")
      ?? activeRows.find((row) => row.lifecycle_status === "live")
      ?? activeRows[0];
    setSelectedStrategy(preferred.strategy_instance);
  }, [activeRows, data, selectedStrategy]);

  useEffect(() => {
    if (!selectedStrategy) return;
    weatherApi
      .getStrategyRuntimeDetail(selectedStrategy, { limit: 12 })
      .then((res) => {
        setDetail(res);
        setDetailError(null);
      })
      .catch((e: Error) => {
        setDetail(null);
        setDetailError(e.message);
      });
  }, [selectedStrategy, refreshNonce, data?.refreshed_at_utc]);

  return (
    <PageFrame title="Strategy Runtime" desc="live · shadow · telemetry · target-date status">
      <>
        <section style={toolbarStyle}>
          <div>
            <div style={{ fontSize: 12, color: "var(--muted)" }}>Target date</div>
            <input
              type="date"
              value={targetDate}
              onChange={(e) => setTargetDate(e.target.value)}
              style={dateInputStyle}
            />
          </div>
          <div style={{ marginLeft: "auto", color: "var(--muted)", fontSize: 12 }}>
            refreshed {fmtTime(data?.refreshed_at_utc)}
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--muted)" }}>
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            auto 30s
          </label>
          <button onClick={() => setRefreshNonce((x) => x + 1)}>Refresh</button>
        </section>

        {loading && <div style={mutedBlockStyle}>Loading strategy runtime registry...</div>}
        {error && <div style={errorStyle}>{error}</div>}

        {data && !loading && !error && (
          <div style={stack16Style}>
            <SummaryGrid data={data} />
            <section style={panelStyle}>
              <div style={sectionHeadStyle}>
                <div>
                  <h2 style={sectionTitleStyle}>Live Monitor</h2>
                  <div style={subtleStyle}>selected runtime · recent heartbeat/orders</div>
                </div>
                <select value={selectedStrategy} onChange={(e) => {
                  const instanceId = e.target.value;
                  setSelectedStrategy(instanceId);
                  setSearchParams((prev) => {
                    const next = new URLSearchParams(prev);
                    next.set("instance_id", instanceId);
                    return next;
                  });
                }}>
                  {activeRows.map((row) => (
                    <option key={row.strategy_instance} value={row.strategy_instance}>
                      {row.display_name}
                    </option>
                  ))}
                </select>
              </div>
              {detailError && <div style={errorStyle}>{detailError}</div>}
              {detail && <RuntimeDetailPanel detail={detail} />}
            </section>

            <section style={panelStyle}>
              <div style={sectionHeadStyle}>
                <div>
                  <h2 style={sectionTitleStyle}>Runtime Status</h2>
                  <div style={subtleStyle}>{activeRows.length} registered instances · target {data.target_date}</div>
                </div>
              </div>
              <RuntimeStatusGroups groups={groupedRows} />
            </section>

            <section style={panelStyle}>
              <div style={sectionHeadStyle}>
                <div>
                  <h2 style={sectionTitleStyle}>Shadow Queue</h2>
                  <div style={subtleStyle}>{queueRows.length} proposed or blocked shadow tracks</div>
                </div>
              </div>
              <ShadowQueueTable rows={queueRows} />
            </section>
          </div>
        )}
      </>
    </PageFrame>
  );
}

type RuntimeGroup = {
  key: string;
  title: string;
  desc: string;
  rows: StrategyRuntimeRow[];
  color: string;
};

function groupRuntimeRows(rows: StrategyRuntimeRow[]): RuntimeGroup[] {
  const isShadow = (row: StrategyRuntimeRow) => row.lifecycle_status === "shadow" || row.execution_mode === "zero_notional_shadow";
  const isMonitor = (row: StrategyRuntimeRow) => ["monitor", "telemetry"].includes(row.lifecycle_status) || ["monitor", "telemetry"].includes(row.execution_mode);
  const isBlocked = (row: StrategyRuntimeRow) => ["blocked", "stale", "shelved"].includes(row.lifecycle_status) || ["blocked", "stale", "shelved"].includes(row.health_status);
  const isLive = (row: StrategyRuntimeRow) => row.lifecycle_status === "live" || row.execution_mode === "live";
  const isRunning = (row: StrategyRuntimeRow) => row.process_status === "running";
  const isStoppedish = (row: StrategyRuntimeRow) => ["stopped", "unknown", "crashed", "stale"].includes(row.process_status);

  const groups: RuntimeGroup[] = [
    {
      key: "running_live",
      title: "Running Live",
      desc: "实盘意图且当前观测到进程在跑",
      color: "var(--ok)",
      rows: rows.filter((row) => isLive(row) && isRunning(row)),
    },
    {
      key: "running_shadow",
      title: "Running Shadow",
      desc: "零 notional / shadow 且当前观测到进程在跑",
      color: "var(--accent)",
      rows: rows.filter((row) => isShadow(row) && isRunning(row)),
    },
    {
      key: "stopped_live",
      title: "Stopped / Unknown Live",
      desc: "标记为 live，但 supervisor 未观测到本机进程；需要看是否远端镜像、已停、或缺 tmux_session",
      color: "var(--bad)",
      rows: rows.filter((row) => isLive(row) && isStoppedish(row)),
    },
    {
      key: "stopped_shadow",
      title: "Stopped / Unknown Shadow",
      desc: "标记为 shadow，但当前未观测到本机进程",
      color: "var(--muted)",
      rows: rows.filter((row) => isShadow(row) && isStoppedish(row)),
    },
    {
      key: "monitor",
      title: "Monitor / Telemetry",
      desc: "监控或遥测实例，不应按交易策略解读",
      color: "var(--accent-2)",
      rows: rows.filter((row) => isMonitor(row) && !isLive(row) && !isShadow(row)),
    },
    {
      key: "blocked",
      title: "Blocked / Stale / Shelved",
      desc: "已明确 blocked/stale/shelved 的实例",
      color: "var(--bad)",
      rows: rows.filter((row) => isBlocked(row) && !isLive(row) && !isShadow(row) && !isMonitor(row)),
    },
  ];

  const assigned = new Set(groups.flatMap((group) => group.rows.map((row) => row.strategy_instance)));
  const otherRows = rows.filter((row) => !assigned.has(row.strategy_instance));
  if (otherRows.length > 0) {
    groups.push({
      key: "other",
      title: "Other",
      desc: "未落入标准运行状态分组的实例",
      color: "var(--muted)",
      rows: otherRows,
    });
  }
  return groups;
}

function RuntimeDetailPanel({ detail }: { detail: StrategyRuntimeDetail }) {
  const strategy = detail.strategy;
  const summary = strategy.summary ?? {};
  const recent = detail.recent_records ?? {};
  const liveOrders = recent.live_orders ?? [];
  const paperOrders = recent.paper_orders ?? [];
  const blockedCandidates = recent.blocked_candidates ?? [];
  const latestCandidates = recent.latest_candidates ?? [];
  const heartbeats = recent.summary_history ?? recent.primary_journal ?? [];
  const shadowCandidates = recent.shadow_candidates ?? [];
  const latestHeartbeat = heartbeats.length ? heartbeats[heartbeats.length - 1] : summary;
  const executor = valueOf(latestHeartbeat, "executor_result") as Record<string, unknown> | undefined;
  const executorParsed = valueOf(executor, "parsed") as Record<string, unknown> | undefined;

  return (
    <div style={stack12Style}>
      <div style={metricGridStyle}>
        <Metric label="Live enabled" value={fmtUnknown(valueOf(latestHeartbeat, "live_enabled") ?? strategy.live_enabled)} color={valueOf(latestHeartbeat, "live_enabled") || strategy.live_enabled ? "var(--ok)" : "var(--muted)"} />
        <Metric label="Routed" value={fmtUnknown(valueOf(latestHeartbeat, "routed_candidates") ?? strategy.candidate_rows)} />
        <Metric label="Eligible" value={fmtUnknown(valueOf(latestHeartbeat, "execution_eligible") ?? strategy.plan_rows)} color={Number(valueOf(latestHeartbeat, "execution_eligible") ?? strategy.plan_rows) > 0 ? "var(--ok)" : undefined} />
        <Metric label="Live orders" value={fmtUnknown(valueOf(executorParsed, "live_orders") ?? strategy.live_order_rows)} color={Number(valueOf(executorParsed, "live_orders") ?? strategy.live_order_rows) > 0 ? "var(--ok)" : undefined} />
        <Metric label="Base N" value={fmtUsd(valueOf(latestHeartbeat, "base_notional") ?? strategy.cap_order_notional)} />
        <Metric label="Day cap" value={fmtUsd(valueOf(latestHeartbeat, "daily_gross_cap") ?? strategy.cap_total_day_notional)} />
      </div>

      <div style={detailGridStyle}>
        <InfoBox title="Latest heartbeat" rows={[
          ["generated", fmtTime(String(valueOf(latestHeartbeat, "generated_at_utc") ?? strategy.latest_summary_ts_utc ?? ""))],
          ["snapshot", fmtTime(String(valueOf(valueOf(latestHeartbeat, "meta") as Record<string, unknown> | undefined, "snapshot_ts_utc") ?? ""))],
          ["obs cache", fmtUnknown(valueOf(valueOf(latestHeartbeat, "meta") as Record<string, unknown> | undefined, "observation_cache_status"))],
          ["by regime", compactJson(valueOf(latestHeartbeat, "candidate_by_regime"))],
          ["skips", compactJson(valueOf(latestHeartbeat, "skip_reasons"))],
          ["no order", fmtUnknown(valueOf(latestHeartbeat, "no_order_placed"))],
        ]} />
        <InfoBox title="Execution result" rows={[
          ["plans read", fmtUnknown(valueOf(executorParsed, "plans_read"))],
          ["paper written", fmtUnknown(valueOf(executorParsed, "paper_written"))],
          ["live written", fmtUnknown(valueOf(executorParsed, "live_written"))],
          ["live errors", fmtUnknown(valueOf(executorParsed, "live_errors"))],
          ["cancel checked", fmtUnknown(valueOf(executorParsed, "cancel_expired_checked"))],
        ]} />
      </div>

      <ShadowSizingPanel counts={valueOf(latestHeartbeat, "shadow_policy_counts") as Record<string, unknown> | undefined} />
      <LatestCandidateCards rows={latestCandidates} />
      <BlockedCandidateCards rows={blockedCandidates} />
      <RecentRecordTable
        title="Recent live orders"
        rows={liveOrders}
        empty="No live order records yet."
        columns={[
          ["time", (row) => fmtTime(String(row.placed_at_utc ?? row.created_at_utc ?? row.live_attempt_ts_utc ?? ""))],
          ["city", (row) => fmtUnknown(row.city)],
          ["side", (row) => fmtUnknown(row.order_side ?? row.side)],
          ["bracket", (row) => fmtUnknown(row.bracket)],
          ["price", (row) => fmtPrice(row.entry_price ?? row.limit_price ?? row.no_ask)],
          ["shares", (row) => fmtShares(row.shares ?? row.desired_shares ?? row.size)],
          ["temp L", (row) => fmtShares(row.soft_temp_context_light_shadow_shares)],
          ["temp regime", (row) => fmtUnknown(row.temperature_context_regime)],
          ["status", (row) => fmtUnknown(row.status ?? row.order_status)],
        ]}
      />
      <RecentRecordTable
        title="Recent shadow candidates"
        rows={shadowCandidates}
        empty="No shadow candidate records in this artifact."
        columns={[
          ["time", (row) => fmtTime(String(row.created_at_utc ?? row.decision_snapshot_ts_utc ?? ""))],
          ["city", (row) => fmtUnknown(row.city)],
          ["regime", (row) => fmtUnknown(row.day_regime)],
          ["expression", (row) => fmtUnknown(row.expression)],
          ["ask", (row) => fmtPrice(row.no_ask ?? row.ask)],
          ["soft $", (row) => fmtUsd(row.hypothetical_weighted_notional_usd ?? row.weighted_notional_usd)],
          ["shares", (row) => fmtUnknown(row.hypothetical_weighted_shares ?? row.weighted_shares)],
        ]}
      />
      <RecentRecordTable
        title="Recent paper orders"
        rows={paperOrders}
        empty="No paper order records yet."
        columns={[
          ["time", (row) => fmtTime(String(row.placed_at_utc ?? row.created_at_utc ?? ""))],
          ["city", (row) => fmtUnknown(row.city)],
          ["side", (row) => fmtUnknown(row.order_side ?? row.side)],
          ["bracket", (row) => fmtUnknown(row.bracket)],
          ["price", (row) => fmtPrice(row.entry_price ?? row.limit_price ?? row.no_ask)],
          ["shares", (row) => fmtShares(row.shares ?? row.desired_shares ?? row.size)],
          ["temp L", (row) => fmtShares(row.soft_temp_context_light_shadow_shares)],
          ["temp regime", (row) => fmtUnknown(row.temperature_context_regime)],
          ["status", (row) => fmtUnknown(row.status ?? row.order_status)],
        ]}
      />
    </div>
  );
}

function ShadowSizingPanel({ counts }: { counts: Record<string, unknown> | undefined }) {
  const rows = counts && typeof counts === "object" ? Object.entries(counts) : [];
  return (
    <div>
      <div style={{ fontWeight: 700, marginBottom: 6 }}>Shadow sizing</div>
      {rows.length === 0 ? (
        <div style={mutedInlineStyle}>No shadow sizing records in the latest heartbeat.</div>
      ) : (
        <div style={tableWrapStyle}>
          <table style={{ ...tableStyle, minWidth: 620 }}>
            <thead>
              <tr>
                <th style={thStyle}>Policy</th>
                <th style={numThStyle}>Avg weight</th>
                <th style={numThStyle}>Shadow $</th>
                <th style={numThStyle}>Exec rows</th>
                <th style={numThStyle}>Exec $</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(([name, raw]) => {
                const row = raw as Record<string, unknown>;
                return (
                  <tr key={name}>
                    <td style={tdStyle}>{policyLabel(name)}</td>
                    <td style={numTdStyle}>{fmtWeight(row.avg_weight)}</td>
                    <td style={numTdStyle}>{fmtUsd(row.shadow_notional_usd)}</td>
                    <td style={numTdStyle}>{fmtUnknown(row.shadow_executable_rows)}</td>
                    <td style={numTdStyle}>{fmtUsd(row.shadow_executable_notional_usd)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function LatestCandidateCards({ rows }: { rows: Array<Record<string, unknown>> }) {
  const ordered = rows.slice().reverse();
  return (
    <CandidateCards
      title="Latest candidates"
      empty="No latest candidate records in this artifact."
      rows={ordered}
      showStatus
    />
  );
}

function BlockedCandidateCards({ rows }: { rows: Array<Record<string, unknown>> }) {
  const ordered = rows.slice().reverse();
  return <CandidateCards title="Blocked candidates" empty="No blocked candidate records yet." rows={ordered} />;
}

function CandidateCards({
  title,
  rows,
  empty,
  showStatus = false,
}: {
  title: string;
  rows: Array<Record<string, unknown>>;
  empty: string;
  showStatus?: boolean;
}) {
  return (
    <div>
      <div style={{ fontWeight: 700, marginBottom: 6 }}>{title}</div>
      {rows.length === 0 ? (
        <div style={mutedInlineStyle}>{empty}</div>
      ) : (
        <div className="runtime-candidate-grid" style={candidateGridStyle}>
          {rows.map((row, idx) => (
            <div key={String(row.candidate_id ?? idx)} style={candidateCardStyle}>
              <div style={candidateCardHeadStyle}>
                <div>
                  <div style={{ fontWeight: 800 }}>{fmtUnknown(row.city)} · {fmtUnknown(row.bracket)}</div>
                  <div style={subtleStyle}>{fmtUnknown(row.target_date)} · {fmtUnknown(row.route_leg)}</div>
                </div>
                <Badge text={showStatus ? fmtUnknown(row.candidate_status) : fmtUnknown(row.day_regime)} color={showStatus && row.candidate_status === "accepted" ? "var(--ok)" : "var(--accent)"} />
              </div>
              <div style={candidateMetricGridStyle}>
                <MiniStat label="ask" value={fmtPrice(row.ask)} />
                <MiniStat label="shares" value={fmtShares(row.soft_shares)} />
                <MiniStat label="soft $" value={fmtUsd(row.soft_notional_usd)} />
                <MiniStat label="temp L" value={fmtShares(row.soft_temp_context_light_shadow_shares)} />
                <MiniStat label="temp M" value={fmtShares(row.soft_temp_context_medium_shadow_shares)} />
                <MiniStat label="wind ctx" value={fmtShares(row.soft_wind_context_shadow_shares)} />
                <MiniStat label="parity" value={fmtUnknown(row.live_feature_parity_ok)} />
              </div>
              <div style={blockedReasonStyle}>{fmtUnknown(row.execution_skip_reason)}</div>
              <div style={contextLineStyle}>{fmtUnknown(row.temperature_context_regime)}</div>
              <div style={candidateFooterStyle}>
                <span>{fmtTime(String(row.created_at_utc ?? ""))}</span>
                <span>{fmtUnknown(row.live_feature_source)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ color: "var(--muted)", fontSize: 10 }}>{label}</div>
      <div style={{ fontFamily: "monospace", fontSize: 12, fontWeight: 700 }}>{value}</div>
    </div>
  );
}

function InfoBox({ title, rows }: { title: string; rows: [string, string][] }) {
  return (
    <div style={metricStyle}>
      <div style={{ fontWeight: 700, marginBottom: 8 }}>{title}</div>
      <div style={{ display: "grid", gap: 6 }}>
        {rows.map(([label, value]) => (
          <div key={label} style={{ display: "grid", gridTemplateColumns: "110px 1fr", gap: 8 }}>
            <span style={{ color: "var(--muted)", fontSize: 12 }}>{label}</span>
            <span style={{ fontFamily: "monospace", fontSize: 12, wordBreak: "break-word" }}>{value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function RecentRecordTable({
  title,
  rows,
  columns,
  empty,
}: {
  title: string;
  rows: Array<Record<string, unknown>>;
  columns: [string, (row: Record<string, unknown>) => string][];
  empty: string;
}) {
  return (
    <div>
      <div style={{ fontWeight: 700, marginBottom: 6 }}>{title}</div>
      {rows.length === 0 ? (
        <div style={mutedInlineStyle}>{empty}</div>
      ) : (
        <div style={tableWrapStyle}>
          <table style={tableStyle}>
            <thead>
              <tr>
                {columns.map(([label]) => <th key={label} style={thStyle}>{label}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.slice().reverse().map((row, idx) => (
                <tr key={String(row.shadow_decision_id ?? row.signal_id ?? row.order_id ?? idx)}>
                  {columns.map(([label, render]) => <td key={label} style={tdStyle}>{render(row)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SummaryGrid({ data }: { data: StrategyRuntimeOverview }) {
  const s = data.summary;
  const running = data.strategies.filter((row) => row.process_status === "running").length;
  const stopped = data.strategies.filter((row) => row.process_status === "stopped").length;
  const unknown = data.strategies.filter((row) => row.process_status === "unknown").length;
  return (
    <div style={metricGridStyle}>
      <Metric label="Registered" value={String(s.total_strategies)} />
      <Metric label="Live" value={String(s.live_strategies)} color="var(--ok)" />
      <Metric label="Shadow" value={String(s.shadow_strategies)} color="var(--accent)" />
      <Metric label="Running" value={String(running)} color={running > 0 ? "var(--ok)" : undefined} />
      <Metric label="Stopped / unknown" value={`${stopped} / ${unknown}`} color={stopped + unknown > 0 ? "var(--bad)" : undefined} />
      <Metric label="Telemetry" value={String(s.telemetry_strategies)} />
      <Metric label="Healthy" value={String(s.healthy_strategies)} color="var(--ok)" />
      <Metric label="Blocked / stale" value={`${s.blocked_strategies} / ${s.stale_strategies}`} color={s.blocked_strategies + s.stale_strategies > 0 ? "var(--bad)" : undefined} />
      <Metric label="Target seen" value={String(s.target_seen_strategies)} color={s.target_seen_strategies > 0 ? "var(--ok)" : undefined} />
      <Metric label="Queue high" value={`${s.shadow_queue_high} / ${s.shadow_queue_items}`} />
    </div>
  );
}

function Metric({ label, value, color = "inherit" }: { label: string; value: string; color?: string }) {
  return (
    <div style={metricStyle}>
      <div style={{ color: "var(--muted)", fontSize: 11 }}>{label}</div>
      <div style={{ marginTop: 4, fontSize: 22, fontWeight: 700, color, fontVariantNumeric: "tabular-nums" }}>{value}</div>
    </div>
  );
}

function RuntimeStatusGroups({ groups }: { groups: RuntimeGroup[] }) {
  return (
    <div style={groupStackStyle}>
      {groups.map((group) => (
        <section key={group.key} style={runtimeGroupStyle}>
          <div style={runtimeGroupHeadStyle}>
            <div>
              <h3 style={runtimeGroupTitleStyle}>{group.title}</h3>
              <div style={subtleStyle}>{group.desc}</div>
            </div>
            <Badge text={String(group.rows.length)} color={group.color} />
          </div>
          {group.rows.length === 0 ? (
            <div style={mutedInlineStyle}>No instances.</div>
          ) : (
            <div style={strategyListStyle}>
              {group.rows.map((row) => (
                <StrategyRowView key={row.strategy_instance} row={row} />
              ))}
            </div>
          )}
        </section>
      ))}
    </div>
  );
}

function StrategyRowView({ row }: { row: StrategyRuntimeRow }) {
  const targetArtifacts = row.target_artifacts.map((a) => a.artifact_kind).join(", ");
  const counts = [
    `live ${fmtInt(row.live_order_rows)}`,
    `paper ${fmtInt(row.paper_order_rows)}`,
    `shadow ${fmtInt(row.shadow_rows)}`,
    `telemetry ${fmtInt(row.telemetry_rows)}`,
  ].join(" · ");
  const cap = [
    row.cap_order_notional != null ? `order ${fmtMoney(row.cap_order_notional)}` : null,
    row.cap_city_day_notional != null ? `city ${fmtMoney(row.cap_city_day_notional)}` : null,
    row.live_enabled != null ? `live ${row.live_enabled ? "on" : "off"}` : null,
  ].filter(Boolean).join(" · ");
  const blockers = listText(row.blockers);
  const orderHref = row.config_id
    ? `/weather/orders?trade_class=all&instance_id=${encodeURIComponent(row.strategy_instance)}`
    : null;

  return (
    <article style={strategyCardStyle}>
      <div style={strategyCardHeadStyle}>
        <div style={{ minWidth: 0 }}>
          <div style={strategyTitleRowStyle}>
            <h3 style={strategyCardTitleStyle}>{row.display_name}</h3>
            <Badge text={row.lifecycle_status} color={statusColor(row.lifecycle_status)} />
            <Badge text={row.process_status} color={processColor(row.process_status)} />
            <Badge text={row.health_status} color={statusColor(row.health_status)} />
          </div>
          <div style={strategyDescriptionStyle}>{strategyDescriptionZh(row)}</div>
          <div style={subtleStyle}>{row.strategy_name} · {row.strategy_key}</div>
          <div style={monoSmallStyle}>{row.strategy_instance}</div>
          <div style={strategyActionRowStyle}>
            <span style={monoSmallInlineStyle}>config {shortId(row.config_id)}</span>
            <Link to={`/weather/instances/${encodeURIComponent(row.strategy_instance)}`} style={linkButtonStyle}>Instance</Link>
            {orderHref ? (
              <Link to={orderHref} style={linkButtonStyle}>Orders</Link>
            ) : (
              <span style={disabledLinkStyle}>Orders unavailable</span>
            )}
          </div>
        </div>
      </div>

      <div style={strategyMetaGridStyle}>
        <MiniStat label="target" value={targetLabel(row.target_status)} />
        <MiniStat label="age" value={fmtAge(row.heartbeat_age_min)} />
        <MiniStat label="mode" value={row.execution_mode} />
        <MiniStat label="process" value={row.process_status} />
        <MiniStat label="config" value={shortId(row.config_id)} />
        <MiniStat label="rows" value={counts} />
        <MiniStat label="fact / real" value={`${fmtInt(row.fact_trade_rows)} / ${fmtInt(row.fact_live_real_rows)}`} />
        <MiniStat label="cap / live" value={cap || "-"} />
      </div>

      <div style={strategyFooterGridStyle}>
        <div>
          <div style={metaLabelStyle}>Freshness</div>
          <div>{fmtTime(row.latest_data_ts_utc || row.latest_artifact_mtime_utc)}</div>
          <div style={monoSmallStyle}>{shortPath(row.primary_journal_path)}</div>
        </div>
        <div>
          <div style={metaLabelStyle}>Target artifact</div>
          <div>{targetArtifacts || `latest ${row.latest_sample_target_date ?? "-"}`}</div>
        </div>
        <div>
          <div style={metaLabelStyle}>Notes / blockers</div>
          <div>{blockers !== "-" ? blockers : row.notes ?? "-"}</div>
        </div>
      </div>
    </article>
  );
}

function processColor(status: string): string {
  if (status === "running") return "var(--ok)";
  if (["stopped", "crashed", "stale"].includes(status)) return "var(--bad)";
  return "var(--muted)";
}

function ShadowQueueTable({ rows }: { rows: StrategyShadowQueueRow[] }) {
  if (rows.length === 0) {
    return <div style={mutedBlockStyle}>No queued shadow strategies.</div>;
  }
  return (
    <div style={tableWrapStyle}>
      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={thStyle}>Shadow</th>
            <th style={thStyle}>Priority</th>
            <th style={thStyle}>Status</th>
            <th style={thStyle}>Family</th>
            <th style={thStyle}>Target runtime</th>
            <th style={thStyle}>Required fields</th>
            <th style={thStyle}>Notes</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.shadow_id}>
              <td style={tdStyle}>
                <div style={{ fontWeight: 700 }}>{row.display_name}</div>
                <div style={monoSmallStyle}>{row.shadow_id}</div>
              </td>
              <td style={tdStyle}><Badge text={row.priority} color={row.priority === "high" ? "var(--bad)" : "var(--accent)"} /></td>
              <td style={tdStyle}><Badge text={row.status} color={statusColor(row.status)} /></td>
              <td style={tdStyle}>{row.family}</td>
              <td style={monoTdStyle}>{shortPath(row.target_runtime_dir)}</td>
              <td style={tdStyle}>{listText(row.required_fields, 4)}</td>
              <td style={tdStyle}>{listText(row.blockers) !== "-" ? listText(row.blockers) : row.notes ?? "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Badge({ text, color }: { text: string; color: string }) {
  return (
    <span style={{
      display: "inline-block",
      padding: "2px 7px",
      borderRadius: 6,
      fontSize: 10,
      fontWeight: 700,
      color,
      background: `${color}20`,
      border: `1px solid ${color}66`,
      whiteSpace: "nowrap",
    }}>
      {text}
    </span>
  );
}

const toolbarStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  flexWrap: "wrap",
  gap: 14,
  marginBottom: 16,
  border: "1px solid var(--stroke)",
  background: "var(--card)",
  borderRadius: 8,
  padding: 14,
};
const stack16Style: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr)",
  gap: 16,
  minWidth: 0,
};
const stack12Style: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr)",
  gap: 12,
  marginTop: 12,
  minWidth: 0,
};
const dateInputStyle: React.CSSProperties = {
  marginTop: 6,
  height: 30,
  borderRadius: 6,
  border: "1px solid var(--stroke)",
  background: "transparent",
  color: "var(--fg)",
  padding: "0 8px",
};
const panelStyle: React.CSSProperties = {
  border: "1px solid var(--stroke)",
  background: "var(--card)",
  borderRadius: 8,
  padding: 16,
  minWidth: 0,
};
const sectionHeadStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
  flexWrap: "wrap",
  gap: 16,
};
const sectionTitleStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 18,
};
const subtleStyle: React.CSSProperties = {
  marginTop: 4,
  color: "var(--muted)",
  fontSize: 12,
};
const monoSmallStyle: React.CSSProperties = {
  marginTop: 5,
  color: "var(--muted)",
  fontSize: 10,
  fontFamily: "monospace",
  wordBreak: "break-word",
};
const monoSmallInlineStyle: React.CSSProperties = {
  color: "var(--muted)",
  fontSize: 10,
  fontFamily: "monospace",
  wordBreak: "break-word",
};
const strategyActionRowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  flexWrap: "wrap",
  gap: 8,
  marginTop: 8,
};
const linkButtonStyle: React.CSSProperties = {
  color: "var(--accent-2)",
  textDecoration: "none",
  fontSize: 12,
  fontWeight: 700,
};
const disabledLinkStyle: React.CSSProperties = {
  color: "var(--muted)",
  fontSize: 12,
};
const metricGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(min(138px, 100%), 1fr))",
  gap: 10,
  minWidth: 0,
};
const metricStyle: React.CSSProperties = {
  border: "1px solid var(--stroke)",
  borderRadius: 8,
  background: "var(--card)",
  padding: 12,
  minWidth: 0,
};
const strategyListStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr)",
  gap: 10,
  marginTop: 12,
  minWidth: 0,
};
const groupStackStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr)",
  gap: 14,
  marginTop: 14,
  minWidth: 0,
};
const runtimeGroupStyle: React.CSSProperties = {
  borderTop: "1px solid var(--stroke)",
  paddingTop: 12,
  minWidth: 0,
};
const runtimeGroupHeadStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
  gap: 12,
  minWidth: 0,
};
const runtimeGroupTitleStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 15,
};
const strategyCardStyle: React.CSSProperties = {
  border: "1px solid var(--stroke)",
  borderRadius: 8,
  background: "rgba(255,255,255,0.48)",
  padding: 14,
  minWidth: 0,
};
const strategyCardHeadStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr)",
  gap: 10,
};
const strategyTitleRowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  flexWrap: "wrap",
  gap: 8,
  minWidth: 0,
};
const strategyCardTitleStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 15,
  lineHeight: 1.25,
};
const strategyDescriptionStyle: React.CSSProperties = {
  marginTop: 8,
  color: "var(--fg)",
  fontSize: 13,
  lineHeight: 1.5,
  overflowWrap: "anywhere",
};
const strategyMetaGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(min(150px, 100%), 1fr))",
  gap: 10,
  marginTop: 12,
  minWidth: 0,
};
const strategyFooterGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(min(220px, 100%), 1fr))",
  gap: 12,
  marginTop: 12,
  paddingTop: 12,
  borderTop: "1px solid var(--stroke)",
  fontSize: 12,
  lineHeight: 1.45,
  minWidth: 0,
};
const metaLabelStyle: React.CSSProperties = {
  color: "var(--muted)",
  fontSize: 11,
  marginBottom: 4,
};
const detailGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(min(280px, 100%), 1fr))",
  gap: 10,
  minWidth: 0,
};
const tableWrapStyle: React.CSSProperties = {
  overflowX: "auto",
  marginTop: 12,
  width: "100%",
  maxWidth: "100%",
  minWidth: 0,
};
const tableStyle: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: 12,
  minWidth: 720,
};
const thStyle: React.CSSProperties = {
  textAlign: "left",
  color: "var(--muted)",
  borderBottom: "1px solid var(--stroke)",
  padding: "8px 6px",
  whiteSpace: "nowrap",
};
const numThStyle: React.CSSProperties = {
  ...thStyle,
  textAlign: "right",
};
const tdStyle: React.CSSProperties = {
  borderBottom: "1px solid var(--stroke)",
  padding: "9px 6px",
  verticalAlign: "top",
  lineHeight: 1.35,
};
const monoTdStyle: React.CSSProperties = {
  ...tdStyle,
  fontFamily: "monospace",
  whiteSpace: "nowrap",
};
const numTdStyle: React.CSSProperties = {
  ...tdStyle,
  textAlign: "right",
  fontVariantNumeric: "tabular-nums",
  whiteSpace: "nowrap",
};
const mutedBlockStyle: React.CSSProperties = {
  color: "var(--muted)",
  textAlign: "center",
  padding: 34,
};
const mutedInlineStyle: React.CSSProperties = {
  color: "var(--muted)",
  padding: "12px 0",
};
const errorStyle: React.CSSProperties = {
  color: "var(--bad)",
  border: "1px solid var(--bad)",
  borderRadius: 8,
  padding: 12,
  background: "var(--bad)18",
};
const candidateGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(min(240px, 100%), 1fr))",
  gap: 10,
  minWidth: 0,
};
const candidateCardStyle: React.CSSProperties = {
  border: "1px solid var(--stroke)",
  borderRadius: 8,
  background: "rgba(255,255,255,0.56)",
  padding: 12,
  minWidth: 0,
};
const candidateCardHeadStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
  gap: 10,
};
const candidateMetricGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
  gap: 8,
  marginTop: 10,
};
const blockedReasonStyle: React.CSSProperties = {
  marginTop: 10,
  color: "var(--bad)",
  fontFamily: "monospace",
  fontSize: 11,
  overflowWrap: "anywhere",
};
const contextLineStyle: React.CSSProperties = {
  marginTop: 8,
  color: "var(--muted)",
  fontFamily: "monospace",
  fontSize: 11,
  overflowWrap: "anywhere",
};
const candidateFooterStyle: React.CSSProperties = {
  marginTop: 10,
  display: "flex",
  justifyContent: "space-between",
  gap: 10,
  color: "var(--muted)",
  fontSize: 11,
  overflowWrap: "anywhere",
};
