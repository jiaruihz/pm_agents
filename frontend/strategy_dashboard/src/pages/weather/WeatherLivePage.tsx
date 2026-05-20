/**
 * WeatherLivePage — Quantitative blood lineage live monitor.
 *
 * Panel A: Current CLOB positions (filled real orders + settlement status)
 * Panel B: Paper vs CLOB execution gap (price slippage, sizing, PnL delta)
 *
 * Polls every 30s.  Route: /weather/live
 */
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import type { ExecutionGapRow, LivePosition, LiveSummary } from "../../data/weather-types";

const POLL_MS = 30_000;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function pct(n: number | null, digits = 1): string {
  if (n == null) return "—";
  return `${(n * 100).toFixed(digits)}%`;
}

function usd(n: number | null, sign = false): string {
  if (n == null) return "—";
  const prefix = sign && n > 0 ? "+" : "";
  return `${prefix}$${Math.abs(n).toFixed(2)}`;
}

function pnlColor(n: number | null): string {
  if (n == null) return "var(--muted)";
  return n > 0 ? "var(--ok)" : n < 0 ? "var(--bad)" : "inherit";
}

function sideTag(side: string | null): React.ReactNode {
  if (!side) return "—";
  const yes = side === "BUY_YES";
  return (
    <span style={{ color: yes ? "var(--ok)" : "var(--accent)", fontWeight: 700 }}>
      {yes ? "YES" : "NO"}
    </span>
  );
}

function Badge({ text, color }: { text: string; color?: string }) {
  return (
    <span style={{
      display: "inline-block", padding: "1px 7px", borderRadius: 99,
      fontSize: 10, fontWeight: 700, letterSpacing: "0.04em",
      background: color ? `${color}22` : "var(--stroke)",
      color: color ?? "var(--muted)",
      border: `1px solid ${color ?? "var(--stroke)"}`,
    }}>{text}</span>
  );
}

// ---------------------------------------------------------------------------
// Page component
// ---------------------------------------------------------------------------

export function WeatherLivePage() {
  const [summary, setSummary] = useState<LiveSummary | null>(null);
  const [positions, setPositions] = useState<LivePosition[]>([]);
  const [gap, setGap] = useState<ExecutionGapRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastPoll, setLastPoll] = useState<Date | null>(null);
  const [activePanel, setActivePanel] = useState<"positions" | "gap">("positions");
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function pollOnce() {
    try {
      const [s, pos, g] = await Promise.all([
        weatherApi.getLiveSummary(),
        weatherApi.getLivePositions({ limit: 200 }),
        weatherApi.getExecutionGap({ limit: 200 }),
      ]);
      setSummary(s);
      setPositions(pos);
      setGap(g);
      setLastPoll(new Date());
      setError(null);
    } catch (e: unknown) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    pollOnce();
    timerRef.current = setInterval(pollOnce, POLL_MS);
    return () => { if (timerRef.current) clearInterval(timerRef.current); };
  }, []);

  return (
    <PageFrame title="Live Monitor" desc="CLOB positions · execution gap · 实盘监控">
      <>
        {/* Status bar */}
        <div style={{ display: "flex", gap: 16, alignItems: "center", marginBottom: 16, fontSize: 11, color: "var(--muted)" }}>
          <span>
            <span style={{
              display: "inline-block", width: 7, height: 7, borderRadius: "50%",
              background: error ? "var(--bad)" : "var(--ok)",
              marginRight: 5, verticalAlign: "middle",
            }} />
            Polling every {POLL_MS / 1000}s
          </span>
          {lastPoll && <span>Updated {lastPoll.toLocaleTimeString()}</span>}
          {error && <span style={{ color: "var(--bad)" }}>{error}</span>}
          {loading && !lastPoll && <span>Loading…</span>}
          <span style={{ marginLeft: "auto" }}>
            <Link to="/weather/runs" style={{ color: "var(--accent-2)", fontSize: 11 }}>← All runs</Link>
          </span>
        </div>

        {/* Summary cards */}
        {summary && <SummaryBar summary={summary} />}

        {/* Panel tabs */}
        <div style={{ display: "flex", gap: 4, marginBottom: 16, borderBottom: "1px solid var(--stroke)", paddingBottom: 0 }}>
          {(["positions", "gap"] as const).map((p) => (
            <button
              key={p}
              onClick={() => setActivePanel(p)}
              style={{
                padding: "7px 16px", fontSize: 12, fontWeight: 600,
                border: "none", background: "none", cursor: "pointer",
                color: activePanel === p ? "var(--accent)" : "var(--muted)",
                borderBottom: activePanel === p ? "2px solid var(--accent)" : "2px solid transparent",
                marginBottom: -1,
              }}
            >
              {p === "positions" ? `Panel A — CLOB Positions (${positions.length})` : `Panel B — Execution Gap (${gap.length})`}
            </button>
          ))}
        </div>

        {activePanel === "positions" && (
          <PositionsPanel positions={positions} loading={loading} />
        )}
        {activePanel === "gap" && (
          <ExecutionGapPanel rows={gap} loading={loading} />
        )}
      </>
    </PageFrame>
  );
}

