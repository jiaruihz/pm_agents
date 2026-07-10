import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import type { StrategyInstanceRow } from "../../data/weather-types";

function text(value: unknown): string { return value == null || value === "" ? "-" : String(value); }

export function WeatherStrategyInstanceDetailPage(): JSX.Element {
  const { instanceId = "" } = useParams<{ instanceId: string }>();
  const [instance, setInstance] = useState<StrategyInstanceRow | null>(null);
  const [controlLog, setControlLog] = useState<Array<Record<string, unknown>>>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!instanceId) return;
    weatherApi.getStrategyInstance(instanceId).then((data) => {
      setInstance(data.instance);
      setControlLog(data.control_log);
    }).catch((e: Error) => setError(e.message));
  }, [instanceId]);

  return (
    <PageFrame title={instance?.display_name || "Instance"} desc="Deployment identity and actual runtime state">
      <>
        <Link to="/weather/instances" style={backStyle}>← All instances</Link>
        {error && <div style={errorStyle}>{error}</div>}
        {!instance && !error && <div style={emptyStyle}>Loading instance...</div>}
        {instance && <>
          <section style={gridStyle}>
            <Info title="Strategy" value={instance.strategy_key} href={`/weather/strategies/${encodeURIComponent(instance.strategy_key)}`} />
            <Info title="Config" value={instance.config_name || "未绑定"} href={instance.config_id ? `/weather/configs/${encodeURIComponent(instance.config_id)}` : undefined} />
            <Info title="Desired" value={instance.desired_status} />
            <Info title="Process" value={instance.process_status} />
            <Info title="Health" value={instance.health_status} />
            <Info title="Mode" value={`${instance.lifecycle_status} · ${instance.execution_mode}`} />
          </section>
          <section style={sectionStyle}>
            <h2 style={sectionTitleStyle}>Runtime</h2>
            <div style={gridStyle}>
              <Info title="Heartbeat" value={text(instance.heartbeat_at_utc)} />
              <Info title="Last tick" value={text(instance.last_tick_ts_utc)} />
              <Info title="Last data" value={text(instance.last_data_ts_utc)} />
              <Info title="Plans / orders" value={`${text(instance.plan_rows)} / ${text(instance.live_order_rows)}`} />
              <Info title="Fact trades" value={text(instance.fact_trade_rows)} />
              <Info title="Blockers" value={text(instance.blocker_count)} />
            </div>
            <div style={{ marginTop: 12 }}>
              <Link to={`/weather/orders?instance_id=${encodeURIComponent(instance.instance_id)}&trade_class=all`} style={actionStyle}>View orders and fills →</Link>
              <Link to={`/weather/runtime?instance_id=${encodeURIComponent(instance.instance_id)}`} style={actionStyle}>View runtime telemetry →</Link>
            </div>
          </section>
          <section style={sectionStyle}>
            <h2 style={sectionTitleStyle}>Control log</h2>
            {controlLog.length === 0 ? <div style={emptyStyle}>No control actions recorded.</div> : controlLog.map((row, index) => (
              <div key={String(row.log_id ?? index)} style={logRowStyle}>{text(row.ts_utc)} · {text(row.action)} · {text(row.reason)}</div>
            ))}
          </section>
        </>}
      </>
    </PageFrame>
  );
}

function Info({ title, value, href }: { title: string; value: string; href?: string }) {
  const body = <div style={valueStyle}>{value}</div>;
  return <div style={infoStyle}><div style={labelStyle}>{title}</div>{href ? <Link to={href} style={infoLinkStyle}>{body}</Link> : body}</div>;
}

const backStyle: React.CSSProperties = { display: "inline-block", color: "var(--muted)", textDecoration: "none", marginBottom: 16, fontSize: 13 };
const gridStyle: React.CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(180px, 100%), 1fr))", gap: 8 };
const infoStyle: React.CSSProperties = { border: "1px solid var(--stroke)", borderRadius: 7, background: "var(--card)", padding: "11px 12px", minWidth: 0 };
const labelStyle: React.CSSProperties = { color: "var(--muted)", fontSize: 10, textTransform: "uppercase", marginBottom: 4 };
const valueStyle: React.CSSProperties = { fontFamily: "IBM Plex Mono, monospace", fontSize: 12, overflowWrap: "anywhere" };
const infoLinkStyle: React.CSSProperties = { color: "var(--accent-2)", textDecoration: "none" };
const sectionStyle: React.CSSProperties = { marginTop: 24 };
const sectionTitleStyle: React.CSSProperties = { margin: "0 0 10px", fontSize: 16 };
const actionStyle: React.CSSProperties = { color: "var(--accent-2)", textDecoration: "none", marginRight: 16, fontSize: 13 };
const logRowStyle: React.CSSProperties = { padding: "8px 0", borderBottom: "1px solid var(--stroke)", fontFamily: "IBM Plex Mono, monospace", fontSize: 11, overflowWrap: "anywhere" };
const emptyStyle: React.CSSProperties = { color: "var(--muted)", padding: 16 };
const errorStyle: React.CSSProperties = { color: "var(--bad)" };
