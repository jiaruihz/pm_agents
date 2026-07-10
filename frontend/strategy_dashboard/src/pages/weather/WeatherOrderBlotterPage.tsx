import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import type { OrderBlotterRow } from "../../data/v2-types";

const TRADE_CLASS_OPTIONS = ["live_real", "paper", "snapshot_replay", "live_simulated", "all"];
const STATUS_OPTIONS = ["all", "open", "settled", "unfilled"];
const TABLE_COLUMNS = [
  ["Time", 152],
  ["Kind", 68],
  ["Class", 112],
  ["Instance", 160],
  ["Strategy ID", 132],
  ["Strategy", 360],
  ["Date", 108],
  ["City", 124],
  ["Bracket", 92],
  ["Side", 104],
  ["Order", 104],
  ["Fill", 92],
  ["Entry / Fill / Now", 158],
  ["Shares", 86],
  ["Cost", 86],
  ["Realized / MTM", 132],
  ["Run", 150],
] as const;

function money(n: number | null | undefined, sign = false): string {
  if (n == null) return "-";
  const abs = `$${Math.abs(n).toFixed(2)}`;
  if (n < 0) return `-${abs}`;
  return sign && n > 0 ? `+${abs}` : abs;
}

function px(n: number | null | undefined): string {
  return n == null ? "-" : n.toFixed(4);
}

function shortId(value: string | null | undefined): string {
  if (!value) return "-";
  return value.length <= 18 ? value : `${value.slice(0, 8)}...${value.slice(-6)}`;
}

function ts(value: string | null | undefined): string {
  return value ? value.slice(0, 19).replace("T", " ") : "-";
}

function pnlColor(n: number | null | undefined): string {
  if (n == null) return "var(--muted)";
  return n > 0 ? "var(--ok)" : n < 0 ? "var(--bad)" : "inherit";
}

