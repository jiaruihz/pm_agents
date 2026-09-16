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
import { EquityCurve } from "./EquityCurve";
import type { RunDetail, TradeRow } from "../../data/weather-types";

// ─── Slice breakdown table ────────────────────────────────────────────────────

type SliceDim = "city_pool" | "forecast_source" | "model_version" | "side" | "city" | "target_date" | "bracket";

interface SliceRow {
  slice_value: string | null;
  num_trades: number;
  settled_trades: number;
  total_pnl_usd: number;
  win_rate: number | null;
  total_cost_usd: number;
  roi: number | null;
}

function SliceBreakdown({ runId }: { runId: string }) {
  const [dim, setDim] = useState<SliceDim>("city_pool");
  const [rows, setRows] = useState<SliceRow[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    weatherApi.getRunMetricsSlice(runId, dim)
      .then((d) => setRows(d.slices ?? []))
      .catch(() => setRows([]))
      .finally(() => setLoading(false));
  }, [runId, dim]);

  const DIMS: { value: SliceDim; label: string }[] = [
    { value: "city_pool",       label: "T1/T2 Pool" },
    { value: "forecast_source", label: "Forecast Source" },
    { value: "model_version",   label: "Model" },
    { value: "side",            label: "Side (YES/NO)" },
    { value: "city",            label: "City" },
    { value: "target_date",     label: "Date" },
    { value: "bracket",         label: "Bracket" },
  ];

  return (
    <div style={{ marginBottom: 20 }}>
      <div style={{ display: "flex", gap: 8, marginBottom: 10, alignItems: "center", flexWrap: "wrap" }}>
        <span style={{ fontSize: 12, color: "var(--muted)", fontWeight: 600 }}>Breakdown by / 维度切片:</span>
        {DIMS.map(({ value, label }) => (
          <button
            key={value}
            onClick={() => setDim(value)}
            style={{
              padding: "4px 10px", borderRadius: 6, fontSize: 12, cursor: "pointer",
              border: "1px solid var(--stroke)",
              background: dim === value ? "var(--accent-2)" : "var(--card)",
              color: dim === value ? "#fff" : "inherit",
            }}
          >
            {label}
          </button>
        ))}
        {loading && <span style={{ fontSize: 11, color: "var(--muted)" }}>Loading…</span>}
      </div>
      {rows.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12, background: "var(--card)", borderRadius: 8 }}>
            <thead>
              <tr>
                {["Slice", "Trades", "Settled", "Total PnL", "Win%", "Cost", "ROI"].map((h) => (
                  <th key={h} style={{ padding: "8px 12px", textAlign: h === "Slice" ? "left" : "right",
                    borderBottom: "2px solid var(--stroke)", color: "var(--muted)", fontSize: 11 }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const pnlColor = r.total_pnl_usd > 0 ? "var(--ok)" : r.total_pnl_usd < 0 ? "var(--bad)" : "inherit";
                return (
                  <tr key={i} style={{ borderBottom: "1px solid var(--stroke)" }}>
                    <td style={{ padding: "6px 12px", fontFamily: "IBM Plex Mono, monospace", fontSize: 11 }}>
                      {r.slice_value ?? "—"}
                    </td>
                    <td style={{ padding: "6px 12px", textAlign: "right" }}>{r.num_trades}</td>
                    <td style={{ padding: "6px 12px", textAlign: "right", color: "var(--muted)" }}>
                      {r.settled_trades}/{r.num_trades}
                    </td>
                    <td style={{ padding: "6px 12px", textAlign: "right", color: pnlColor, fontWeight: 600, fontFamily: "IBM Plex Mono, monospace" }}>
                      {r.total_pnl_usd != null ? `${r.total_pnl_usd >= 0 ? "+" : ""}$${r.total_pnl_usd.toFixed(2)}` : "—"}
                    </td>
                    <td style={{ padding: "6px 12px", textAlign: "right" }}>
                      {r.win_rate != null ? `${(r.win_rate * 100).toFixed(1)}%` : "—"}
                    </td>
                    <td style={{ padding: "6px 12px", textAlign: "right", color: "var(--muted)", fontFamily: "IBM Plex Mono, monospace" }}>
                      ${r.total_cost_usd.toFixed(2)}
                    </td>
                    <td style={{ padding: "6px 12px", textAlign: "right",
                      color: r.roi != null ? (r.roi > 0 ? "var(--ok)" : "var(--bad)") : "inherit" }}>
                      {r.roi != null ? `${(r.roi * 100).toFixed(2)}%` : "—"}
                    </td>
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
  const cityPool = searchParams.get("pool") ?? "";
  const forecastSource = searchParams.get("source") ?? "";

  const [run, setRun] = useState<RunDetail | null>(null);
  const [trades, setTrades] = useState<TradeRow[]>([]);
  const [equity, setEquity] = useState<{ date: string; cumulative_pnl: number }[]>([]);
  const [allForecastSources, setAllForecastSources] = useState<string[]>([]);
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
    // Equity curve (unfiltered — shows full run performance)
    weatherApi.getRunEquity(runId)
      .then(setEquity)
      .catch(() => {});
    // Fetch unfiltered trades once for dropdown enumeration (city + forecast_source)
    weatherApi.getRunTrades(runId, { limit: 2000 }).then((rows) => {
      const cs = Array.from(new Set(rows.map((t) => t.city).filter(Boolean) as string[])).sort();
      setAllCities(cs);
      const srcs = Array.from(new Set(rows.map((t) => (t as any).forecast_source).filter(Boolean) as string[])).sort();
      setAllForecastSources(srcs);
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
        city_pool: cityPool || undefined,
        forecast_source: forecastSource || undefined,
        limit: 500,
      })
      .then(setTrades)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [runId, city, targetDate, cityPool, forecastSource]);

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
              <label style={labelStyle}>
                <span>Pool / 池</span>
                <select value={cityPool} onChange={(e) => setFilter("pool", e.target.value)} style={selectStyle}>
                  <option value="">All</option>
                  <option value="t1_trading">T1 trading</option>
                  <option value="t2_research">T2 research</option>
                </select>
              </label>
              {allForecastSources.length > 0 && (
                <label style={labelStyle}>
                  <span>Source / 预报源</span>
                  <select value={forecastSource} onChange={(e) => setFilter("source", e.target.value)} style={selectStyle}>
                    <option value="">All</option>
                    {allForecastSources.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </label>
              )}
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

        {/* Equity curve */}
        {equity.length > 1 && (
          <div style={{ marginBottom: 20 }}>
            <EquityCurve points={equity} />
          </div>
        )}

        {/* Slice breakdown */}
        {runId && <SliceBreakdown runId={runId} />}

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
                  <td style={tdStyle}>
                    {t.signal_id && runId ? (
                      <Link to={`/weather/trade/${runId}/${t.signal_id}`} style={{ color: "var(--accent-2)", textDecoration: "none" }}>
                        {t.target_date ?? "—"}
                      </Link>
                    ) : (
                      t.target_date ?? "—"
                    )}
                  </td>
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
                    {t.model_p_yes !== null && t.model_p_yes !== undefined ? `${(t.model_p_yes * 100).toFixed(1)}%` : "—"}
                  </td>
                  <td style={{ ...tdStyle, fontFamily: "IBM Plex Mono, monospace" }}>
                    {t.market_price !== null && t.market_price !== undefined ? `${(t.market_price * 100).toFixed(1)}¢` : "—"}
                  </td>
                  <td style={{
                    ...tdStyle,
                    fontFamily: "IBM Plex Mono, monospace",
                    color: t.edge !== null && t.edge !== undefined ? (t.edge > 0 ? "var(--ok)" : "var(--bad)") : "inherit",
                  }}>
                    {t.edge !== null && t.edge !== undefined ? `${(t.edge * 100).toFixed(1)}%` : "—"}
                  </td>
                  <td style={{ ...tdStyle, fontFamily: "IBM Plex Mono, monospace" }}>{t.shares ?? "—"}</td>
                  <td style={{ ...tdStyle, fontFamily: "IBM Plex Mono, monospace" }}>
                    {t.cost_usd !== null && t.cost_usd !== undefined ? `$${t.cost_usd.toFixed(2)}` : "—"}
                  </td>
                  <td style={{ ...tdStyle, fontFamily: "IBM Plex Mono, monospace" }}>
                    {t.entry_price !== null && t.entry_price !== undefined ? `${(t.entry_price * 100).toFixed(1)}¢` : "—"}
                  </td>
                  <td style={tdStyle}>
                    {t.final_price !== null && t.final_price !== undefined ? (
                      <span style={{
                        color: t.final_price >= 0.5 ? "var(--ok)" : "var(--bad)",
                        fontWeight: 600, fontSize: 11,
                      }}>
                        {t.final_price >= 0.5 ? "YES" : "NO"}
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
