import { useEffect, useState } from "react";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import type { WeatherEdgeV2Latest, WeatherEdgeV2RuleAggregate } from "../../data/weather-types";

function pct(v: number | null | undefined, decimals = 2) {
  return v == null ? "—" : `${(v * 100).toFixed(decimals)}%`;
}

function usd(v: number | null | undefined, decimals = 2) {
  if (v == null) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}$${Math.abs(v).toFixed(decimals)}`;
}

function pnlColor(v: number | null | undefined) {
  if (v == null) return "var(--muted)";
  return v >= 0 ? "var(--ok)" : "var(--bad)";
}

export function WeatherResearchPage(): JSX.Element {
  const [data, setData] = useState<WeatherEdgeV2Latest | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    weatherApi
      .getWeatherEdgeV2Latest()
      .then((res) => {
        setData(res);
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  const lineage = data?.lineage ?? null;
  const liveRecent = lineage?.actual_live_operational_base.recent_from_2026_06_01 ?? null;
  const rows = Object.entries(lineage?.aggregate ?? {}).sort(([a], [b]) => {
    if (a === "legacy_side_band_raw") return -1;
    if (b === "legacy_side_band_raw") return 1;
    return a.localeCompare(b);
  });

  return (
    <PageFrame title="Weather Research" desc="weather_edge_v2 shadow evidence · 研究态，不代表 live 已上线">
      <>
        {loading && <div style={mutedBlockStyle}>Loading weather_edge_v2 research…</div>}
        {error && <div style={errorStyle}>{error}</div>}
        {!loading && !error && !lineage && <div style={mutedBlockStyle}>No weather_edge_v2 artifact found.</div>}
        {lineage && (
          <div style={{ display: "grid", gap: 16 }}>
            <section style={panelStyle}>
              <div style={headerRowStyle}>
                <div>
                  <h2 style={sectionTitleStyle}>weather_edge_v2 Shadow Lineage</h2>
                  <div style={subtleStyle}>
                    generated {lineage.generated_at_utc} · {lineage.city_day_count} city-days · {lineage.lineage_record_count} lineage rows
                  </div>
                </div>
                <div style={{ textAlign: "right", fontSize: 12, color: "var(--muted)" }}>
                  <div>Research JSON</div>
                  <div style={{ fontFamily: "monospace" }}>{data?.lineage_path?.split("/").pop() ?? "—"}</div>
                </div>
              </div>
              <div style={metricGridStyle}>
                <Metric label="Live recent ROI" value={pct(liveRecent?.roi)} color={pnlColor(liveRecent?.roi)} />
                <Metric label="Live recent top5 ROI" value={pct(liveRecent?.roi_excl_top5)} color={pnlColor(liveRecent?.roi_excl_top5)} />
                <Metric label="Live recent fills" value={liveRecent ? String(liveRecent.fills) : "—"} />
                <Metric label="Filtered rows" value={String(lineage.operational_base.filtered_rows)} />
              </div>
            </section>

            <section style={panelStyle}>
              <h2 style={sectionTitleStyle}>Rule Summary</h2>
              <div style={tableWrapStyle}>
                <table style={tableStyle}>
                  <thead>
                    <tr>
                      <th style={thStyle}>Rule</th>
                      <th style={numThStyle}>City-days</th>
                      <th style={numThStyle}>Legs</th>
                      <th style={numThStyle}>PnL</th>
                      <th style={numThStyle}>ROI</th>
                      <th style={numThStyle}>Top5 ROI</th>
                      <th style={numThStyle}>Missed</th>
                      <th style={numThStyle}>Avoided</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(([ruleId, block]) => (
                      <RuleRow key={ruleId} ruleId={ruleId} block={block} />
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section style={panelStyle}>
              <h2 style={sectionTitleStyle}>Highest-Risk Shadow Records</h2>
              <div style={tableWrapStyle}>
                <table style={tableStyle}>
                  <thead>
                    <tr>
                      <th style={thStyle}>City</th>
                      <th style={thStyle}>Date</th>
                      <th style={thStyle}>Rule</th>
                      <th style={numThStyle}>Legs</th>
                      <th style={numThStyle}>Actual</th>
                      <th style={numThStyle}>EV</th>
                      <th style={numThStyle}>CVaR20</th>
                      <th style={numThStyle}>Leave-best EV</th>
                      <th style={numThStyle}>Worst</th>
                    </tr>
                  </thead>
                  <tbody>
                    {lineage.sample_records.slice(0, 30).map((record) => (
                      <tr key={record.lineage_id}>
                        <td style={tdStyle}>{record.city}</td>
                        <td style={tdStyle}>{record.target_date}</td>
                        <td style={monoTdStyle}>{record.rule_id}</td>
                        <td style={numTdStyle}>{record.selected_count}</td>
                        <td style={{ ...numTdStyle, color: pnlColor(record.metrics.actual_pnl_usd) }}>{usd(record.metrics.actual_pnl_usd)}</td>
                        <td style={{ ...numTdStyle, color: pnlColor(record.metrics.expected_value) }}>{usd(record.metrics.expected_value)}</td>
                        <td style={{ ...numTdStyle, color: pnlColor(record.metrics.cvar20) }}>{usd(record.metrics.cvar20)}</td>
                        <td style={{ ...numTdStyle, color: pnlColor(record.metrics.leave_best_out_ev) }}>{usd(record.metrics.leave_best_out_ev)}</td>
                        <td style={{ ...numTdStyle, color: pnlColor(record.metrics.worst_case) }}>{usd(record.metrics.worst_case)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </div>
        )}
      </>
    </PageFrame>
  );
}

function Metric({ label, value, color = "inherit" }: { label: string; value: string; color?: string }) {
  return (
    <div style={metricStyle}>
      <div style={{ fontSize: 12, color: "var(--muted)" }}>{label}</div>
      <div style={{ marginTop: 4, fontSize: 22, fontWeight: 700, color, fontVariantNumeric: "tabular-nums" }}>{value}</div>
    </div>
  );
}

function RuleRow({ ruleId, block }: { ruleId: string; block: WeatherEdgeV2RuleAggregate }) {
  const summary = block.summary;
  const attr = block.vs_legacy_side_band_raw;
  return (
    <tr>
      <td style={monoTdStyle}>{ruleId}</td>
      <td style={numTdStyle}>{block.active_city_days}</td>
      <td style={numTdStyle}>{summary.n_legs}</td>
      <td style={{ ...numTdStyle, color: pnlColor(summary.total_pnl_usd) }}>{usd(summary.total_pnl_usd)}</td>
      <td style={{ ...numTdStyle, color: pnlColor(summary.roi) }}>{pct(summary.roi)}</td>
      <td style={{ ...numTdStyle, color: pnlColor(summary.roi_excl_top5) }}>{pct(summary.roi_excl_top5)}</td>
      <td style={numTdStyle}>{usd(attr?.missed_profit_usd)}</td>
      <td style={numTdStyle}>{usd(attr?.avoided_loss_usd)}</td>
    </tr>
  );
}

const panelStyle: React.CSSProperties = {
  border: "1px solid var(--stroke)",
  background: "var(--card)",
  borderRadius: 8,
  padding: 16,
};
const headerRowStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  gap: 16,
  alignItems: "flex-start",
  marginBottom: 14,
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
const metricGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
  gap: 10,
};
const metricStyle: React.CSSProperties = {
  border: "1px solid var(--stroke)",
  borderRadius: 6,
  padding: 12,
  background: "rgba(255,255,255,0.02)",
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
};
const numThStyle: React.CSSProperties = {
  ...thStyle,
  textAlign: "right",
};
const tdStyle: React.CSSProperties = {
  borderBottom: "1px solid var(--stroke)",
  padding: "8px 6px",
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
};
const mutedBlockStyle: React.CSSProperties = {
  color: "var(--muted)",
  textAlign: "center",
  padding: 40,
};
const errorStyle: React.CSSProperties = {
  color: "var(--bad)",
  border: "1px solid var(--bad)",
  borderRadius: 6,
  padding: 12,
};