export function WeatherOrderBlotterPage() {
  const [params, setParams] = useSearchParams();
  const tradeClass = params.get("trade_class") ?? "live_real";
  const status = params.get("status") ?? "all";
  const targetDate = params.get("target_date") ?? "";
  const city = params.get("city") ?? "";
  const configId = params.get("config_id") ?? "";
  const instanceId = params.get("instance_id") ?? "";

  const [rows, setRows] = useState<OrderBlotterRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function setFilter(key: string, val: string) {
    setParams((prev) => {
      const next = new URLSearchParams(prev);
      if (val) next.set(key, val);
      else next.delete(key);
      return next;
    });
  }

  useEffect(() => {
    setLoading(true);
    setError(null);
    weatherApi.getOrderBlotter({
      trade_class: tradeClass,
      status,
      instance_id: instanceId || undefined,
      target_date: targetDate || undefined,
      city: city || undefined,
      config_id: configId || undefined,
      limit: 400,
    }).then((res) => {
      setRows(res.rows);
      setTotal(res.total);
    }).catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [tradeClass, status, targetDate, city, configId, instanceId]);

  const realized = rows.reduce((s, r) => s + (r.pnl_usd_at_fill ?? 0), 0);
  const mtm = rows.reduce((s, r) => s + (r.unrealized_pnl_mid ?? 0), 0);
  const unfilled = rows.filter((r) => r.row_kind === "order").length;

  return (
    <PageFrame title="Order Blotter" desc="Unified order/fill ledger · 订单成交明细">
      <>
        <div style={filterBarStyle}>
          <label style={labelStyle}>
            <span>Class</span>
            <select value={tradeClass} onChange={(e) => setFilter("trade_class", e.target.value)} style={selectStyle}>
              {TRADE_CLASS_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          </label>
          <label style={labelStyle}>
            <span>Status</span>
            <select value={status} onChange={(e) => setFilter("status", e.target.value)} style={selectStyle}>
              {STATUS_OPTIONS.map((o) => <option key={o} value={o}>{o}</option>)}
            </select>
          </label>
          <label style={labelStyle}>
            <span>Date</span>
            <input value={targetDate} onChange={(e) => setFilter("target_date", e.target.value)} placeholder="2026-07-09" style={inputStyle} />
          </label>
          <label style={labelStyle}>
            <span>City</span>
            <input value={city} onChange={(e) => setFilter("city", e.target.value)} placeholder="Helsinki" style={inputStyle} />
          </label>
          <label style={labelStyle}>
            <span>Instance</span>
            <input value={instanceId} onChange={(e) => setFilter("instance_id", e.target.value)} placeholder="low_price_yes..." style={{ ...inputStyle, minWidth: 220 }} />
          </label>
          <div style={{ marginLeft: "auto", color: "var(--muted)", fontSize: 12 }}>
            {loading ? "Loading..." : `${rows.length}/${total} rows`}
            {" · unfilled "}
            {unfilled}
            {" · realized "}
            <span style={{ color: pnlColor(realized), fontWeight: 700 }}>{money(realized, true)}</span>
            {" · MTM "}
            <span style={{ color: pnlColor(mtm), fontWeight: 700 }}>{money(mtm, true)}</span>
          </div>
        </div>

        {error && <div style={{ color: "var(--bad)", marginBottom: 12 }}>{error}</div>}

        <div style={tableWrapStyle}>
          <table style={tableStyle}>
            <colgroup>
              {TABLE_COLUMNS.map(([name, width]) => <col key={name} style={{ width }} />)}
            </colgroup>
            <thead>
              <tr>
                {TABLE_COLUMNS.map(([h]) => (
                  <th key={h} style={thStyle}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, index) => {
                const rowPnl = r.pnl_usd_at_fill ?? r.unrealized_pnl_mid;
                return (
                  <tr key={`${r.row_kind}-${r.fill_id ?? r.execution_id ?? index}`} style={{ borderBottom: "1px solid var(--stroke)" }}>
                    <td style={tdStyle}>{ts(r.fill_ts_utc ?? r.order_ts_utc)}</td>
                    <td style={tdStyle}>{r.row_kind}</td>
                    <td style={tdStyle}>{r.trade_class ?? "-"}</td>
                    <td style={monoTdStyle} title={r.strategy_instance ?? undefined}>{shortId(r.strategy_instance)}</td>
                    <td style={monoTdStyle} title={r.strategy_id ?? r.config_id ?? undefined}>{shortId(r.strategy_id ?? r.config_id)}</td>
                    <td style={tdStyle} title={r.strategy_name ?? undefined}>{r.strategy_name ?? "-"}</td>
                    <td style={tdStyle}>{r.target_date ?? "-"}</td>
                    <td style={tdStyle}>{r.city ?? "-"}</td>
                    <td style={monoTdStyle}>{r.bracket ?? "-"}</td>
                    <td style={tdStyle}>{r.side ?? "-"}</td>
                    <td style={tdStyle}>{r.order_status ?? "-"}</td>
                    <td style={tdStyle}>{r.fill_status ?? "-"}</td>
                    <td style={monoTdStyle}>{px(r.market_price)} / {px(r.fill_price ?? r.limit_price)} / {px(r.val_mid)}</td>
                    <td style={monoTdStyle}>{r.fill_qty != null ? r.fill_qty.toFixed(3) : "-"}</td>
                    <td style={monoTdStyle}>{money(r.cost_usd)}</td>
                    <td style={{ ...monoTdStyle, color: pnlColor(rowPnl), fontWeight: 700 }}>{money(rowPnl, true)}</td>
                    <td style={monoTdStyle} title={r.run_id ?? undefined}>
                      {r.run_id ? <Link to={`/weather/history/${r.run_id}`} style={linkStyle}>{shortId(r.run_id)}</Link> : "-"}
                    </td>
                  </tr>
                );
              })}
              {!loading && rows.length === 0 && (
                <tr><td colSpan={17} style={{ ...tdStyle, textAlign: "center", color: "var(--muted)", padding: 28 }}>No orders found.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </>
    </PageFrame>
  );
}

const filterBarStyle: React.CSSProperties = {
  display: "flex",
  gap: 10,
  flexWrap: "wrap",
  alignItems: "flex-end",
  marginBottom: 14,
};
const labelStyle: React.CSSProperties = { display: "flex", flexDirection: "column", gap: 4, fontSize: 11, color: "var(--muted)" };
const selectStyle: React.CSSProperties = { padding: "6px 9px", border: "1px solid var(--stroke)", borderRadius: 6, background: "var(--card)" };
const inputStyle: React.CSSProperties = { ...selectStyle, minWidth: 120 };
const tableWrapStyle: React.CSSProperties = {
  overflowX: "auto",
  background: "var(--card)",
  border: "1px solid var(--stroke)",
  borderRadius: 8,
  maxWidth: "100%",
};
const tableStyle: React.CSSProperties = {
  width: "100%",
  minWidth: TABLE_COLUMNS.reduce((sum, [, width]) => sum + width, 0),
  tableLayout: "fixed",
  borderCollapse: "collapse",
  fontSize: 12,
};
const ellipsisStyle: React.CSSProperties = {
  overflow: "hidden",
  textOverflow: "ellipsis",
  whiteSpace: "nowrap",
};
const thStyle: React.CSSProperties = {
  ...ellipsisStyle,
  textAlign: "left",
  padding: "8px 10px",
  color: "var(--muted)",
  borderBottom: "1px solid var(--stroke)",
};
const tdStyle: React.CSSProperties = {
  ...ellipsisStyle,
  padding: "7px 10px",
  verticalAlign: "middle",
};
const monoTdStyle: React.CSSProperties = { ...tdStyle, fontFamily: "IBM Plex Mono, monospace", fontSize: 11 };
const linkStyle: React.CSSProperties = {
  color: "var(--accent-2)",
  textDecoration: "none",
  display: "block",
  overflow: "hidden",
  textOverflow: "ellipsis",
};
