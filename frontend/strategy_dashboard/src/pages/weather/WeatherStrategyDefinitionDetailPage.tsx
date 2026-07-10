import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import type { StrategyDefinitionDetail } from "../../data/weather-types";

function shortId(value: string | null): string {
  return value && value.length > 18 ? `${value.slice(0, 8)}...${value.slice(-6)}` : value || "未绑定";
}

export function WeatherStrategyDefinitionDetailPage(): JSX.Element {
  const { strategyKey = "" } = useParams<{ strategyKey: string }>();
  const [data, setData] = useState<StrategyDefinitionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!strategyKey) return;
    weatherApi.getStrategyDefinition(strategyKey).then(setData).catch((e: Error) => setError(e.message));
  }, [strategyKey]);

  const strategy = data?.strategy;
  return (
    <PageFrame title={strategy?.strategy_name || "Strategy"} desc="Definition → configs → instances">
      <>
        <Link to="/weather/strategies" style={backStyle}>← All strategies</Link>
        {error && <div style={errorStyle}>{error}</div>}
        {!strategy && !error && <div style={emptyStyle}>Loading strategy...</div>}
        {strategy && data && <>
          <section style={heroStyle}>
            <div style={keyStyle}>{strategy.strategy_key}</div>
            <div style={descStyle}>{strategy.description}</div>
            <div style={portfolioStyle}>当前定位 · {strategy.portfolio_note}</div>
            <div style={metricLineStyle}>
              <Metric label="Configs" value={strategy.config_count} />
              <Metric label="Instances" value={strategy.instance_count} />
              <Metric label="Running" value={strategy.running_instance_count} />
            </div>
          </section>

          <section style={sectionStyle}>
            <h2 style={sectionTitleStyle}>Configs</h2>
            <div style={stackStyle}>
              {data.configs.map((config) => (
                <Link key={config.config_id} to={`/weather/configs/${encodeURIComponent(config.config_id)}`} style={rowLinkStyle}>
                  <div style={rowStyle}>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={rowTitleStyle}>{config.name}</div>
                      <div style={keyStyle}>{config.config_id}</div>
                    </div>
                    <div style={rowMetricsStyle}>{config.run_count} runs · {config.instance_count} instances</div>
                  </div>
                </Link>
              ))}
              {data.configs.length === 0 && <div style={emptyStyle}>暂无已归属的 config。</div>}
            </div>
          </section>

          <section style={sectionStyle}>
            <h2 style={sectionTitleStyle}>Instances</h2>
            <div style={stackStyle}>
              {data.instances.map((instance) => (
                <Link key={instance.instance_id} to={`/weather/instances/${encodeURIComponent(instance.instance_id)}`} style={rowLinkStyle}>
                  <div style={rowStyle}>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={rowTitleStyle}>{instance.display_name}</div>
                      <div style={keyStyle}>{instance.instance_id} · config {shortId(instance.config_id)}</div>
                    </div>
                    <div style={rowMetricsStyle}>{instance.lifecycle_status} · {instance.process_status} · {instance.health_status}</div>
                  </div>
                </Link>
              ))}
              {data.instances.length === 0 && <div style={emptyStyle}>暂无部署 instance。</div>}
            </div>
          </section>
        </>}
      </>
    </PageFrame>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return <span><b>{value}</b> <span style={{ color: "var(--muted)" }}>{label}</span></span>;
}

const backStyle: React.CSSProperties = { display: "inline-block", color: "var(--muted)", textDecoration: "none", marginBottom: 16, fontSize: 13 };
const heroStyle: React.CSSProperties = { borderBottom: "1px solid var(--stroke)", paddingBottom: 18 };
const sectionStyle: React.CSSProperties = { marginTop: 24 };
const sectionTitleStyle: React.CSSProperties = { margin: "0 0 10px", fontSize: 16 };
const keyStyle: React.CSSProperties = { color: "var(--muted)", fontFamily: "IBM Plex Mono, monospace", fontSize: 11, overflowWrap: "anywhere" };
const descStyle: React.CSSProperties = { margin: "10px 0 12px", lineHeight: 1.55, fontSize: 14 };
const portfolioStyle: React.CSSProperties = { margin: "-3px 0 12px", color: "var(--muted)", lineHeight: 1.5, fontSize: 13 };
const metricLineStyle: React.CSSProperties = { display: "flex", gap: 18, flexWrap: "wrap", fontSize: 12 };
const stackStyle: React.CSSProperties = { display: "grid", gap: 8 };
const rowLinkStyle: React.CSSProperties = { color: "inherit", textDecoration: "none" };
const rowStyle: React.CSSProperties = { display: "flex", alignItems: "center", gap: 16, border: "1px solid var(--stroke)", borderRadius: 7, padding: "12px 14px", background: "var(--card)" };
const rowTitleStyle: React.CSSProperties = { fontWeight: 650, fontSize: 13, overflowWrap: "anywhere" };
const rowMetricsStyle: React.CSSProperties = { color: "var(--muted)", fontFamily: "IBM Plex Mono, monospace", fontSize: 11, textAlign: "right" };
const emptyStyle: React.CSSProperties = { color: "var(--muted)", padding: 18 };
const errorStyle: React.CSSProperties = { color: "var(--bad)" };
