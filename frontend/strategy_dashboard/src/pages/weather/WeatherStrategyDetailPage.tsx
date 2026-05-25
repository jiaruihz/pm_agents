/**
 * WeatherStrategyDetailPage — per-strategy equity curve, analytics & positions.
 * Route: /weather/strategies/:configId
 */
import { useEffect, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Bar, BarChart,
} from "recharts";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import type {
  StrategyRow, EquityPoint, StrategyAnalytics,
  AnalyticsDimension, PositionRow, FunnelRow, PendingOrderRow, StrategyOrderRow, MarkToMarketSummary,
} from "../../data/weather-types";

type StrategyState = "live" | "paper" | "explore" | "all";

// ── helpers ───────────────────────────────────────────────────────────────────

function pct(v: number | null | undefined, d = 1) {
  return v == null ? "—" : `${(v * 100).toFixed(d)}%`;
}
function usd(v: number | null | undefined, d = 2) {
  if (v == null) return "—";
  const sign = v >= 0 ? "+" : "-";
  return `${sign}$${Math.abs(v).toFixed(d)}`;
}
function fmtDate(s: string | null | undefined) { return s ? s.slice(0, 10) : "—"; }
// Detect and suppress incorrect 1970 timestamps from ingest bug (CLOB fill sync stored ms as s)
function fmtTs(s: string | null | undefined) {
  if (!s) return "—";
  if (s.startsWith("1970-")) return "—";  // ingest bug: unix_ms treated as unix_s
  return s.slice(0, 16).replace("T", " ");
}

function dimWinRate(dim: AnalyticsDimension) {
  return dim.settled > 0 ? pct(dim.wins / dim.settled) : "—";
}
function dimRoi(dim: AnalyticsDimension) {
  return (dim.capital > 0 && dim.settled > 0) ? pct(dim.pnl / dim.capital, 2) : "—";
}

function shortName(name: string): string {
  const stripped = name.replace(/^.*?_mid_price_core_v\d+_/, "")
    .replace(/^.*?_maker_queue_v\d+_/, "");
  return (stripped || name).replace(/_/g, " ");
}

function pnlColor(v: number | null, hasSettled: boolean) {
  if (!hasSettled || v == null) return "var(--muted)";
  return v >= 0 ? "var(--ok)" : "var(--bad)";
}

// Probability-implied fair value for an order
function fairValue(row: PositionRow): number | null {
  if (row.model_p_yes == null) return null;
  return row.order_side === "BUY_YES" ? row.model_p_yes : 1 - row.model_p_yes;
}

// Unrealized edge: fair_value - fill_price  (positive = expected profit)
function unrealizedEdge(row: PositionRow): number | null {
  const fv = fairValue(row);
  if (fv == null) return null;
  return fv - row.filled_price;
}

// ── component ─────────────────────────────────────────────────────────────────

const DIMENSIONS = [
  { key: "by_side",            label: "Order Side" },
  { key: "by_city",            label: "City" },
  { key: "by_bracket",         label: "Bracket" },
  { key: "by_model",           label: "Model" },
  { key: "by_forecast_source", label: "Forecast" },
] as const;

type DimKey = typeof DIMENSIONS[number]["key"];
type VenueStats = {
  venue: "paper" | "polymarket_clob";
  label: string;
  orders: number;
  fills: number;
  settled: number;
  unsettled: number;
  errors: number;
  noFill: number;
  wins: number;
  pnl: number;
  capital: number;
  rows: StrategyOrderRow[];
};

type DailyLedgerRow = {
  date: string;
  paper: VenueStats;
  clob: VenueStats;
  gapPnl: number | null;
  bothFilled: number;
  missedCount: number;
  missedPnl: number;
  fillDelta: number;
};

function buildVenueStats(rows: StrategyOrderRow[], venue: "paper" | "polymarket_clob", label: string): VenueStats {
  const vr = rows.filter(r => r.venue === venue);
  const filled = vr.filter(r => r.fill_id != null);
  const settled = filled.filter(r => r.final_price != null);
  const pnl = settled.reduce((s, r) => s + (r.pnl_usd ?? 0), 0);
  const capital = filled.reduce((s, r) => s + ((r.filled_shares ?? r.order_shares) * (r.filled_price ?? r.entry_price)), 0);
  return {
    venue,
    label,
    orders: vr.length,
    fills: filled.length,
    settled: settled.length,
    unsettled: filled.length - settled.length,
    errors: vr.filter(r => r.order_status === "error").length,
    noFill: vr.filter(r => r.order_status === "submitted" && r.fill_id == null).length,
    wins: settled.filter(r => (r.pnl_usd ?? 0) > 0).length,
    pnl,
    capital,
    rows: vr,
  };
}

function buildDailyLedger(orders: StrategyOrderRow[]): DailyLedgerRow[] {
  const byDate = new Map<string, StrategyOrderRow[]>();
  orders.forEach((row) => {
    const key = row.target_date || "unknown";
    byDate.set(key, [...(byDate.get(key) ?? []), row]);
  });
  return [...byDate.entries()]
    .sort(([a], [b]) => b.localeCompare(a))
    .map(([date, rows]) => {
      const paper = buildVenueStats(rows, "paper", "Paper");
      const clob = buildVenueStats(rows, "polymarket_clob", "CLOB");
      const gapPnl = paper.settled > 0 || clob.settled > 0 ? clob.pnl - paper.pnl : null;
      const byPlan = new Map<string, StrategyOrderRow[]>();
      rows.forEach((row) => byPlan.set(row.plan_id, [...(byPlan.get(row.plan_id) ?? []), row]));
      let bothFilled = 0;
      let missedCount = 0;
      let missedPnl = 0;
      let fillDelta = 0;
      byPlan.forEach((planRows) => {
        const paperRow = planRows.find(r => r.venue === "paper");
        const clobRow = planRows.find(r => r.venue === "polymarket_clob");
        if (!paperRow || paperRow.pnl_usd == null) return;
        if (clobRow?.pnl_usd != null) {
          bothFilled += 1;
          fillDelta += clobRow.pnl_usd - paperRow.pnl_usd;
        } else if (clobRow && clobRow.fill_id == null) {
          missedCount += 1;
          missedPnl += paperRow.pnl_usd;
        }
      });
      return { date, paper, clob, gapPnl, bothFilled, missedCount, missedPnl, fillDelta };
    });
}

