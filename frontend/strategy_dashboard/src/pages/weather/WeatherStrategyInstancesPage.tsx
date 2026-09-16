import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import type { StrategyInstanceRow } from "../../data/weather-types";

function tone(status: string): string {
  if (status === "running" || status === "healthy") return "var(--ok)";
  if (["stale", "blocked", "crashed"].includes(status)) return "var(--bad)";
  return "var(--muted)";
}

export function WeatherStrategyInstancesPage(): JSX.Element {
  const [rows, setRows] = useState<StrategyInstanceRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { weatherApi.listStrategyInstances().then(setRows).catch((e: Error) => setError(e.message)); }, []);

  return (
    <PageFrame title="Instances" desc="Deployments and desired state · 策略部署实例">
      <>
        {error && <div style={errorStyle}>{error}</div>}
        <div style={summaryStyle}>{rows.length} instances · instance 决定 live/shadow 与启停意图</div>
        <div style={stackStyle}>
          {rows.map((row) => (
            <Link key={row.instance_id} to={`/weather/instances/${encodeURIComponent(row.instance_id)}`} style={linkStyle}>
              <div style={cardStyle}>
                <div style={{ ...accentStyle, background: tone(row.process_status) }} />
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={titleStyle}>{row.display_name}</div>
                  <div style={keyStyle}>{row.instance_id}</div>
                  <div style={subStyle}>{row.strategy_key} · config {row.config_name || "未绑定"}</div>
                </div>
                <div style={statusStyle}>
                  <span style={{ color: tone(row.process_status) }}>{row.process_status}</span>
                  <span>{row.lifecycle_status}</span>
                  <span>{row.desired_status}</span>
                </div>
              </div>
            </Link>
          ))}
          {!error && rows.length === 0 && <div style={emptyStyle}>Loading instances...</div>}
        </div>
      </>
    </PageFrame>
  );
}

const summaryStyle: React.CSSProperties = { color: "var(--muted)", fontSize: 13, marginBottom: 14 };
const stackStyle: React.CSSProperties = { display: "grid", gap: 8 };
const linkStyle: React.CSSProperties = { color: "inherit", textDecoration: "none" };
const cardStyle: React.CSSProperties = { display: "flex", gap: 14, alignItems: "center", padding: "12px 14px", border: "1px solid var(--stroke)", background: "var(--card)", borderRadius: 8 };
const accentStyle: React.CSSProperties = { width: 3, alignSelf: "stretch", flexShrink: 0 };
const titleStyle: React.CSSProperties = { fontWeight: 650, fontSize: 14, overflowWrap: "anywhere" };
const keyStyle: React.CSSProperties = { color: "var(--muted)", fontFamily: "IBM Plex Mono, monospace", fontSize: 11, marginTop: 3, overflowWrap: "anywhere" };
const subStyle: React.CSSProperties = { color: "var(--muted)", fontSize: 11, marginTop: 5, overflowWrap: "anywhere" };
const statusStyle: React.CSSProperties = { display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end", color: "var(--muted)", fontFamily: "IBM Plex Mono, monospace", fontSize: 11, textAlign: "right" };
const errorStyle: React.CSSProperties = { color: "var(--bad)", marginBottom: 12 };
const emptyStyle: React.CSSProperties = { color: "var(--muted)", padding: 24, textAlign: "center" };
