/**
 * WeatherStrategiesPage — one card per strategy config.
 * Strategy = config_id. All dimensions live in strategy_config.params JSON.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import type { StrategyRow } from "../../data/weather-types";

type StrategyState = "live" | "paper" | "explore" | "all";

// ── helpers ────────────────────────────────────────────────────────────────────

function pct(v: number | null, decimals = 1) {
  return v == null ? "—" : `${(v * 100).toFixed(decimals)}%`;
}
function usd(v: number | null | undefined, decimals = 2) {
  if (v == null) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}$${Math.abs(v).toFixed(decimals)}`;
}
function shortName(name: string): string {
  // Strip algorithm prefix, humanize the rest
  const stripped = name
    .replace(/^.*?_mid_price_core_v\d+_/, "")
    .replace(/^.*?_maker_queue_v\d+_/, "");
  return (stripped || name)
    .replace(/_/g, " ")
    .replace(/\$None/g, "—")   // "$None" → "—"
    .replace(/shares —/g, "")  // "shares —" → ""
    .trim();
}

function strategyPurposeZh(s: StrategyRow): string {
  const p = s.params;
  const policy = String(s.execution_policy ?? p.execution_policy ?? "");
  const pool = String(p.city_pool ?? "");
  const entry = String(p.entry_price_window ?? "");

  if (policy.includes("low_price_yes_lottery")) {
    return "低价 YES 小仓探针：在 5c-20c 的温度 exact bracket YES 里找低价高赔率候选，用于前向验证，不是已确认主力策略。";
  }
  if (policy.includes("low_price_yes_take_profit_exit")) {
    return "低价 YES 止盈退出：对已有低价 YES 持仓做 position-aware revalue，满足条件时卖出锁利润或降低尾部风险。";
  }
  if (policy.includes("tmax_distribution_edge")) {
    return "Tmax 分布表达选择器：先估计最终最高温落在 current/d1/d2/tail 的概率，再选择最有价值的 exact bracket 表达。";
  }
  if (policy.includes("value_d1_no")) {
    return "D1 NO 价值探针：评估下一档 NO 是否被低估，重点看 overshoot 风险、真实 NO ask 和可成交容量。";
  }
  if (policy.includes("regime_routed_no")) {
    return "Regime-routed NO：按温度路径状态选择 NO 侧表达，并用价格/size 纪律控制入场质量。";
  }
  if (policy.includes("theta_current_yes")) {
    return "Current YES taker：围绕当前最高温 exact bracket YES 的胜率和 overshoot 风险做进场判断。";
  }
  if (policy.includes("maker_queue")) {
    return "Maker queue 版本：尝试用挂单队列改善成交价格和滑点，重点评估订单质量而不只看信号本身。";
  }
  if (policy.includes("mid_price_core")) {
    const poolLabel = pool === "t2_research" ? "T2 研究池" : "T1 交易池";
    const entryLabel = entry && entry !== "full_range" ? `，入场价窗口 ${entry}` : "";
    return `${poolLabel} 的旧 mid-price core 基线：按模型概率和盘口中价计算 edge 后进场${entryLabel}；主要用于历史对照，不代表当前活跃主线。`;
  }
  if (s.config_id === "legacy_research_weather_edge") {
    return "旧研究 CSV 迁移基线：用于保留历史研究/回放数据，不是当前执行策略。";
  }
  return "历史策略配置：说明主要藏在 params/execution_policy 里；这页用于配置级绩效对照，不等同于当前 live runner。";
}

// ── component ──────────────────────────────────────────────────────────────────

export function WeatherStrategiesPage() {
  const [strategies, setStrategies] = useState<StrategyRow[]>([]);
  const [stateFilter, setStateFilter] = useState<StrategyState>("live");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    weatherApi
      .listStrategies({ state: stateFilter })
      .then((data) => setStrategies([...data].sort((a, b) => {
        const score = (s: StrategyRow) =>
          (s.params.live_enabled ? 2 : s.params.paper_enabled ? 1 : 0);
        const sd = score(b) - score(a);
        if (sd !== 0) return sd;
        return (b.latest_run_at ?? "").localeCompare(a.latest_run_at ?? "");
      })))
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [stateFilter]);

  return (
    <PageFrame
      title="Strategies"
      desc="Per-config strategy registry · 策略配置注册表"
    >
      <>
        <div style={topBarStyle}>
          <div style={segmentedStyle}>
            {(["live", "paper", "explore", "all"] as StrategyState[]).map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => setStateFilter(v)}
                style={v === stateFilter ? segmentedButtonActiveStyle : segmentedButtonStyle}
              >
                {v.toUpperCase()}
              </button>
            ))}
          </div>
          {!loading && (
            <div style={{ color: "var(--muted)", fontSize: 13 }}>
              {stateFilter} · {strategies.length} strategies · {strategies.filter(s => s.settled_trades > 0).length} with settled trades
            </div>
          )}
        </div>

        {error && <div style={errorStyle}>{error}</div>}

        {loading && !error && (
          <div style={{ color: "var(--muted)", textAlign: "center", padding: 40 }}>
            Loading strategies…
          </div>
        )}

        <div style={gridStyle}>
          {strategies.map((s) => (
            <StrategyCard key={s.config_id} s={s} stateFilter={stateFilter} />
          ))}
        </div>
      </>
    </PageFrame>
  );
}

// ── StrategyCard ───────────────────────────────────────────────────────────────

function StrategyCard({ s, stateFilter }: { s: StrategyRow; stateFilter: StrategyState }) {
  const p = s.params;
  const liveEnabled = p.live_enabled as boolean | undefined;
  const paperEnabled = p.paper_enabled as boolean | undefined;
  const sizingMode = (p.sizing_mode as string | undefined) ?? "—";
  const notional = p.max_order_notional as number | null | undefined;
  const shares = p.fixed_order_shares as number | null | undefined;
  const entryWindow = (p.entry_price_window as string | undefined) ?? "—";
  const minEdge = p.min_edge as number | null | undefined;
  const algVersion = (p.algorithm_version as string | undefined) ?? "—";

  const pnl = s.total_pnl_usd;
  const pnlColor =
    s.total_trades === 0 ? "var(--muted)" : pnl >= 0 ? "var(--ok)" : "var(--bad)";
  const hasActivity = s.total_trades > 0;

  const accentColor = liveEnabled ? "var(--ok)" : paperEnabled ? "var(--accent-2)" : "var(--muted)";

  const hasSettled = s.settled_trades > 0;

  return (
    <Link to={`/weather/strategies/${encodeURIComponent(s.config_id)}?state=${stateFilter}`} style={{ textDecoration: "none", color: "inherit" }}>
      <div style={{ ...cardStyle, borderLeft: `3px solid ${accentColor}`, cursor: "pointer", transition: "box-shadow 0.15s" }}
        onMouseEnter={e => (e.currentTarget.style.boxShadow = "0 4px 20px rgba(0,0,0,0.15)")}
        onMouseLeave={e => (e.currentTarget.style.boxShadow = "none")}
      >
        {/* Header */}
        <div style={cardHeaderStyle}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={cardTitleStyle} title={s.name}>
              {shortName(s.name)}
            </div>
            <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2, fontFamily: "monospace" }}>
              {s.config_id.slice(-12)}
            </div>
          </div>
          <div style={{ display: "flex", gap: 5, flexShrink: 0 }}>
            {liveEnabled && <Badge label="LIVE" color="var(--ok)" />}
            {paperEnabled && <Badge label="PAPER" color="var(--accent-2)" />}
            {!liveEnabled && !paperEnabled && <Badge label="INACTIVE" color="var(--muted)" />}
          </div>
        </div>

        <div style={purposeStyle}>{strategyPurposeZh(s)}</div>

        {/* Key params grid */}
        <div style={paramsGridStyle}>
          <ParamCell label="Sizing" value={sizingMode} />
          <ParamCell
            label="Notional / Shares"
            value={notional != null ? `$${notional}` : shares != null ? `${shares} sh` : "—"}
          />
          <ParamCell label="Entry Window" value={entryWindow} />
          <ParamCell label="Min Edge" value={minEdge != null ? pct(minEdge, 0) : "—"} />
          <ParamCell label="Algorithm" value={algVersion.replace("_v1", " v1")} />
          <ParamCell label="Exec Policy" value={s.execution_policy ?? "mid_price_core_v1"} />
        </div>

        {/* Divider */}
        <div style={dividerStyle} />

        {/* Stats row */}
        <div style={statsRowStyle}>
          <StatCell label="Runs" value={String(s.num_runs)} />
          <StatCell label="Trades" value={hasActivity ? String(s.total_trades) : "—"} />
          <StatCell label="Settled" value={hasActivity ? String(s.settled_trades) : "—"} />
          <StatCell label="Win%" value={hasSettled ? pct(s.win_rate) : "—"} />
          <StatCell
            label="PnL"
            value={hasSettled ? usd(pnl) : "—"}
            valueColor={hasSettled ? pnlColor : "var(--muted)"}
          />
          <StatCell
            label="ROI"
            value={hasSettled ? pct(s.roi, 2) : "—"}
            valueColor={hasSettled && s.roi != null ? (s.roi >= 0 ? "var(--ok)" : "var(--bad)") : "var(--muted)"}
          />
        </div>

        {/* Footer */}
        <div style={cardFooterStyle}>
          <span style={{ color: "var(--muted)", fontSize: 11 }}>
            Latest: {s.latest_run_at ? s.latest_run_at.slice(0, 10) : "never"}
            {" · "}
            {s.live_run_count} live · {s.paper_run_count} paper
          </span>
          <span style={{ ...linkStyle, fontSize: 12 }}>Details →</span>
        </div>
      </div>
    </Link>
  );
}

