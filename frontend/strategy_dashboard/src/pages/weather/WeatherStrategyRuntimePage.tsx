import { useEffect, useMemo, useState } from "react";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import type { StrategyRuntimeOverview, StrategyRuntimeRow, StrategyShadowQueueRow } from "../../data/weather-types";

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

function listText(items: unknown[] | undefined, limit = 2): string {
  if (!items || items.length === 0) return "-";
  return items.slice(0, limit).map((item) => String(item)).join("; ");
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

export function WeatherStrategyRuntimePage(): JSX.Element {
  const [targetDate, setTargetDate] = useState(tomorrowLocal);
  const [data, setData] = useState<StrategyRuntimeOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
  }, [targetDate]);

  const activeRows = useMemo(() => data?.strategies ?? [], [data]);
  const queueRows = data?.shadow_queue ?? [];

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
        </section>

        {loading && <div style={mutedBlockStyle}>Loading strategy runtime registry...</div>}
        {error && <div style={errorStyle}>{error}</div>}

        {data && !loading && !error && (
          <div style={{ display: "grid", gap: 16 }}>
            <SummaryGrid data={data} />
            <section style={panelStyle}>
              <div style={sectionHeadStyle}>
                <div>
                  <h2 style={sectionTitleStyle}>Strategy Status</h2>
                  <div style={subtleStyle}>{activeRows.length} registered instances · target {data.target_date}</div>
                </div>
              </div>
              <StrategyTable rows={activeRows} />
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

function SummaryGrid({ data }: { data: StrategyRuntimeOverview }) {
  const s = data.summary;
  return (
    <div style={metricGridStyle}>
      <Metric label="Registered" value={String(s.total_strategies)} />
      <Metric label="Live" value={String(s.live_strategies)} color="var(--ok)" />
      <Metric label="Shadow" value={String(s.shadow_strategies)} color="var(--accent)" />
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

function StrategyTable({ rows }: { rows: StrategyRuntimeRow[] }) {
  return (
    <div style={tableWrapStyle}>
      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={thStyle}>Strategy</th>
            <th style={thStyle}>Mode</th>
            <th style={thStyle}>Health</th>
            <th style={thStyle}>Target</th>
            <th style={numThStyle}>Rows</th>
            <th style={thStyle}>Freshness</th>
            <th style={thStyle}>Cap / live</th>
            <th style={thStyle}>Notes</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <StrategyRowView key={row.strategy_instance} row={row} />
          ))}
        </tbody>
      </table>
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

  return (
    <tr>
      <td style={tdStyle}>
        <div style={{ fontWeight: 700 }}>{row.display_name}</div>
        <div style={subtleStyle}>{row.family}</div>
        <div style={monoSmallStyle}>{row.strategy_instance}</div>
      </td>
      <td style={tdStyle}>
        <Badge text={row.lifecycle_status} color={statusColor(row.lifecycle_status)} />
        <div style={{ marginTop: 6, color: "var(--muted)" }}>{row.execution_mode}</div>
      </td>
      <td style={tdStyle}>
        <Badge text={row.health_status} color={statusColor(row.health_status)} />
        <div style={{ marginTop: 6, color: "var(--muted)" }}>age {fmtAge(row.heartbeat_age_min)}</div>
      </td>
      <td style={tdStyle}>
        <Badge text={targetLabel(row.target_status)} color={statusColor(row.target_status)} />
        <div style={{ marginTop: 6, color: "var(--muted)" }}>
          {targetArtifacts || `latest ${row.latest_sample_target_date ?? "-"}`}
        </div>
      </td>
      <td style={numTdStyle}>
        <div>{counts}</div>
        <div style={{ marginTop: 6, color: "var(--muted)" }}>fact {fmtInt(row.fact_trade_rows)} · real {fmtInt(row.fact_live_real_rows)}</div>
      </td>
      <td style={tdStyle}>
        <div>{fmtTime(row.latest_data_ts_utc || row.latest_artifact_mtime_utc)}</div>
        <div style={monoSmallStyle}>{shortPath(row.primary_journal_path)}</div>
      </td>
      <td style={tdStyle}>{cap || "-"}</td>
      <td style={tdStyle}>
        <div>{blockers !== "-" ? blockers : row.notes ?? "-"}</div>
      </td>
    </tr>
  );
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
  gap: 14,
  marginBottom: 16,
  border: "1px solid var(--stroke)",
  background: "var(--card)",
  borderRadius: 8,
  padding: 14,
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
};
const sectionHeadStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
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
const metricGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(138px, 1fr))",
  gap: 10,
};
const metricStyle: React.CSSProperties = {
  border: "1px solid var(--stroke)",
  borderRadius: 8,
  background: "var(--card)",
  padding: 12,
};
const tableWrapStyle: React.CSSProperties = {
  overflowX: "auto",
  marginTop: 12,
};
const tableStyle: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: 12,
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
const errorStyle: React.CSSProperties = {
  color: "var(--bad)",
  border: "1px solid var(--bad)",
  borderRadius: 8,
  padding: 12,
  background: "var(--bad)18",
};