// ---------------------------------------------------------------------------
// Summary bar
// ---------------------------------------------------------------------------

function SummaryBar({ summary }: { summary: LiveSummary }) {
  const { clob, pending_orders, paper_baseline } = summary;
  const paperRoi: number | null = paper_baseline.metrics?.roi ?? null;

  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "repeat(auto-fill, minmax(140px, 1fr))",
      gap: 10, marginBottom: 20,
    }}>
      <SummaryCard label="Last Cycle" value={
        summary.last_cycle_utc
          ? new Date(summary.last_cycle_utc).toLocaleString()
          : "—"
      } />
      <SummaryCard label="CLOB Positions" value={String(clob.total_positions)} />
      <SummaryCard
        label="Open / Settled"
        value={`${clob.open_count} / ${clob.settled_count}`}
      />
      <SummaryCard
        label="Capital Deployed"
        value={usd(clob.capital_deployed_usd)}
      />
      <SummaryCard
        label="Realized PnL"
        value={usd(clob.realized_pnl_usd, true)}
        valueColor={pnlColor(clob.realized_pnl_usd)}
      />
      <SummaryCard
        label="Pending Orders"
        value={`${pending_orders.count} (${usd(pending_orders.reserved_usd)})`}
      />
      <SummaryCard
        label="Paper Baseline ROI"
        value={pct(paperRoi)}
        valueColor={paperRoi != null ? pnlColor(paperRoi) : undefined}
        sub={paper_baseline.run_id ? `run: ${paper_baseline.run_id.slice(0, 10)}…` : undefined}
      />
    </div>
  );
}

