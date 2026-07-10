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

export function WeatherStrategyManagementPage(): JSX.Element {
  const [rows, setRows] = useState<StrategyDefinitionRow[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    weatherApi.listStrategyDefinitions().then(setRows).catch((e: Error) => setError(e.message));
  }, []);

  return (
    <PageFrame title="Strategies" desc="Trading ideas · 策略定义">
      <>
        {error && <div style={errorStyle}>{error}</div>}
        <div style={summaryStyle}>{rows.length} strategies · 策略定义下包含 config 和部署 instance</div>
        <div style={stackStyle}>
          {rows.map((row) => (
            <Link key={row.strategy_key} to={`/weather/strategies/${encodeURIComponent(row.strategy_key)}`} style={linkResetStyle}>
              <article style={cardStyle}>
                <div style={{ ...accentStyle, background: badgeColor(row) }} />
                <div style={bodyStyle}>
                  <div style={titleLineStyle}>
                    <div>
                      <h2 style={titleStyle}>{row.strategy_name || row.strategy_key}</h2>
                      <div style={keyStyle}>{row.strategy_key}</div>
                    </div>
                    <div style={statusStyle}>
                      {row.running_instance_count > 0 ? `${row.running_instance_count} RUNNING` : row.live_instance_count > 0 ? `${row.live_instance_count} LIVE` : "DORMANT"}
                    </div>
                  </div>
                  <p style={descStyle}>{row.description || "尚未补充策略说明。"}</p>
                  <div style={metricLineStyle}>
                    <Metric label="Configs" value={row.config_count} />
                    <Metric label="Instances" value={row.instance_count} />
                    <Metric label="Live" value={row.live_instance_count} />
                    <Metric label="Running" value={row.running_instance_count} />
                  </div>
                </div>
              </article>
            </Link>
          ))}
          {!error && rows.length === 0 && <div style={emptyStyle}>Loading strategy catalog...</div>}
        </div>
      </>
    </PageFrame>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return <span><b style={{ color: "var(--fg)" }}>{value}</b> <span style={{ color: "var(--muted)" }}>{label}</span></span>;
}

const summaryStyle: React.CSSProperties = { color: "var(--muted)", fontSize: 13, marginBottom: 14 };
const stackStyle: React.CSSProperties = { display: "grid", gap: 10 };
const linkResetStyle: React.CSSProperties = { color: "inherit", textDecoration: "none" };
const cardStyle: React.CSSProperties = { display: "flex", background: "var(--card)", border: "1px solid var(--stroke)", borderRadius: 8, overflow: "hidden" };
const accentStyle: React.CSSProperties = { width: 4, flexShrink: 0 };
const bodyStyle: React.CSSProperties = { padding: "15px 18px", minWidth: 0, flex: 1 };
const titleLineStyle: React.CSSProperties = { display: "flex", justifyContent: "space-between", gap: 12, alignItems: "start" };
const titleStyle: React.CSSProperties = { margin: 0, fontSize: 17, lineHeight: 1.25 };
const keyStyle: React.CSSProperties = { marginTop: 3, color: "var(--muted)", fontFamily: "IBM Plex Mono, monospace", fontSize: 11, overflowWrap: "anywhere" };
const statusStyle: React.CSSProperties = { color: "var(--muted)", fontSize: 11, fontWeight: 700, whiteSpace: "nowrap" };
const descStyle: React.CSSProperties = { margin: "10px 0 12px", color: "var(--fg)", lineHeight: 1.55, fontSize: 13 };
const metricLineStyle: React.CSSProperties = { display: "flex", gap: 18, flexWrap: "wrap", fontSize: 12 };
const errorStyle: React.CSSProperties = { color: "var(--bad)", marginBottom: 12 };
const emptyStyle: React.CSSProperties = { color: "var(--muted)", padding: 32, textAlign: "center" };