export function WeatherStrategyDetailPage() {
  const { configId = "" } = useParams<{ configId: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const stateParam = searchParams.get("state");
  const stateFilter: StrategyState =
    stateParam === "paper" || stateParam === "explore" || stateParam === "all"
      ? stateParam
      : "live";
  const [strategy, setStrategy]         = useState<StrategyRow | null>(null);
  const [equity, setEquity]             = useState<EquityPoint[]>([]);
  const [analytics, setAnalytics]       = useState<StrategyAnalytics | null>(null);
  const [positions, setPositions]       = useState<PositionRow[]>([]);
  const [funnel, setFunnel]             = useState<FunnelRow[]>([]);
  const [pendingOrders, setPendingOrders] = useState<PendingOrderRow[]>([]);
  const [orders, setOrders]             = useState<StrategyOrderRow[]>([]);
  const [markToMarket, setMarkToMarket] = useState<MarkToMarketSummary | null>(null);
  const [expandedDays, setExpandedDays] = useState<Record<string, boolean>>({});
  const [tab, setTab]                   = useState<DimKey>("by_side");
  const [loading, setLoading]           = useState(true);
  const [markLoading, setMarkLoading]   = useState(false);
  const [error, setError]               = useState<string | null>(null);
  const [markError, setMarkError]       = useState<string | null>(null);

  useEffect(() => {
    if (!configId) return;
    setLoading(true);
    setError(null);
    Promise.all([
      weatherApi.getStrategy(configId, { state: stateFilter }),
      weatherApi.getStrategyEquity(configId, { state: stateFilter }),
      weatherApi.getStrategyAnalytics(configId, { state: stateFilter }),
      weatherApi.getStrategyPositions(configId, { state: stateFilter }),
      weatherApi.getStrategyOrders(configId, { state: stateFilter, limit: 1000 }),
      weatherApi.getStrategyFunnel(configId),
      weatherApi.getStrategyPendingOrders(configId),
    ])
      .then(([s, eq, an, pos, ord, fn, po]) => {
        setStrategy(s);
        setEquity(eq);
        setAnalytics(an);
        setPositions(pos);
        setOrders(ord);
        setFunnel(fn);
        setPendingOrders(po);
        setMarkToMarket(null);
        setMarkError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [configId, stateFilter]);

  const refreshMarkToMarket = () => {
    if (!configId) return;
    setMarkLoading(true);
    setMarkError(null);
    weatherApi.getStrategyMarkToMarket(configId, { state: stateFilter })
      .then(setMarkToMarket)
      .catch((e: Error) => setMarkError(e.message))
      .finally(() => setMarkLoading(false));
  };

  const p = strategy?.params ?? {};
  const liveEnabled  = p.live_enabled  as boolean | undefined;
  const paperEnabled = p.paper_enabled as boolean | undefined;
  const accent = liveEnabled ? "var(--ok)" : paperEnabled ? "var(--accent-2)" : "var(--muted)";

  const settledPnl    = strategy?.total_pnl_usd ?? 0;
  const hasSettled    = (strategy?.settled_trades ?? 0) > 0;
  const settledColor  = pnlColor(settledPnl, hasSettled);

  const openPositions   = positions.filter(r => r.final_price == null);
  const closedPositions = positions.filter(r => r.final_price != null);

  // Chart data: show all equity points (settled ones have real PnL, others show 0)
  const chartData = equity.filter(e => e.settled > 0);

  // Total unrealized edge on open positions
  const totalOpenCapital = openPositions.reduce((s, r) => s + r.filled_shares * r.filled_price, 0);
  const totalEdge = openPositions.reduce((s, r) => {
    const e = unrealizedEdge(r);
    return s + (e != null ? e * r.filled_shares : 0);
  }, 0);
  const mtmPnl = markToMarket?.unrealized_pnl_usd ?? null;
  const mtmPnlColor = mtmPnl == null ? "var(--muted)" : mtmPnl >= 0 ? "var(--ok)" : "var(--bad)";
  const dailyLedger = useMemo(() => buildDailyLedger(orders), [orders]);
  const mtmByFill = useMemo(
    () => new Map((markToMarket?.positions ?? []).map(row => [row.fill_id, row])),
    [markToMarket],
  );

  useEffect(() => {
    if (dailyLedger.length === 0) return;
    setExpandedDays((prev) => {
      if (Object.keys(prev).length > 0) return prev;
      return Object.fromEntries(dailyLedger.map(day => [day.date, true]));
    });
  }, [dailyLedger]);

  return (
    <PageFrame
      title={strategy ? shortName(strategy.name) : configId.slice(-12)}
      desc="Strategy detail · 策略详情"
    >
      <>
        {/* ── Back ── */}
        <div style={{ marginBottom: 16 }}>
          <Link to={`/weather/strategies`} style={{ color: "var(--muted)", textDecoration: "none", fontSize: 13 }}>
            ← All strategies
          </Link>
        </div>

        {error && <div style={{ color: "var(--bad)", padding: "10px 14px", background: "rgba(255,50,50,.08)", borderRadius: 8, marginBottom: 12 }}>{error}</div>}
        {loading && !error && <div style={{ color: "var(--muted)", padding: 40, textAlign: "center" }}>Loading…</div>}

        {strategy && (
          <>
            {/* ── Header ── */}
            <div style={{ display: "flex", gap: 16, alignItems: "flex-start", marginBottom: 24, flexWrap: "wrap" }}>
              <div style={{ flex: 1, minWidth: 240 }}>
                <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, lineHeight: 1.2 }}>
                  {shortName(strategy.name)}
                </h1>
                <div style={{ fontSize: 11, color: "var(--muted)", fontFamily: "monospace", marginTop: 4 }}>
                  {strategy.config_id}
                </div>
                <div style={{ display: "flex", gap: 6, marginTop: 8, flexWrap: "wrap" }}>
                  {liveEnabled  && <Badge label="LIVE"     color="var(--ok)" />}
                  {paperEnabled && <Badge label="PAPER"    color="var(--accent-2)" />}
                  {!liveEnabled && !paperEnabled && <Badge label="INACTIVE" color="var(--muted)" />}
                  {strategy.execution_policy && (
                    <Badge label={strategy.execution_policy} color="rgba(128,128,128,0.6)" />
                  )}
                </div>
                <div style={{ marginTop: 10, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                  <button
                    type="button"
                    onClick={refreshMarkToMarket}
                    disabled={markLoading || !(stateFilter === "live" || stateFilter === "all")}
                    style={refreshButtonStyle}
                  >
                    {markLoading ? "Refreshing mark…" : "Refresh Mark PnL"}
                  </button>
                  <span style={{ fontSize: 11, color: "var(--muted)" }}>
                    {markToMarket?.as_of_utc ? `Marked ${fmtTs(markToMarket.as_of_utc)}` : "Live CLOB mark"}
                  </span>
                </div>
                {markError && <div style={{ color: "var(--bad)", fontSize: 12, marginTop: 6 }}>{markError}</div>}
                <div style={segmentedStyle}>
                  {(["live", "paper", "explore", "all"] as StrategyState[]).map((v) => (
                    <button
                      key={v}
                      type="button"
                      onClick={() => setSearchParams({ state: v })}
                      style={v === stateFilter ? segmentedButtonActiveStyle : segmentedButtonStyle}
                    >
                      {v.toUpperCase()}
                    </button>
                  ))}
                </div>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3, auto)", gap: "6px 24px" }}>
                <ParamCell label="Notional" value={p.max_order_notional != null ? `$${p.max_order_notional}` : "—"} />
                <ParamCell label="Entry Window" value={(p.entry_price_window as string) || "—"} />
                <ParamCell label="Min Edge" value={p.min_edge != null ? pct(p.min_edge as number, 0) : "—"} />
                <ParamCell label="Sizing Mode" value={(p.sizing_mode as string) || "—"} />
                <ParamCell label="City Pool" value={(p.city_pool as string) || "—"} />
                <ParamCell label="Latest Run" value={fmtDate(strategy.latest_run_at)} />
              </div>
            </div>

            {/* ── KPI strip ── */}
            <div style={kpiStripStyle}>
              <KPI label="Runs" value={String(strategy.num_runs)} />
              <KpiDivider />
              <KPI label="Trades" value={strategy.total_trades > 0 ? String(strategy.total_trades) : "—"} />
              <KPI label="Settled" value={strategy.settled_trades > 0 ? String(strategy.settled_trades) : "—"} />
              <KPI label="Open" value={openPositions.length > 0 ? String(openPositions.length) : "—"} color="var(--accent-2)" />
              <KpiDivider />
              <KPI label="Win Rate" value={pct(strategy.win_rate)} />
              <KPI label="PnL" value={hasSettled ? usd(strategy.total_pnl_usd) : "—"} color={settledColor} />
              <KPI label="ROI" value={hasSettled ? pct(strategy.roi, 2) : "—"} color={settledColor} />
              <KpiDivider />
              <KPI label="Capital" value={strategy.capital_deployed_usd > 0 ? `$${strategy.capital_deployed_usd.toFixed(0)}` : "—"} />
              <KPI label="Open Capital" value={totalOpenCapital > 0 ? `$${totalOpenCapital.toFixed(1)}` : "—"} color="var(--accent-2)" />
              <KPI label="Unreal. Edge" value={openPositions.length > 0 ? usd(totalEdge) : "—"} color={totalEdge >= 0 ? "var(--ok)" : "var(--bad)"} />
              <KPI
                label="Mark PnL"
                value={markToMarket && markToMarket.marked_positions > 0 ? usd(markToMarket.unrealized_pnl_usd) : "—"}
                color={mtmPnlColor}
              />
            </div>

            {/* ── Execution Funnel ── */}
            {funnel.length > 0 && <ExecutionFunnelSection funnel={funnel} pendingOrders={pendingOrders} />}

            {dailyLedger.length > 0 && <DailyPnlComparisonTable days={dailyLedger} />}

            <DailyLedgerSection
              days={dailyLedger}
              expandedDays={expandedDays}
              onToggle={(date) => setExpandedDays(prev => ({ ...prev, [date]: !prev[date] }))}
            />

            {/* ── Equity Curve ── */}
            <SectionHeader title="Equity Curve" subtitle="累计盈亏曲线（已结算日期）" />
            {chartData.length === 0 ? (
              <Card style={{ padding: "32px 24px", textAlign: "center", color: "var(--muted)", marginBottom: 24 }}>
                {openPositions.length > 0
                  ? `${openPositions.length} open positions waiting for settlement · 等待结算数据同步`
                  : "No settled trades yet · 暂无已结算交易"}
              </Card>
            ) : (
              <Card style={{ paddingTop: 20, paddingRight: 8, paddingBottom: 12, paddingLeft: 4, marginBottom: 24 }}>
                <ResponsiveContainer width="100%" height={220}>
                  <AreaChart data={chartData} margin={{ left: 20, right: 20, top: 4, bottom: 0 }}>
                    <defs>
                      <linearGradient id="grad" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%"  stopColor={accent} stopOpacity={0.3} />
                        <stop offset="95%" stopColor={accent} stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--stroke)" />
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 11, fill: "var(--muted)" }}
                      tickLine={false}
                      tickFormatter={d => d.slice(5)} // MM-DD
                    />
                    <YAxis
                      tickFormatter={v => `$${(v as number).toFixed(0)}`}
                      tick={{ fontSize: 11, fill: "var(--muted)" }}
                      tickLine={false} axisLine={false}
                      width={54}
                    />
                    <ReferenceLine y={0} stroke="var(--stroke)" strokeWidth={1.5} />
                    <Tooltip
                      contentStyle={{ background: "var(--card)", border: "1px solid var(--stroke)", borderRadius: 8, fontSize: 12 }}
                      formatter={(v: unknown) => [`$${(v as number).toFixed(2)}`, "Cumulative PnL"]}
                      labelFormatter={(l: unknown) => `Date: ${l}`}
                      labelStyle={{ color: "var(--muted)" }}
                    />
                    <Area
                      type="monotone" dataKey="cumulative_pnl"
                      stroke={accent} strokeWidth={2}
                      fill="url(#grad)"
                      dot={{ r: 3, fill: accent, strokeWidth: 0 }}
                      activeDot={{ r: 5 }}
                    />
                  </AreaChart>
                </ResponsiveContainer>
                {/* Daily PnL bars */}
                <ResponsiveContainer width="100%" height={80}>
                  <BarChart data={chartData} margin={{ left: 20, right: 20, top: 8, bottom: 0 }}>
                    <XAxis dataKey="date" hide />
                    <YAxis tickFormatter={v => `$${(v as number).toFixed(0)}`} tick={{ fontSize: 10, fill: "var(--muted)" }} tickLine={false} axisLine={false} width={54} />
                    <ReferenceLine y={0} stroke="var(--stroke)" />
                    <Tooltip
                      contentStyle={{ background: "var(--card)", border: "1px solid var(--stroke)", borderRadius: 8, fontSize: 12 }}
                      formatter={(v: unknown) => [`$${(v as number).toFixed(2)}`, "Daily PnL"]}
                      labelFormatter={(l: unknown) => `Date: ${l}`}
                    />
                    <Bar dataKey="pnl" fill={accent} opacity={0.7} radius={[2, 2, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </Card>
            )}

            {/* ── Open Positions ── */}
            {openPositions.length > 0 && (
              <>
                <SectionHeader
                  title={`Open Positions (${openPositions.length})`}
                  subtitle="待结算持仓 · awaiting settlement"
                />
                <Card style={{ marginBottom: 24, padding: 0 }}>
                  <div style={{ overflowX: "auto" }}>
                    <table style={tableStyle}>
                      <thead>
                        <tr>
                          {["Date", "City", "Bracket", "Side", "Fill Price", "Mkt Bid", "Mark PnL", "Fair Value", "Edge", "Shares", "Cost", "Time", "Venue"].map(h => (
                            <th key={h} style={thStyle}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {openPositions.map(row => {
                          const fv = fairValue(row);
                          const edge = unrealizedEdge(row);
                          const edgeColor = edge == null ? "var(--muted)" : edge >= 0 ? "var(--ok)" : "var(--bad)";
                          const mark = mtmByFill.get(row.fill_id);
                          const markPnl = mark?.unrealized_pnl_usd ?? null;
                          const markColor = markPnl == null ? "var(--muted)" : markPnl >= 0 ? "var(--ok)" : "var(--bad)";
                          return (
                            <tr key={row.fill_id} style={{ borderBottom: "1px solid var(--stroke)" }}>
                              <td style={tdStyle}>{row.target_date}</td>
                              <td style={{ ...tdStyle, fontWeight: 600 }}>{row.city}</td>
                              <td style={tdStyle}>{row.bracket}</td>
                              <td style={tdStyle}>
                                <span style={{ color: row.order_side === "BUY_NO" ? "var(--ok)" : "var(--accent-2)", fontWeight: 600, fontSize: 12 }}>
                                  {row.order_side}
                                </span>
                              </td>
                              <td style={{ ...tdStyle, textAlign: "right", fontFamily: "monospace" }}>
                                {(row.filled_price ?? 0).toFixed(3)}
                              </td>
                              <td style={{ ...tdStyle, textAlign: "right", fontFamily: "monospace", color: mark?.mark_price ? "inherit" : "var(--muted)" }}>
                                {mark?.mark_price != null ? mark.mark_price.toFixed(3) : "—"}
                              </td>
                              <td style={{ ...tdStyle, textAlign: "right", color: markColor, fontWeight: 700 }}>
                                {markPnl != null ? usd(markPnl) : "—"}
                              </td>
                              <td style={{ ...tdStyle, textAlign: "right", fontFamily: "monospace", color: "var(--muted)" }}>
                                {fv != null ? fv.toFixed(3) : "—"}
                              </td>
                              <td style={{ ...tdStyle, textAlign: "right", color: edgeColor, fontWeight: 600 }}>
                                {edge != null ? `${edge >= 0 ? "+" : ""}${(edge * 100).toFixed(1)}¢` : "—"}
                              </td>
                              <td style={{ ...tdStyle, textAlign: "right" }}>
                                {(row.filled_shares ?? 0).toFixed(2)}
                              </td>
                              <td style={{ ...tdStyle, textAlign: "right" }}>
                                ${((row.filled_shares ?? 0) * (row.filled_price ?? 0)).toFixed(2)}
                              </td>
                              <td style={{ ...tdStyle, fontSize: 11, color: "var(--muted)" }}>
                                {fmtTs(row.filled_at_utc)}
                              </td>
                              <td style={{ ...tdStyle, fontSize: 11, color: "var(--muted)" }}>
                                {row.venue}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </Card>
              </>
            )}

            {/* ── Closed Positions ── */}
            {closedPositions.length > 0 && (
              <>
                <SectionHeader
                  title={`Settled Positions (${closedPositions.length})`}
                  subtitle="已结算持仓"
                />
                <Card style={{ marginBottom: 24, padding: 0 }}>
                  <div style={{ overflowX: "auto" }}>
                    <table style={tableStyle}>
                      <thead>
                        <tr>
                          {["Date", "City", "Bracket", "Side", "Fill Price", "Final Price", "PnL", "Shares", "Cost"].map(h => (
                            <th key={h} style={thStyle}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {closedPositions.map(row => {
                          const color = row.pnl_usd == null ? "var(--muted)" : row.pnl_usd >= 0 ? "var(--ok)" : "var(--bad)";
                          return (
                            <tr key={row.fill_id} style={{ borderBottom: "1px solid var(--stroke)" }}>
                              <td style={tdStyle}>{row.target_date}</td>
                              <td style={{ ...tdStyle, fontWeight: 600 }}>{row.city}</td>
                              <td style={tdStyle}>{row.bracket}</td>
                              <td style={tdStyle}>
                                <span style={{ color: row.order_side === "BUY_NO" ? "var(--ok)" : "var(--accent-2)", fontWeight: 600, fontSize: 12 }}>
                                  {row.order_side}
                                </span>
                              </td>
                              <td style={{ ...tdStyle, textAlign: "right", fontFamily: "monospace" }}>
                                {(row.filled_price ?? 0).toFixed(3)}
                              </td>
                              <td style={{ ...tdStyle, textAlign: "right", fontFamily: "monospace", color: "var(--muted)" }}>
                                {row.final_price != null ? row.final_price.toFixed(2) : "—"}
                              </td>
                              <td style={{ ...tdStyle, textAlign: "right", color, fontWeight: 600 }}>
                                {usd(row.pnl_usd)}
                              </td>
                              <td style={{ ...tdStyle, textAlign: "right" }}>
                                {(row.filled_shares ?? 0).toFixed(2)}
                              </td>
                              <td style={{ ...tdStyle, textAlign: "right" }}>
                                ${((row.filled_shares ?? 0) * (row.filled_price ?? 0)).toFixed(2)}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </Card>
              </>
            )}

            {/* ── Analytics ── */}
            <SectionHeader title="P&L Breakdown" subtitle="盈亏归因分析" />
            <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
              {DIMENSIONS.map(d => (
                <button
                  key={d.key}
                  onClick={() => setTab(d.key)}
                  style={{
                    padding: "6px 14px", borderRadius: 8, fontSize: 12, cursor: "pointer",
                    border: `1px solid ${tab === d.key ? accent : "var(--stroke)"}`,
                    background: tab === d.key ? `${accent}22` : "var(--card)",
                    color: tab === d.key ? accent : "var(--muted)",
                    fontWeight: tab === d.key ? 700 : 400,
                  }}
                >
                  {d.label}
                </button>
              ))}
            </div>

            {analytics && (analytics[tab] as AnalyticsDimension[]).length === 0 ? (
              <Card style={{ padding: "32px 24px", textAlign: "center", color: "var(--muted)", marginBottom: 24 }}>
                No settled trades in this dimension · 暂无已结算数据
              </Card>
            ) : analytics ? (
              <Card style={{ padding: 0, marginBottom: 24 }}>
                <div style={{ overflowX: "auto" }}>
                  <table style={tableStyle}>
                    <thead>
                      <tr>
                        {["Dimension", "Trades", "Settled", "Wins", "Win%", "Capital", "PnL", "ROI"].map(h => (
                          <th key={h} style={thStyle}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {(analytics[tab] as AnalyticsDimension[]).map((row) => {
                        const color = row.settled > 0
                          ? row.pnl >= 0 ? "var(--ok)" : "var(--bad)"
                          : "var(--muted)";
                        return (
                          <tr key={row.dimension} style={{ borderBottom: "1px solid var(--stroke)" }}>
                            <td style={{ ...tdStyle, fontWeight: 600 }}>{row.dimension}</td>
                            <td style={{ ...tdStyle, textAlign: "right" }}>{row.trades}</td>
                            <td style={{ ...tdStyle, textAlign: "right" }}>{row.settled}</td>
                            <td style={{ ...tdStyle, textAlign: "right" }}>{row.wins}</td>
                            <td style={{ ...tdStyle, textAlign: "right" }}>{dimWinRate(row)}</td>
                            <td style={{ ...tdStyle, textAlign: "right" }}>${(row.capital ?? 0).toFixed(2)}</td>
                            <td style={{ ...tdStyle, textAlign: "right", color, fontWeight: 600 }}>
                              {row.settled > 0 ? usd(row.pnl) : "—"}
                            </td>
                            <td style={{ ...tdStyle, textAlign: "right", color }}>{dimRoi(row)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </Card>
            ) : null}

            {/* ── Footer links ── */}
            <div style={{ display: "flex", gap: 20, fontSize: 13, paddingTop: 4, paddingBottom: 24 }}>
              <Link to={`/weather/runs?config_id=${strategy.config_id}`} style={linkStyle}>View all runs →</Link>
              <Link to={`/weather/compare?run_ids=${strategy.config_id}`} style={linkStyle}>Compare →</Link>
            </div>
          </>
        )}
      </>
    </PageFrame>
  );
}

// ── Daily P&L Comparison Table ────────────────────────────────────────────────

function DailyPnlComparisonTable({ days }: { days: DailyLedgerRow[] }) {
  if (days.length === 0) return null;

  const settled = days.filter(d => d.paper.settled > 0 || d.clob.settled > 0);
  if (settled.length === 0) return null;

  const totPaper    = settled.reduce((s, d) => s + (d.paper.settled > 0 ? d.paper.pnl : 0), 0);
  const totClob     = settled.reduce((s, d) => s + (d.clob.settled  > 0 ? d.clob.pnl  : 0), 0);
  const totGap      = settled.reduce((s, d) => s + (d.gapPnl ?? 0), 0);
  const totMissed   = settled.reduce((s, d) => s + d.missedPnl, 0);
  const totExecDiff = settled.reduce((s, d) => s + d.fillDelta, 0);

  const numCol: React.CSSProperties = { ...tdStyle, textAlign: "right", fontFamily: "monospace", fontVariantNumeric: "tabular-nums" };
  const numColBold: React.CSSProperties = { ...numCol, fontWeight: 700 };
  const thR: React.CSSProperties = { ...thStyle, textAlign: "right" };

  function pnlCell(v: number, show: boolean): React.ReactNode {
    if (!show) return <span style={{ color: "var(--muted)" }}>—</span>;
    return <span style={{ color: v >= 0 ? "var(--ok)" : "var(--bad)", fontWeight: 600 }}>{usd(v)}</span>;
  }

  function gapCell(v: number | null): React.ReactNode {
    if (v == null) return <span style={{ color: "var(--muted)" }}>—</span>;
    return <span style={{ color: v >= 0 ? "var(--ok)" : "var(--bad)", fontWeight: 700 }}>{usd(v)}</span>;
  }

  return (
    <>
      <SectionHeader title="Daily P&L Comparison" subtitle="每日 paper vs CLOB 执行对比" />
      <Card style={{ padding: 0, marginBottom: 24 }}>
        <div style={{ overflowX: "auto" }}>
          <table style={tableStyle}>
            <thead>
              <tr>
                <th style={thStyle}>Date</th>
                <th style={thR}>Paper PnL</th>
                <th style={thR}>CLOB PnL</th>
                <th style={thR}>Gap</th>
                <th style={thR}>Missed Gap</th>
                <th style={thR}>Exec Diff</th>
              </tr>
            </thead>
            <tbody>
              {settled.map(d => (
                <tr key={d.date} style={{ borderBottom: "1px solid var(--stroke)" }}>
                  <td style={{ ...tdStyle, fontWeight: 700, fontFamily: "monospace" }}>{d.date}</td>
                  <td style={numCol}>{pnlCell(d.paper.pnl, d.paper.settled > 0)}</td>
                  <td style={numCol}>{pnlCell(d.clob.pnl,  d.clob.settled  > 0)}</td>
                  <td style={numCol}>{gapCell(d.gapPnl)}</td>
                  <td style={numCol}>
                    {d.missedCount > 0
                      ? <span style={{ color: d.missedPnl >= 0 ? "var(--bad)" : "var(--ok)", fontWeight: 600 }}>{usd(d.missedPnl)}</span>
                      : <span style={{ color: "var(--muted)" }}>—</span>}
                  </td>
                  <td style={numCol}>
                    {d.bothFilled > 0
                      ? <span style={{ color: d.fillDelta >= 0 ? "var(--ok)" : "var(--bad)", fontWeight: 600 }}>{usd(d.fillDelta)}</span>
                      : <span style={{ color: "var(--muted)" }}>—</span>}
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr style={{ background: "rgba(128,128,128,0.06)", borderTop: "2px solid var(--stroke)" }}>
                <td style={{ ...tdStyle, fontWeight: 800, fontSize: 12, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>TOTAL</td>
                <td style={numColBold}><span style={{ color: totPaper    >= 0 ? "var(--ok)" : "var(--bad)" }}>{usd(totPaper)}</span></td>
                <td style={numColBold}><span style={{ color: totClob     >= 0 ? "var(--ok)" : "var(--bad)" }}>{usd(totClob)}</span></td>
                <td style={numColBold}><span style={{ color: totGap      >= 0 ? "var(--ok)" : "var(--bad)" }}>{usd(totGap)}</span></td>
                <td style={numColBold}><span style={{ color: totMissed   >= 0 ? "var(--bad)" : "var(--ok)" }}>{usd(totMissed)}</span></td>
                <td style={numColBold}><span style={{ color: totExecDiff >= 0 ? "var(--ok)" : "var(--bad)" }}>{usd(totExecDiff)}</span></td>
              </tr>
            </tfoot>
          </table>
        </div>
      </Card>
    </>
  );
}

// ── Daily Execution Ledger ─────────────────────────────────────────────────────

function DailyLedgerSection({
  days,
  expandedDays,
  onToggle,
}: {
  days: DailyLedgerRow[];
  expandedDays: Record<string, boolean>;
  onToggle: (date: string) => void;
}) {
  return (
    <>
      <SectionHeader
        title={`Daily Execution Ledger (${days.length})`}
        subtitle="按结算目标日拆分 paper vs CLOB"
      />
      {days.length === 0 ? (
        <Card style={{ padding: "28px 24px", textAlign: "center", color: "var(--muted)", marginBottom: 24 }}>
          No orders for this state · 当前状态下没有下单明细
        </Card>
      ) : (
        <div style={dailyLedgerStyle}>
          {days.map(day => (
            <DailyLedgerDay
              key={day.date}
              day={day}
              expanded={Boolean(expandedDays[day.date])}
              onToggle={() => onToggle(day.date)}
            />
          ))}
        </div>
      )}
    </>
  );
}

function DailyLedgerDay({
  day,
  expanded,
  onToggle,
}: {
  day: DailyLedgerRow;
  expanded: boolean;
  onToggle: () => void;
}) {
  const gapColor = day.gapPnl == null ? "var(--muted)" : day.gapPnl >= 0 ? "var(--ok)" : "var(--bad)";
  const hasSettled = day.paper.settled > 0 || day.clob.settled > 0;

  return (
    <Card style={{ padding: 0, overflow: "hidden" }}>
      <button type="button" onClick={onToggle} style={dayHeaderButtonStyle}>
        <div style={{ minWidth: 118 }}>
          <div style={{ fontSize: 15, fontWeight: 800 }}>{day.date}</div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>
            {expanded ? "Hide details ▲" : "Show orders ▼"}
          </div>
        </div>
        <div style={daySummaryGridStyle}>
          <DailyStat label="Paper Orders"    value={`${day.paper.orders} / ${day.paper.fills} filled`} />
          <DailyStat label="CLOB Orders"     value={`${day.clob.orders} / ${day.clob.fills} filled`} />
          <DailyStat label="Paper PnL"
            value={day.paper.settled > 0 ? usd(day.paper.pnl) : "—"}
            color={day.paper.settled > 0 ? (day.paper.pnl >= 0 ? "var(--ok)" : "var(--bad)") : "var(--muted)"} />
          <DailyStat label="CLOB PnL"
            value={day.clob.settled > 0 ? usd(day.clob.pnl) : "—"}
            color={day.clob.settled > 0 ? (day.clob.pnl >= 0 ? "var(--ok)" : "var(--bad)") : "var(--muted)"} />
          <DailyStat label="Execution Gap"
            value={hasSettled && day.gapPnl != null ? usd(day.gapPnl) : "—"}
            color={gapColor} />
          <DailyStat label="Errors / No Fill"
            value={`${day.clob.errors} / ${day.clob.noFill}`}
            color={day.clob.errors || day.clob.noFill ? "var(--bad)" : "var(--muted)"} />
        </div>
        <div style={{ color: "var(--muted)", fontSize: 18, paddingLeft: 8 }}>{expanded ? "−" : "+"}</div>
      </button>
      {expanded && (
        <div style={dayExpandedStyle}>
          <VenuePanel stats={day.paper} />
          <VenuePanel stats={day.clob} />
        </div>
      )}
    </Card>
  );
}

function DailyStat({ label, value, color = "inherit" }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <div style={{ fontSize: 14, fontWeight: 750, color, fontVariantNumeric: "tabular-nums" }}>{value}</div>
      <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>{label}</div>
    </div>
  );
}

function VenuePanel({ stats }: { stats: VenueStats }) {
  const winRate = stats.settled ? stats.wins / stats.settled : null;
  const roi = stats.capital ? stats.pnl / stats.capital : null;
  const tone = stats.venue === "polymarket_clob" ? "var(--ok)" : "var(--accent-2)";
  return (
    <div style={venuePanelStyle}>
      <div style={venueHeaderStyle}>
        <Badge label={stats.label} color={tone} />
        <DailyStat label="Orders / Fills / Settled" value={`${stats.orders} / ${stats.fills} / ${stats.settled}`} />
        <DailyStat label="PnL" value={stats.settled ? usd(stats.pnl) : "—"} color={stats.pnl >= 0 ? "var(--ok)" : "var(--bad)"} />
        <DailyStat label="Win / ROI" value={`${pct(winRate)} / ${pct(roi, 2)}`} />
        <DailyStat label="Unsettled" value={String(stats.unsettled)} color={stats.unsettled ? "var(--accent-2)" : "var(--muted)"} />
        <DailyStat label="Errors / No Fill" value={`${stats.errors} / ${stats.noFill}`} color={stats.errors || stats.noFill ? "var(--bad)" : "var(--muted)"} />
      </div>
      <OrderMiniTable rows={stats.rows} />
    </div>
  );
}

function OrderMiniTable({ rows }: { rows: StrategyOrderRow[] }) {
  if (rows.length === 0) {
    return <div style={{ color: "var(--muted)", padding: "14px 0", fontSize: 12 }}>No orders</div>;
  }
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={tableStyle}>
        <thead>
          <tr>
            {["Time", "City", "Bracket", "Side", "Order", "Fill", "Settle", "Shares", "Price", "Cost", "PnL"].map(h => (
              <th key={h} style={thStyle}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(row => {
            const pnl = row.pnl_usd;
            const settlement = row.final_price == null ? "open" : String(row.final_price);
            const filledCost = row.filled_shares != null && row.filled_price != null
              ? row.filled_shares * row.filled_price + (row.fees_usd ?? 0)
              : null;
            return (
              <tr key={`${row.execution_id}:${row.fill_id ?? "none"}`} style={{ borderBottom: "1px solid var(--stroke)" }}>
                <td style={{ ...tdStyle, color: "var(--muted)", whiteSpace: "nowrap" }}>{row.placed_at_utc?.slice(5, 16).replace("T", " ") ?? "—"}</td>
                <td style={{ ...tdStyle, fontWeight: 650 }}>{row.city}</td>
                <td style={tdStyle}>{row.bracket}</td>
                <td style={tdStyle}>{row.order_side.replace("BUY_", "")}</td>
                <td style={tdStyle}>
                  <div>{row.order_status}</div>
                  <div style={{ fontSize: 10, color: "var(--muted)", fontFamily: "monospace" }}>{row.order_id?.slice(0, 12) ?? row.execution_id.slice(0, 12)}</div>
                </td>
                <td style={tdStyle}>{row.fill_status ?? "unfilled"}</td>
                <td style={tdStyle}>{settlement}</td>
                <td style={{ ...tdStyle, textAlign: "right" }}>{(row.filled_shares ?? row.order_shares ?? 0).toFixed(2)}</td>
                <td style={{ ...tdStyle, textAlign: "right" }}>{(row.filled_price ?? row.entry_price ?? 0).toFixed(3)}</td>
                <td style={{ ...tdStyle, textAlign: "right" }}>${(filledCost ?? row.order_cost_usd ?? 0).toFixed(2)}</td>
                <td style={{ ...tdStyle, textAlign: "right", color: pnl == null ? "var(--muted)" : pnl >= 0 ? "var(--ok)" : "var(--bad)", fontWeight: 650 }}>
                  {pnl == null ? "—" : usd(pnl)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Execution Funnel Section ──────────────────────────────────────────────────

function fillRateColor(rate: number | null): string {
  if (rate == null) return "var(--muted)";
  if (rate >= 0.8)  return "var(--ok)";
  if (rate >= 0.4)  return "#f5a623";   // amber
  return "var(--bad)";
}

function ExecutionFunnelSection({
  funnel, pendingOrders,
}: { funnel: FunnelRow[]; pendingOrders: PendingOrderRow[] }) {
  // Aggregate totals across all days
  const totals = funnel.reduce(
    (acc, r) => ({
      signals:  acc.signals  + r.signals_evaluated,
      executed: acc.executed + r.plans_executed,
      skipped:  acc.skipped  + r.plans_skipped,
      placed:   acc.placed   + r.orders_placed,
      filled:   acc.filled   + r.orders_filled,
      pending:  acc.pending  + r.orders_pending,
      filledCap: acc.filledCap + (r.filled_capital_usd ?? 0),
      pendingCap: acc.pendingCap + (r.pending_capital_usd ?? 0),
    }),
    { signals: 0, executed: 0, skipped: 0, placed: 0, filled: 0, pending: 0, filledCap: 0, pendingCap: 0 },
  );
  const overallFillRate = totals.placed > 0 ? totals.filled / totals.placed : null;

  return (
    <>
      <SectionHeader
        title={`Execution Funnel${pendingOrders.length > 0 ? ` · ${pendingOrders.length} pending` : ""}`}
        subtitle="挂单成交漏斗分析"
      />

      {/* Summary KPI row */}
      <Card style={{ marginBottom: 16, padding: "12px 20px" }}>
        <div style={{ display: "flex", gap: 0, alignItems: "center" }}>
          <FunnelKpi label="Signals" value={String(totals.signals)} />
          <FunnelArrow />
          <FunnelKpi label="Plans" value={String(totals.executed)} note={totals.skipped > 0 ? `${totals.skipped} skipped` : undefined} />
          <FunnelArrow />
          <FunnelKpi label="Placed" value={String(totals.placed)} note={`$${totals.filledCap.toFixed(0) === "0" ? totals.pendingCap.toFixed(0) : totals.filledCap.toFixed(0)} cap`} />
          <FunnelArrow />
          <FunnelKpi
            label="Filled"
            value={String(totals.filled)}
            note={overallFillRate != null ? `${(overallFillRate * 100).toFixed(0)}% fill rate` : undefined}
            valueColor={fillRateColor(overallFillRate)}
          />
          {totals.pending > 0 && (
            <>
              <div style={{ width: 1, background: "var(--stroke)", alignSelf: "stretch", margin: "0 16px" }} />
              <FunnelKpi
                label="Pending"
                value={String(totals.pending)}
                note={`$${totals.pendingCap.toFixed(0)} reserved`}
                valueColor="var(--accent-2)"
              />
            </>
          )}
        </div>
      </Card>

      {/* Daily breakdown table */}
      <Card style={{ padding: 0, marginBottom: 24 }}>
        <div style={{ overflowX: "auto" }}>
          <table style={tableStyle}>
            <thead>
              <tr>
                {["Day", "Signals", "Plans", "Skipped", "Placed", "Filled", "Pending", "Fill Rate", "Avg Limit", "Avg Market", "Discount", "Filled $", "Pending $"].map(h => (
                  <th key={h} style={{ ...thStyle, textAlign: h === "Day" ? "left" : "right" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {funnel.map(row => {
                const fc = fillRateColor(row.fill_rate);
                return (
                  <tr key={row.day} style={{ borderBottom: "1px solid var(--stroke)" }}>
                    <td style={{ ...tdStyle, fontFamily: "monospace", fontSize: 12 }}>{row.day}</td>
                    <td style={{ ...tdStyle, textAlign: "right" }}>{row.signals_evaluated}</td>
                    <td style={{ ...tdStyle, textAlign: "right" }}>{row.plans_executed}</td>
                    <td style={{ ...tdStyle, textAlign: "right", color: row.plans_skipped > 0 ? "var(--muted)" : "inherit" }}>
                      {row.plans_skipped > 0 ? row.plans_skipped : "—"}
                    </td>
                    <td style={{ ...tdStyle, textAlign: "right" }}>{row.orders_placed}</td>
                    <td style={{ ...tdStyle, textAlign: "right", color: fc, fontWeight: 600 }}>{row.orders_filled}</td>
                    <td style={{ ...tdStyle, textAlign: "right", color: row.orders_pending > 0 ? "var(--accent-2)" : "var(--muted)" }}>
                      {row.orders_pending > 0 ? row.orders_pending : "—"}
                    </td>
                    <td style={{ ...tdStyle, textAlign: "right", color: fc, fontWeight: 700 }}>
                      {row.fill_rate != null ? `${(row.fill_rate * 100).toFixed(0)}%` : "—"}
                    </td>
                    <td style={{ ...tdStyle, textAlign: "right", fontFamily: "monospace", fontSize: 12 }}>
                      {row.avg_limit_price != null ? row.avg_limit_price.toFixed(3) : "—"}
                    </td>
                    <td style={{ ...tdStyle, textAlign: "right", fontFamily: "monospace", fontSize: 12, color: "var(--muted)" }}>
                      {row.avg_market_price != null ? row.avg_market_price.toFixed(3) : "—"}
                    </td>
                    <td style={{
                      ...tdStyle, textAlign: "right", fontWeight: 600,
                      color: row.limit_discount == null ? "var(--muted)"
                        : row.limit_discount > 0.01 ? "var(--ok)"
                        : row.limit_discount < -0.01 ? "var(--bad)"
                        : "var(--muted)",
                    }}>
                      {row.limit_discount != null
                        ? `${row.limit_discount >= 0 ? "−" : "+"}${Math.abs(row.limit_discount * 100).toFixed(1)}¢`
                        : "—"}
                    </td>
                    <td style={{ ...tdStyle, textAlign: "right", fontSize: 12, color: "var(--muted)" }}>
                      {row.filled_capital_usd != null && row.filled_capital_usd > 0 ? `$${row.filled_capital_usd.toFixed(1)}` : "—"}
                    </td>
                    <td style={{ ...tdStyle, textAlign: "right", fontSize: 12, color: row.pending_capital_usd && row.pending_capital_usd > 0 ? "var(--accent-2)" : "var(--muted)" }}>
                      {row.pending_capital_usd != null && row.pending_capital_usd > 0 ? `$${row.pending_capital_usd.toFixed(1)}` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Pending orders detail */}
      {pendingOrders.length > 0 && (
        <>
          <SectionHeader
            title={`Pending Orders (${pendingOrders.length})`}
            subtitle="在市场中等待成交的挂单"
          />
          <Card style={{ padding: 0, marginBottom: 24 }}>
            <div style={{ overflowX: "auto" }}>
              <table style={tableStyle}>
                <thead>
                  <tr>
                    {["Age (h)", "City", "Target Date", "Bracket", "Side", "Limit Price", "Market Price", "Discount", "Shares", "Cost"].map(h => (
                      <th key={h} style={{ ...thStyle, textAlign: h === "City" || h === "Bracket" || h === "Side" ? "left" : "right" }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {pendingOrders.map(row => {
                    const disc = row.limit_discount ?? 0;
                    const discColor = disc > 0.01 ? "var(--ok)" : disc < -0.01 ? "var(--bad)" : "var(--muted)";
                    const ageColor = (row.hours_pending ?? 0) > 24 ? "var(--bad)" : (row.hours_pending ?? 0) > 6 ? "#f5a623" : "var(--muted)";
                    return (
                      <tr key={row.execution_id} style={{ borderBottom: "1px solid var(--stroke)" }}>
                        <td style={{ ...tdStyle, textAlign: "right", color: ageColor, fontWeight: 600, fontVariantNumeric: "tabular-nums" }}>
                          {row.hours_pending != null ? row.hours_pending.toFixed(1) : "—"}
                        </td>
                        <td style={{ ...tdStyle, fontWeight: 600 }}>{row.city}</td>
                        <td style={{ ...tdStyle, fontFamily: "monospace", fontSize: 12 }}>{row.target_date}</td>
                        <td style={{ ...tdStyle }}>{row.bracket}</td>
                        <td style={{ ...tdStyle }}>
                          <span style={{ color: row.order_side === "BUY_NO" ? "var(--ok)" : "var(--accent-2)", fontWeight: 600, fontSize: 12 }}>
                            {row.order_side}
                          </span>
                        </td>
                        <td style={{ ...tdStyle, textAlign: "right", fontFamily: "monospace" }}>
                          {row.limit_price != null ? row.limit_price.toFixed(3) : "—"}
                        </td>
                        <td style={{ ...tdStyle, textAlign: "right", fontFamily: "monospace", color: "var(--muted)" }}>
                          {row.signal_market_price != null ? row.signal_market_price.toFixed(3) : "—"}
                        </td>
                        <td style={{ ...tdStyle, textAlign: "right", color: discColor, fontWeight: 600 }}>
                          {disc !== 0
                            ? `${disc >= 0 ? "−" : "+"}${Math.abs(disc * 100).toFixed(1)}¢`
                            : <span style={{ color: "var(--bad)", fontWeight: 700 }}>NO DISC</span>}
                        </td>
                        <td style={{ ...tdStyle, textAlign: "right" }}>
                          {row.shares != null ? row.shares.toFixed(2) : "—"}
                        </td>
                        <td style={{ ...tdStyle, textAlign: "right" }}>
                          ${row.cost_usd != null ? row.cost_usd.toFixed(2) : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </>
  );
}

function FunnelKpi({
  label, value, note, valueColor = "inherit",
}: { label: string; value: string; note?: string; valueColor?: string }) {
  return (
    <div style={{ textAlign: "center", flex: 1, minWidth: 72 }}>
      <div style={{ fontSize: 20, fontWeight: 700, color: valueColor, fontVariantNumeric: "tabular-nums" }}>{value}</div>
      <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>{label}</div>
      {note && <div style={{ fontSize: 10, color: "var(--muted)", fontStyle: "italic" }}>{note}</div>}
    </div>
  );
}

function FunnelArrow() {
  return (
    <div style={{ color: "var(--muted)", fontSize: 18, padding: "0 4px", flexShrink: 0 }}>→</div>
  );
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function Badge({ label, color }: { label: string; color: string }) {
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, padding: "2px 8px",
      borderRadius: 4, background: color + "22",
      color, border: `1px solid ${color}55`,
      letterSpacing: "0.05em",
    }}>
      {label}
    </span>
  );
}

function ParamCell({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ fontSize: 10, color: "var(--muted)", textTransform: "uppercase", letterSpacing: "0.05em" }}>{label}</div>
      <div style={{ fontSize: 13, fontWeight: 500 }}>{value}</div>
    </div>
  );
}

function KPI({ label, value, color = "inherit" }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ textAlign: "center", flex: 1, minWidth: 64 }}>
      <div style={{ fontSize: 17, fontWeight: 700, color, fontVariantNumeric: "tabular-nums", lineHeight: 1.2 }}>{value}</div>
      <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 3 }}>{label}</div>
    </div>
  );
}

function KpiDivider() {
  return <div style={{ width: 1, background: "var(--stroke)", alignSelf: "stretch" }} />;
}

function SectionHeader({ title, subtitle }: { title: string; subtitle: string }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 12, paddingLeft: 10, borderLeft: "3px solid var(--accent)" }}>
      <div style={{ fontSize: 15, fontWeight: 700 }}>{title}</div>
      <div style={{ fontSize: 12, color: "var(--muted)" }}>{subtitle}</div>
    </div>
  );
}

function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div style={{
      background: "var(--card)", border: "1px solid var(--stroke)",
      borderRadius: 12, padding: "18px 20px", ...style,
    }}>
      {children}
    </div>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const kpiStripStyle: React.CSSProperties = {
  display: "flex", gap: 0, alignItems: "center",
  background: "var(--card)", border: "1px solid var(--stroke)",
  borderRadius: 12, padding: "14px 20px", marginBottom: 24,
};
const dailyLedgerStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 12,
  marginBottom: 24,
};
const dayHeaderButtonStyle: React.CSSProperties = {
  width: "100%",
  display: "flex",
  alignItems: "center",
  gap: 16,
  padding: "14px 16px",
  background: "transparent",
  border: 0,
  borderBottom: "1px solid var(--stroke)",
  color: "inherit",
  cursor: "pointer",
  textAlign: "left",
};
const daySummaryGridStyle: React.CSSProperties = {
  flex: 1,
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(112px, 1fr))",
  gap: "10px 16px",
};
const dayExpandedStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(360px, 1fr))",
  gap: 14,
  padding: 16,
};
const venuePanelStyle: React.CSSProperties = {
  border: "1px solid var(--stroke)",
  borderRadius: 8,
  padding: "12px 14px",
};
const venueHeaderStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "auto repeat(5, minmax(100px, 1fr))",
  alignItems: "center",
  gap: "10px 14px",
  marginBottom: 10,
};
const segmentedStyle: React.CSSProperties = {
  display: "inline-flex",
  border: "1px solid var(--stroke)",
  borderRadius: 6,
  overflow: "hidden",
  background: "var(--card)",
  marginTop: 12,
};
const segmentedButtonStyle: React.CSSProperties = {
  border: 0,
  borderRight: "1px solid var(--stroke)",
  background: "transparent",
  color: "var(--muted)",
  padding: "7px 10px",
  fontSize: 11,
  fontWeight: 700,
  cursor: "pointer",
};
const segmentedButtonActiveStyle: React.CSSProperties = {
  ...segmentedButtonStyle,
  background: "var(--accent)",
  color: "white",
};
const refreshButtonStyle: React.CSSProperties = {
  border: "1px solid var(--stroke)",
  borderRadius: 6,
  background: "var(--card)",
  color: "inherit",
  padding: "7px 11px",
  fontSize: 12,
  fontWeight: 700,
  cursor: "pointer",
};
const tableStyle: React.CSSProperties = {
  width: "100%", borderCollapse: "collapse", fontSize: 13,
};
const thStyle: React.CSSProperties = {
  textAlign: "left", padding: "10px 14px",
  borderBottom: "2px solid var(--stroke)", color: "var(--muted)",
  fontWeight: 600, fontSize: 12, whiteSpace: "nowrap",
};
const tdStyle: React.CSSProperties = { padding: "9px 14px", verticalAlign: "middle" };
const linkStyle: React.CSSProperties = { color: "var(--accent-2)", textDecoration: "none" };
