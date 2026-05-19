/**
 * WeatherRunsPage — Browse and filter strategy runs.
 * Filters (state, execution_mode) are synced to URL search params.
 */
import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import type { RunSummary } from "../../data/weather-types";

const STATE_OPTIONS = ["", "explore", "paper", "live", "retired"];
const MODE_OPTIONS = ["", "snapshot_replay", "paper", "live"];

const STATE_COLOR: Record<string, string> = {
  explore: "var(--muted)",
  paper: "var(--accent-2)",
  live: "var(--ok)",
  retired: "var(--bad)",
};

export function WeatherRunsPage() {
  const [params, setParams] = useSearchParams();
  const state = params.get("state") ?? "";
  const mode = params.get("mode") ?? "";

  const [runs, setRuns] = useState<RunSummary[]>([]);
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
    weatherApi
      .listRuns({ state: state || undefined, execution_mode: mode || undefined, limit: 200 })
      .then(setRuns)
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [state, mode]);

  return (
    <PageFrame title="Weather Runs" desc="Browse strategy run registry · 策略运行记录">
      <>
        {/* Filter bar */}
        <div style={{ display: "flex", gap: 12, marginBottom: 20, flexWrap: "wrap" }}>
          <label style={labelStyle}>
            <span>State / 状态</span>
            <select value={state} onChange={(e) => setFilter("state", e.target.value)} style={selectStyle}>
              {STATE_OPTIONS.map((o) => (
                <option key={o} value={o}>{o || "All"}</option>
              ))}
            </select>
          </label>
          <label style={labelStyle}>
            <span>Mode / 模式</span>
            <select value={mode} onChange={(e) => setFilter("mode", e.target.value)} style={selectStyle}>
              {MODE_OPTIONS.map((o) => (
                <option key={o} value={o}>{o || "All"}</option>
              ))}
            </select>
          </label>
          <div style={{ marginLeft: "auto", color: "var(--muted)", fontSize: 13, alignSelf: "center" }}>
            {loading ? "Loading…" : `${runs.length} run(s)`}
          </div>
        </div>

        {error && <div style={{ color: "var(--bad)", marginBottom: 12 }}>{error}</div>}

        {/* Runs table */}
        <div style={{ overflowX: "auto" }}>
          <table style={tableStyle}>
            <thead>
              <tr>
                {["Run ID", "State", "Mode", "Date Range", "Trades", "Settled", "PnL (USD)", "Win%", "ROI", "Tags", "Actions"].map((h) => (
                  <th key={h} style={thStyle}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const m = r.metrics;
                const pnl = m?.total_pnl_usd;
                const pnlColor = pnl == null ? "var(--muted)" : pnl >= 0 ? "var(--ok)" : "var(--bad)";
                return (
                <tr key={r.run_id} style={{ borderBottom: "1px solid var(--stroke)" }}>
                  <td style={tdMono} title={r.run_id}>
                    <Link to={`/weather/history/${r.run_id}`} style={linkStyle}>
                      {r.run_id.slice(0, 14)}…
                    </Link>
                  </td>
                  <td style={tdStyle}>
                    <span style={{
                      color: STATE_COLOR[r.state] ?? "inherit",
                      fontWeight: 600, fontSize: 12,
                    }}>
                      {r.state}
                    </span>
                  </td>
                  <td style={tdStyle}>{r.execution_mode}</td>
                  <td style={tdStyle}>
                    {r.date_range_start && (
                      <span style={{ fontSize: 12 }}>{r.date_range_start}{r.date_range_end ? ` → ${r.date_range_end}` : ""}</span>
                    )}
                  </td>
                  <td style={{ ...tdStyle, textAlign: "right" }}>{m ? m.num_trades : "—"}</td>
                  <td style={{ ...tdStyle, textAlign: "right", color: "var(--muted)", fontSize: 12 }}>
                    {m ? `${m.settled_trades ?? 0}/${m.num_trades}` : "—"}
                  </td>
                  <td style={{ ...tdStyle, textAlign: "right", color: pnlColor, fontWeight: 600 }}>
                    {pnl != null ? `$${pnl.toFixed(2)}` : "—"}
                  </td>
                  <td style={{ ...tdStyle, textAlign: "right" }}>
                    {m?.win_rate != null ? `${(m.win_rate * 100).toFixed(1)}%` : "—"}
                  </td>
                  <td style={{ ...tdStyle, textAlign: "right" }}>
                    {m?.roi != null ? `${(m.roi * 100).toFixed(2)}%` : "—"}
                  </td>
                  <td style={tdStyle}>
                    {r.tags?.map((t) => (
                      <span key={t} style={tagStyle}>{t}</span>
                    ))}
                  </td>
                  <td style={tdStyle}>
                    <Link to={`/weather/history/${r.run_id}`} style={linkStyle}>History</Link>
                    {" · "}
                    <Link to={`/weather/compare?run_ids=${r.run_id}`} style={linkStyle}>Compare</Link>
                  </td>
                </tr>
                );
              })}
              {!loading && runs.length === 0 && (
                <tr>
                  <td colSpan={8} style={{ ...tdStyle, color: "var(--muted)", textAlign: "center", padding: 32 }}>
                    No runs found · 暂无数据
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

// Styles
const labelStyle: React.CSSProperties = {
  display: "flex", flexDirection: "column", gap: 4, fontSize: 12,
  color: "var(--muted)",
};
const selectStyle: React.CSSProperties = {
  padding: "6px 10px", borderRadius: 8,
  border: "1px solid var(--stroke)", background: "var(--card)",
  fontSize: 13, cursor: "pointer",
};
const tableStyle: React.CSSProperties = {
  width: "100%", borderCollapse: "collapse", fontSize: 13,
  background: "var(--card)", borderRadius: 12, overflow: "hidden",
};
const thStyle: React.CSSProperties = {
  textAlign: "left", padding: "10px 14px",
  borderBottom: "2px solid var(--stroke)", color: "var(--muted)",
  fontWeight: 600, fontSize: 12, whiteSpace: "nowrap",
};
const tdStyle: React.CSSProperties = { padding: "10px 14px", verticalAlign: "middle" };
const tdMono: React.CSSProperties = {
  ...tdStyle, fontFamily: "IBM Plex Mono, monospace", fontSize: 12,
};
const tagStyle: React.CSSProperties = {
  display: "inline-block", padding: "2px 7px", borderRadius: 6,
  background: "rgba(42,95,255,0.1)", color: "var(--accent-2)",
  fontSize: 11, marginRight: 4,
};
const linkStyle: React.CSSProperties = { color: "var(--accent-2)", textDecoration: "none" };
