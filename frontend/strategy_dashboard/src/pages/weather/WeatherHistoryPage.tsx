/**
 * WeatherHistoryPage — Trade history for a specific run.
 * Filters (city, target_date) synced to URL.
 * Route: /weather/history/:runId
 */
import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import { MetricsCard } from "./MetricsCard";
import type { RunDetail, TradeRow } from "../../data/weather-types";

const PNL_COL: React.CSSProperties = {
  fontFamily: "IBM Plex Mono, monospace",
  fontWeight: 600,
  textAlign: "right",
};

function pnlStyle(val: string | null): React.CSSProperties {
  if (!val) return { ...PNL_COL, color: "var(--muted)" };
  const n = parseFloat(val);
  return { ...PNL_COL, color: n > 0 ? "var(--ok)" : n < 0 ? "var(--bad)" : "inherit" };
}

function fmtPnl(val: string | null): string {
  if (!val) return "—";
  const n = parseFloat(val);
  return `${n >= 0 ? "+" : ""}$${n.toFixed(2)}`;
}

export function WeatherHistoryPage() {
  const { runId } = useParams<{ runId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const city = searchParams.get("city") ?? "";
  const targetDate = searchParams.get("date") ?? "";

  const [run, setRun] = useState<RunDetail | null>(null);
  const [trades, setTrades] = useState<TradeRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function setFilter(key: string, val: string) {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (val) next.set(key, val);
      else next.delete(key);
      return next;
    });
  }

  // All cities for this run (fetched once, unfiltered — avoids self-filtering dropdown)
  const [allCities, setAllCities] = useState<string[]>([]);

  // Load run detail (with metrics) + unfiltered city list
  useEffect(() => {
    if (!runId) return;
    weatherApi.getRun(runId)
      .then(setRun)
      .catch((e: Error) => setError(e.message));
    // Fetch unfiltered trades once just for city enumeration
    weatherApi.getRunTrades(runId, { limit: 2000 }).then((rows) => {
      const cs = Array.from(new Set(rows.map((t) => t.city).filter(Boolean) as string[])).sort();
      setAllCities(cs);
    }).catch(() => {});
  }, [runId]);

  // Load trades when filters change
  useEffect(() => {
    if (!runId) return;
    setLoading(true);
    setError(null);
    weatherApi
      .getRunTrades(runId, {
        city: city || undefined,
        target_date: targetDate || undefined,
        limit: 500,
      })
      .then(setTrades)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [runId, city, targetDate]);

  return (
    <PageFrame
      title={`Trade History · ${runId?.slice(0, 12) ?? ""}…`}
      desc="Signal → Order → Fill lineage · 交易溯源"
    >
      <>
        <div style={{ display: "flex", gap: 16, marginBottom: 20, flexWrap: "wrap", alignItems: "flex-start" }}>
          {/* Metrics summary */}
          {run?.metrics && (
            <div style={{ flex: "0 0 260px" }}>
              <MetricsCard metrics={run.metrics} title="Run Metrics" />
            </div>
          )}

          {/* Run info + filters */}
          <div style={{ flex: "1 1 300px" }}>
            {run && (
              <div style={{
                background: "var(--card)", border: "1px solid var(--stroke)",
                borderRadius: 12, padding: "14px 18px", marginBottom: 14, fontSize: 13,
              }}>
                <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                  <KV label="State">{run.state}</KV>
                  <KV label="Mode">{run.execution_mode}</KV>
                  <KV label="Config">{run.config_id.slice(0, 10)}…</KV>
                  {run.date_range_start && (
                    <KV label="Range">{run.date_range_start}{run.date_range_end ? ` → ${run.date_range_end}` : ""}</KV>
                  )}
                </div>
              </div>
            )}

            {/* Filters */}
            <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
              <label style={labelStyle}>
                <span>City / 城市</span>
                <select value={city} onChange={(e) => setFilter("city", e.target.value)} style={selectStyle}>
                  <option value="">All</option>
                  {allCities.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </label>
              <label style={labelStyle}>
                <span>Date / 日期</span>
                <input
                  type="date"
                  value={targetDate}
                  onChange={(e) => setFilter("date", e.target.value)}
                  style={{ ...selectStyle, width: 150 }}
                />
              </label>
              <div style={{ alignSelf: "flex-end", color: "var(--muted)", fontSize: 12 }}>
                {loading ? "Loading…" : `${trades.length} trade(s)`}
              </div>
              <Link
                to={`/weather/compare?run_ids=${runId}`}
                style={{ alignSelf: "flex-end", color: "var(--accent-2)", fontSize: 12 }}
              >
                → Compare view
              </Link>
            </div>
          </div>
        </div>

        {error && <div style={{ color: "var(--bad)", marginBottom: 12 }}>{error}</div>}

        {/* Trades table */}
        <div style={{ overflowX: "auto" }}>
          <table style={tableStyle}>
            <thead>
              <tr>
                {[
                  "Date", "City", "Bracket", "Side", "Model",
                  "P(Yes)", "Market", "Edge",
                  "Shares", "Cost", "Entry",
                  "Settlement", "PnL",
                  "Filled At",
                ].map((h) => (
                  <th key={h} style={thStyle}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {trades.map((t, i) => (
                <tr key={`${t.signal_id}-${i}`} style={{ borderBottom: "1px solid var(--stroke)" }}>
                  <td style={tdStyle}>{t.target_date ?? "—"}</td>
                  <td style={tdStyle}>{t.city ?? "—"}</td>
                  <td style={{ ...tdStyle, fontFamily: "IBM Plex Mono, monospace" }}>{t.bracket ?? "—"}</td>
                  <td style={tdStyle}>
                    <span style={{
                      color: t.order_side === "BUY_YES" ? "var(--ok)" : "var(--accent)",
                      fontWeight: 600, fontSize: 11,
                    }}>
                      {t.order_side ?? t.signal_side ?? "—"}
                    </span>
                  </td>
                  <td style={tdStyle}>{t.model_version ?? "—"}</td>
                  <td style={{ ...tdStyle, fontFamily: "IBM Plex Mono, monospace" }}>
                    {t.model_p_yes ? `${(parseFloat(t.model_p_yes) * 100).toFixed(1)}%` : "—"}
                  </td>
                  <td style={{ ...tdStyle, fontFamily: "IBM Plex Mono, monospace" }}>
                    {t.market_price ? `${(parseFloat(t.market_price) * 100).toFixed(1)}¢` : "—"}
                  </td>
                  <td style={{
                    ...tdStyle,
                    fontFamily: "IBM Plex Mono, monospace",
                    color: t.edge ? (parseFloat(t.edge) > 0 ? "var(--ok)" : "var(--bad)") : "inherit",
                  }}>
                    {t.edge ? `${(parseFloat(t.edge) * 100).toFixed(1)}%` : "—"}
                  </td>
                  <td style={{ ...tdStyle, fontFamily: "IBM Plex Mono, monospace" }}>{t.shares ?? "—"}</td>
                  <td style={{ ...tdStyle, fontFamily: "IBM Plex Mono, monospace" }}>
                    {t.cost_usd ? `$${parseFloat(t.cost_usd).toFixed(2)}` : "—"}
                  </td>
                  <td style={{ ...tdStyle, fontFamily: "IBM Plex Mono, monospace" }}>
                    {t.entry_price ? `${(parseFloat(t.entry_price) * 100).toFixed(1)}¢` : "—"}
                  </td>
                  <td style={tdStyle}>
                    {t.final_yes !== null && t.final_yes !== undefined ? (
                      <span style={{
                        color: t.final_yes === 1 ? "var(--ok)" : "var(--bad)",
                        fontWeight: 600, fontSize: 11,
                      }}>
                        {t.final_yes === 1 ? "YES" : "NO"}
                      </span>
                    ) : (
                      <span style={{ color: "var(--muted)", fontSize: 11 }}>
                        {t.settlement_status ?? "pending"}
                      </span>
                    )}
                  </td>
                  <td style={pnlStyle(t.pnl_usd)}>{fmtPnl(t.pnl_usd)}</td>
                  <td style={{ ...tdStyle, color: "var(--muted)", fontSize: 11 }}>
                    {t.filled_at_utc ? t.filled_at_utc.slice(0, 16).replace("T", " ") : "—"}
                  </td>
                </tr>
              ))}
              {!loading && trades.length === 0 && (
                <tr>
                  <td colSpan={14} style={{ ...tdStyle, color: "var(--muted)", textAlign: "center", padding: 32 }}>
                    No trades found · 暂无交易数据
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </>
    </PageFrame>
  );
}

function KV({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{label}</div>
      <div style={{ fontWeight: 600, fontSize: 13 }}>{children}</div>
    </div>
  );
}

// Styles
const labelStyle: React.CSSProperties = {
  display: "flex", flexDirection: "column", gap: 4, fontSize: 12, color: "var(--muted)",
};
const selectStyle: React.CSSProperties = {
  padding: "6px 10px", borderRadius: 8,
  border: "1px solid var(--stroke)", background: "var(--card)",
  fontSize: 13, cursor: "pointer",
};
const tableStyle: React.CSSProperties = {
  width: "100%", borderCollapse: "collapse", fontSize: 12,
  background: "var(--card)", borderRadius: 12, overflow: "hidden",
};
const thStyle: React.CSSProperties = {
  textAlign: "left", padding: "9px 12px",
  borderBottom: "2px solid var(--stroke)", color: "var(--muted)",
  fontWeight: 600, fontSize: 11, whiteSpace: "nowrap",
};
const tdStyle: React.CSSProperties = { padding: "8px 12px", verticalAlign: "middle" };