function SummaryCard({ label, value, valueColor, sub }: {
  label: string; value: string; valueColor?: string; sub?: string;
}) {
  return (
    <div style={{
      background: "var(--card)", border: "1px solid var(--stroke)",
      borderRadius: 10, padding: "10px 14px",
    }}>
      <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 3 }}>{label}</div>
      <div style={{ fontWeight: 700, fontSize: 14, color: valueColor }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>{sub}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel A: CLOB Positions
// ---------------------------------------------------------------------------

function PositionsPanel({ positions, loading }: { positions: LivePosition[]; loading: boolean }) {
  const [filterStatus, setFilterStatus] = useState<"all" | "open" | "settled">("all");

  const filtered = positions.filter((p) => {
    if (filterStatus === "open") return p.final_price == null;
    if (filterStatus === "settled") return p.final_price != null;
    return true;
  });

  if (loading && positions.length === 0) {
    return <div style={{ color: "var(--muted)", padding: 32, textAlign: "center" }}>Loading…</div>;
  }
  if (positions.length === 0) {
    return (
      <div style={{ color: "var(--muted)", padding: 32, textAlign: "center" }}>
        No CLOB positions found.<br />
        <small>Run a refresh to pull latest fills from Polymarket.</small>
      </div>
    );
  }

  const totalPnl = filtered.reduce((s, p) => s + (p.pnl_usd ?? 0), 0);
  const settled = filtered.filter((p) => p.pnl_usd != null);
  const winners = settled.filter((p) => (p.pnl_usd ?? 0) > 0).length;

  return (
    <div>
      {/* Filter + aggregate bar */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12 }}>
        {(["all", "open", "settled"] as const).map((s) => (
          <button key={s} onClick={() => setFilterStatus(s)} style={{
            padding: "4px 12px", fontSize: 11, borderRadius: 99, cursor: "pointer",
            border: "1px solid var(--stroke)",
            background: filterStatus === s ? "var(--accent)" : "transparent",
            color: filterStatus === s ? "#fff" : "var(--muted)",
            fontWeight: filterStatus === s ? 700 : 400,
          }}>{s}</button>
        ))}
        <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--muted)" }}>
          {filtered.length} positions
          {settled.length > 0 && (
            <>
              {" · "}
              <span style={{ color: pnlColor(totalPnl), fontWeight: 700 }}>{usd(totalPnl, true)}</span>
              {" settled PnL"}
              {" · "}
              {winners}/{settled.length} wins
              {" ("}
              {settled.length > 0 ? pct(winners / settled.length, 0) : "—"}
              {")"}
            </>
          )}
        </span>
      </div>

      {/* Table */}
      <div style={{ overflowX: "auto", background: "var(--card)", border: "1px solid var(--stroke)", borderRadius: 10 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--stroke)" }}>
              {["Target Date", "City", "Bracket", "Pool", "Side", "Fill Price", "Shares", "Cost", "Signal Edge", "Settled?", "PnL"].map((h) => (
                <th key={h} style={thStyle}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {filtered.map((p) => {
              const isOpen = p.final_price == null;
              return (
                <tr key={p.fill_id} style={{ borderBottom: "1px solid var(--stroke)" }}>
                  <td style={tdStyle}>{p.target_date ?? "—"}</td>
                  <td style={tdStyle}>{p.city ?? "—"}</td>
                  <td style={{ ...tdStyle, fontFamily: "monospace" }}>{p.bracket ?? "—"}</td>
                  <td style={tdStyle}>
                    <Badge
                      text={p.city_pool === "t1_trading" ? "T1" : p.city_pool === "t2_research" ? "T2" : p.city_pool ?? "—"}
                      color={p.city_pool === "t1_trading" ? "var(--accent)" : undefined}
                    />
                  </td>
                  <td style={tdStyle}>{sideTag(p.order_side)}</td>
                  <td style={{ ...tdStyle, fontFamily: "monospace" }}>
                    {p.filled_price != null ? p.filled_price.toFixed(4) : "—"}
                  </td>
                  <td style={{ ...tdStyle, fontFamily: "monospace" }}>
                    {p.filled_shares != null ? p.filled_shares.toFixed(3) : "—"}
                  </td>
                  <td style={{ ...tdStyle, fontFamily: "monospace" }}>{usd(p.cost_usd)}</td>
                  <td style={{ ...tdStyle, fontFamily: "monospace", color: (p.signal_edge ?? 0) > 0 ? "var(--ok)" : "var(--muted)" }}>
                    {p.signal_edge != null ? `${(p.signal_edge * 100).toFixed(1)}%` : "—"}
                  </td>
                  <td style={tdStyle}>
                    {isOpen ? (
                      <Badge text="Open" color="var(--accent-2)" />
                    ) : (
                      <Badge
                        text={p.final_price != null && p.final_price >= 0.99 ? "YES ✓" : "NO ✓"}
                        color={p.final_price != null && p.final_price >= 0.99 ? "var(--ok)" : "var(--bad)"}
                      />
                    )}
                  </td>
                  <td style={{ ...tdStyle, fontFamily: "monospace", fontWeight: 700, color: pnlColor(p.pnl_usd) }}>
                    {p.pnl_usd != null ? usd(p.pnl_usd, true) : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel B: Execution Gap
// ---------------------------------------------------------------------------

function ExecutionGapPanel({ rows, loading }: { rows: ExecutionGapRow[]; loading: boolean }) {
  const [filterGap, setFilterGap] = useState<"all" | "both" | "paper_only_clob_rejected">("all");

  const filtered = rows.filter((r) => {
    if (filterGap === "both") return r.gap_type === "both";
    if (filterGap === "paper_only_clob_rejected") return r.gap_type === "paper_only_clob_rejected";
    return true;
  });

  if (loading && rows.length === 0) {
    return <div style={{ color: "var(--muted)", padding: 32, textAlign: "center" }}>Loading…</div>;
  }
  if (rows.length === 0) {
    return (
      <div style={{ color: "var(--muted)", padding: 32, textAlign: "center" }}>
        No execution gap data.<br />
        <small>Gap data appears when there are both paper and CLOB orders for the same signal.</small>
      </div>
    );
  }

  // Aggregate stats for "both" rows
  const bothRows = rows.filter((r) => r.gap_type === "both" && r.pnl_gap_usd != null);
  const avgPnlGap = bothRows.length > 0
    ? bothRows.reduce((s, r) => s + (r.pnl_gap_usd ?? 0), 0) / bothRows.length
    : null;
  const avgSlippage = bothRows.filter((r) => r.price_slippage != null).length > 0
    ? bothRows.reduce((s, r) => s + (r.price_slippage ?? 0), 0) / bothRows.length
    : null;
  const rejectedCount = rows.filter((r) => r.gap_type === "paper_only_clob_rejected").length;

  return (
    <div>
      {/* Agg stats */}
      <div style={{ display: "flex", gap: 12, marginBottom: 14, flexWrap: "wrap" }}>
        <StatChip label="Paired (paper+CLOB)" value={String(bothRows.length)} />
        <StatChip label="CLOB Rejected" value={String(rejectedCount)} valueColor={rejectedCount > 0 ? "var(--bad)" : undefined} />
        <StatChip
          label="Avg PnL Gap (real−paper)"
          value={usd(avgPnlGap, true)}
          valueColor={pnlColor(avgPnlGap)}
          help="Positive = real outperformed paper"
        />
        <StatChip
          label="Avg Price Slippage"
          value={avgSlippage != null ? avgSlippage.toFixed(4) : "—"}
          valueColor={avgSlippage != null && Math.abs(avgSlippage) > 0.01 ? "var(--bad)" : undefined}
          help="CLOB fill price − paper fill price"
        />
      </div>

      {/* Filter */}
      <div style={{ display: "flex", gap: 6, marginBottom: 10 }}>
        {([["all", "All"], ["both", "Paired only"], ["paper_only_clob_rejected", "CLOB Rejected"]] as const).map(([val, label]) => (
          <button key={val} onClick={() => setFilterGap(val)} style={{
            padding: "4px 12px", fontSize: 11, borderRadius: 99, cursor: "pointer",
            border: "1px solid var(--stroke)",
            background: filterGap === val ? "var(--accent)" : "transparent",
            color: filterGap === val ? "#fff" : "var(--muted)",
            fontWeight: filterGap === val ? 700 : 400,
          }}>{label}</button>
        ))}
        <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--muted)" }}>{filtered.length} signals</span>
      </div>

      {/* Table */}
      <div style={{ overflowX: "auto", background: "var(--card)", border: "1px solid var(--stroke)", borderRadius: 10 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 11 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--stroke)" }}>
              {[
                "Target", "City", "Bracket", "Side", "Model P",
                "Paper Fill", "CLOB Fill", "Slippage",
                "Paper Shares", "CLOB Shares",
                "Paper PnL", "CLOB PnL", "Gap",
                "Type",
              ].map((h) => <th key={h} style={thStyle}>{h}</th>)}
            </tr>
          </thead>
          <tbody>
            {filtered.map((r) => (
              <tr key={r.signal_id} style={{ borderBottom: "1px solid var(--stroke)" }}>
                <td style={tdStyle}>{r.target_date ?? "—"}</td>
                <td style={tdStyle}>{r.city ?? "—"}</td>
                <td style={{ ...tdStyle, fontFamily: "monospace" }}>{r.bracket ?? "—"}</td>
                <td style={tdStyle}>{sideTag(r.signal_side)}</td>
                <td style={{ ...tdStyle, fontFamily: "monospace" }}>
                  {r.model_p_yes != null ? pct(r.model_p_yes) : "—"}
                </td>
                <td style={{ ...tdStyle, fontFamily: "monospace" }}>
                  {r.paper_fill_price != null ? r.paper_fill_price.toFixed(4) : "—"}
                </td>
                <td style={{ ...tdStyle, fontFamily: "monospace" }}>
                  {r.clob_fill_price != null ? r.clob_fill_price.toFixed(4) : "—"}
                </td>
                <td style={{ ...tdStyle, fontFamily: "monospace", color: r.price_slippage != null && Math.abs(r.price_slippage) > 0.01 ? "var(--bad)" : undefined }}>
                  {r.price_slippage != null ? (r.price_slippage >= 0 ? "+" : "") + r.price_slippage.toFixed(4) : "—"}
                </td>
                <td style={{ ...tdStyle, fontFamily: "monospace" }}>
                  {r.paper_shares != null ? r.paper_shares.toFixed(3) : "—"}
                </td>
                <td style={{ ...tdStyle, fontFamily: "monospace" }}>
                  {r.clob_shares != null ? r.clob_shares.toFixed(3) : "—"}
                </td>
                <td style={{ ...tdStyle, fontFamily: "monospace", color: pnlColor(r.paper_pnl_usd) }}>
                  {usd(r.paper_pnl_usd, true)}
                </td>
                <td style={{ ...tdStyle, fontFamily: "monospace", color: pnlColor(r.clob_pnl_usd) }}>
                  {usd(r.clob_pnl_usd, true)}
                </td>
                <td style={{ ...tdStyle, fontFamily: "monospace", fontWeight: 700, color: pnlColor(r.pnl_gap_usd) }}>
                  {r.pnl_gap_usd != null ? (r.pnl_gap_usd >= 0 ? "+" : "") + usd(r.pnl_gap_usd) : "—"}
                </td>
                <td style={tdStyle}>
                  <GapTypeBadge type={r.gap_type} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function GapTypeBadge({ type }: { type: ExecutionGapRow["gap_type"] }) {
  const map: Record<ExecutionGapRow["gap_type"], { label: string; color: string }> = {
    both: { label: "Paired", color: "var(--ok)" },
    clob_only: { label: "CLOB only", color: "var(--accent-2)" },
    paper_only_clob_rejected: { label: "CLOB rejected", color: "var(--bad)" },
    paper_only: { label: "Paper only", color: "var(--muted)" },
  };
  const { label, color } = map[type];
  return <Badge text={label} color={color} />;
}

function StatChip({ label, value, valueColor, help }: {
  label: string; value: string; valueColor?: string; help?: string;
}) {
  return (
    <div style={{
      background: "var(--card)", border: "1px solid var(--stroke)",
      borderRadius: 8, padding: "8px 12px",
    }} title={help}>
      <div style={{ fontSize: 10, color: "var(--muted)", marginBottom: 2 }}>{label}</div>
      <div style={{ fontWeight: 700, fontSize: 13, color: valueColor }}>{value}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const thStyle: React.CSSProperties = {
  textAlign: "left", padding: "8px 12px",
  color: "var(--muted)", fontWeight: 600,
  whiteSpace: "nowrap",
};

const tdStyle: React.CSSProperties = {
  padding: "7px 12px", verticalAlign: "middle",
};
