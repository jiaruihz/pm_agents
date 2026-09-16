import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import type { StrategyDefinitionRow } from "../../data/weather-types";

function badgeColor(row: StrategyDefinitionRow): string {
  if (row.running_instance_count > 0) return "var(--ok)";
  if (row.live_instance_count > 0) return "var(--accent-2)";
  return "var(--muted)";
}

const CURRENT_STATUSES = new Set(["forward_live", "active_research"]);
const RESEARCH_STATUSES = new Set(["research_candidate", "dormant_research", "blocked_research", "monitor"]);

function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    forward_live: "当前前向取证",
    active_research: "当前研究主线",
    research_candidate: "研究候选",
    dormant_research: "暂不主线",
    blocked_research: "研究阻塞",
    historical_live: "历史 live",
    historical: "历史基线",
    monitor: "监控",
  };
  return labels[status] ?? "待分类";
}

export function WeatherStrategyManagementPage(): JSX.Element {
  const [rows, setRows] = useState<StrategyDefinitionRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    weatherApi.listStrategyDefinitions().then(setRows).catch((e: Error) => setError(e.message));
  }, []);
  const current = rows.filter((row) => CURRENT_STATUSES.has(row.portfolio_status));
  const research = rows.filter((row) => RESEARCH_STATUSES.has(row.portfolio_status));
  const historical = rows.filter((row) => !CURRENT_STATUSES.has(row.portfolio_status) && !RESEARCH_STATUSES.has(row.portfolio_status));

  return (
    <PageFrame title="Strategies" desc="Trading ideas · 策略定义">
      <>
        {error && <div style={errorStyle}>{error}</div>}
        <div style={summaryStyle}>{current.length} 当前主线 · {research.length} 研究/监控 · {historical.length} 历史/暂不主线</div>
        <StrategySection title="当前主线" subtitle="正在推进的前向取证或研究线" rows={current} />
        <StrategySection title="研究 / 监控" subtitle="保留观察，不等同于实盘策略" rows={research} />
        <details style={historyStyle}>
          <summary style={historySummaryStyle}>历史 / 暂不主线 ({historical.length})</summary>
          <div style={{ ...stackStyle, marginTop: 10 }}>
            {historical.map((row) => <StrategyCard key={row.strategy_key} row={row} />)}
          </div>
        </details>
        {!error && rows.length === 0 && <div style={emptyStyle}>Loading strategy catalog...</div>}
      </>
    </PageFrame>
  );
}

function StrategySection({ title, subtitle, rows }: { title: string; subtitle: string; rows: StrategyDefinitionRow[] }) {
  if (rows.length === 0) return null;
  return <section style={sectionStyle}>
    <div style={sectionHeadStyle}><h2 style={sectionTitleStyle}>{title}</h2><span style={sectionSubStyle}>{subtitle}</span></div>
    <div style={stackStyle}>
      {rows.map((row) => <StrategyCard key={row.strategy_key} row={row} />)}
    </div>
  </section>;
}

function StrategyCard({ row }: { row: StrategyDefinitionRow }) {
  return (
    <Link to={`/weather/strategies/${encodeURIComponent(row.strategy_key)}`} style={linkResetStyle}>
      <article style={cardStyle}>
        <div style={{ ...accentStyle, background: badgeColor(row) }} />
        <div style={bodyStyle}>
          <div style={titleLineStyle}>
            <div>
              <h2 style={titleStyle}>{row.strategy_name || row.strategy_key}</h2>
              <div style={keyStyle}>{row.strategy_key}</div>
            </div>
            <div style={statusStyle}>{statusLabel(row.portfolio_status)}</div>
          </div>
          <p style={descStyle}>{row.description || "尚未补充策略说明。"}</p>
          <div style={portfolioStyle}>当前定位 · {row.portfolio_note || "尚未补充当前定位。"}</div>
          <div style={metricLineStyle}>
            <Metric label="Configs" value={row.config_count} />
            <Metric label="Instances" value={row.instance_count} />
            <Metric label="Live" value={row.live_instance_count} />
            <Metric label="Running" value={row.running_instance_count} />
          </div>
        </div>
      </article>
    </Link>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return <span><b style={{ color: "var(--fg)" }}>{value}</b> <span style={{ color: "var(--muted)" }}>{label}</span></span>;
}

const summaryStyle: React.CSSProperties = { color: "var(--muted)", fontSize: 13, marginBottom: 14 };
const stackStyle: React.CSSProperties = { display: "grid", gap: 10 };
const sectionStyle: React.CSSProperties = { marginBottom: 22 };
const sectionHeadStyle: React.CSSProperties = { display: "flex", gap: 10, alignItems: "baseline", marginBottom: 9 };
const sectionTitleStyle: React.CSSProperties = { fontSize: 15, margin: 0 };
const sectionSubStyle: React.CSSProperties = { color: "var(--muted)", fontSize: 12 };
const historyStyle: React.CSSProperties = { borderTop: "1px solid var(--stroke)", paddingTop: 16 };
const historySummaryStyle: React.CSSProperties = { cursor: "pointer", fontWeight: 650, fontSize: 15 };
const linkResetStyle: React.CSSProperties = { color: "inherit", textDecoration: "none" };
const cardStyle: React.CSSProperties = { display: "flex", background: "var(--card)", border: "1px solid var(--stroke)", borderRadius: 8, overflow: "hidden" };
const accentStyle: React.CSSProperties = { width: 4, flexShrink: 0 };
const bodyStyle: React.CSSProperties = { padding: "15px 18px", minWidth: 0, flex: 1 };
const titleLineStyle: React.CSSProperties = { display: "flex", justifyContent: "space-between", gap: 12, alignItems: "start" };
const titleStyle: React.CSSProperties = { margin: 0, fontSize: 17, lineHeight: 1.25 };
const keyStyle: React.CSSProperties = { marginTop: 3, color: "var(--muted)", fontFamily: "IBM Plex Mono, monospace", fontSize: 11, overflowWrap: "anywhere" };
const statusStyle: React.CSSProperties = { color: "var(--muted)", fontSize: 11, fontWeight: 700, whiteSpace: "nowrap" };
const descStyle: React.CSSProperties = { margin: "10px 0 12px", color: "var(--fg)", lineHeight: 1.55, fontSize: 13 };
const portfolioStyle: React.CSSProperties = { margin: "-3px 0 12px", color: "var(--muted)", lineHeight: 1.45, fontSize: 12 };
const metricLineStyle: React.CSSProperties = { display: "flex", gap: 18, flexWrap: "wrap", fontSize: 12 };
const errorStyle: React.CSSProperties = { color: "var(--bad)", marginBottom: 12 };
const emptyStyle: React.CSSProperties = { color: "var(--muted)", padding: 32, textAlign: "center" };