// ── sub-components ─────────────────────────────────────────────────────────────

function Badge({ label, color }: { label: string; color: string }) {
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, padding: "2px 7px",
      borderRadius: 4, background: color + "22",
      color, border: `1px solid ${color}55`,
      letterSpacing: "0.04em",
    }}>
      {label}
    </span>
  );
}

function ParamCell({ label, value }: { label: string; value: string }) {
  return (
    <div style={paramCellStyle}>
      <div style={paramLabelStyle}>{label}</div>
      <div style={paramValueStyle}>{value}</div>
    </div>
  );
}

function StatCell({
  label, value, valueColor = "inherit",
}: { label: string; value: string; valueColor?: string }) {
  return (
    <div style={{ textAlign: "center", flex: 1 }}>
      <div style={{ fontSize: 16, fontWeight: 700, color: valueColor, fontVariantNumeric: "tabular-nums" }}>
        {value}
      </div>
      <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>{label}</div>
    </div>
  );
}

// ── styles ─────────────────────────────────────────────────────────────────────

const topBarStyle: React.CSSProperties = {
  display: "flex", justifyContent: "space-between", alignItems: "center",
  marginBottom: 20,
};
const segmentedStyle: React.CSSProperties = {
  display: "inline-flex",
  border: "1px solid var(--stroke)",
  borderRadius: 6,
  overflow: "hidden",
  background: "var(--card)",
};
const segmentedButtonStyle: React.CSSProperties = {
  border: 0,
  borderRight: "1px solid var(--stroke)",
  background: "transparent",
  color: "var(--muted)",
  padding: "7px 10px",
  fontSize: 11,
  fontWeight: 700,
  cursor: "pointer",
};
const segmentedButtonActiveStyle: React.CSSProperties = {
  ...segmentedButtonStyle,
  background: "var(--accent)",
  color: "white",
};
const errorStyle: React.CSSProperties = {
  color: "var(--bad)", marginBottom: 12, padding: "10px 14px",
  background: "rgba(255,50,50,0.08)", borderRadius: 8,
};
const gridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr)",
  gap: 12,
};
const cardStyle: React.CSSProperties = {
  background: "var(--card)",
  border: "1px solid var(--stroke)",
  borderRadius: 8,
  padding: "18px 20px",
  display: "flex",
  flexDirection: "column",
  gap: 14,
  minWidth: 0,
};
const cardHeaderStyle: React.CSSProperties = {
  display: "flex", alignItems: "flex-start", gap: 10,
};
const cardTitleStyle: React.CSSProperties = {
  fontSize: 15, fontWeight: 700,
  overflowWrap: "anywhere",
};
const purposeStyle: React.CSSProperties = {
  color: "var(--fg)",
  fontSize: 13,
  lineHeight: 1.5,
  overflowWrap: "anywhere",
};
const paramsGridStyle: React.CSSProperties = {
  display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(180px, 100%), 1fr))",
  gap: "8px 12px",
};
const paramCellStyle: React.CSSProperties = {
  display: "flex", flexDirection: "column", gap: 2,
};
const paramLabelStyle: React.CSSProperties = {
  fontSize: 10, color: "var(--muted)", textTransform: "uppercase",
  letterSpacing: "0.06em",
};
const paramValueStyle: React.CSSProperties = {
  fontSize: 13, fontWeight: 500,
};
const dividerStyle: React.CSSProperties = {
  height: 1, background: "var(--stroke)", margin: "0 -20px",
};
const statsRowStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(min(92px, 100%), 1fr))",
  gap: 8,
};
const cardFooterStyle: React.CSSProperties = {
  display: "flex", justifyContent: "space-between", alignItems: "center",
  gap: 12,
  flexWrap: "wrap",
};
const linkStyle: React.CSSProperties = {
  color: "var(--accent-2)", textDecoration: "none", fontSize: 12,
};
