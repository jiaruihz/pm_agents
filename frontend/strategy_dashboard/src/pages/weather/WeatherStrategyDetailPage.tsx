/**
 * WeatherStrategyDetailPage — per-strategy equity curve, analytics & positions.
 * Route: /weather/strategies/:configId
 */
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine, Bar, BarChart,
} from "recharts";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import type {
  StrategyRow, EquityPoint, StrategyAnalytics,
  AnalyticsDimension, PositionRow,
} from "../../data/weather-types";

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

export function WeatherStrategyDetailPage() {
  const { configId = "" } = useParams<{ configId: string }>();
  const [strategy, setStrategy]   = useState<StrategyRow | null>(null);
  const [equity, setEquity]       = useState<EquityPoint[]>([]);
  const [analytics, setAnalytics] = useState<StrategyAnalytics | null>(null);
  const [positions, setPositions] = useState<PositionRow[]>([]);
  const [tab, setTab]             = useState<DimKey>("by_side");
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState<string | null>(null);

  useEffect(() => {
    if (!configId) return;
    setLoading(true);
    setError(null);
    Promise.all([
      weatherApi.getStrategy(configId),
      weatherApi.getStrategyEquity(configId),
      weatherApi.getStrategyAnalytics(configId),
      weatherApi.getStrategyPositions(configId),
    ])
      .then(([s, eq, an, pos]) => {
        setStrategy(s);
        setEquity(eq);
        setAnalytics(an);
        setPositions(pos);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [configId]);

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

  return (
    <PageFrame
      title={strategy ? shortName(strategy.name) : configId.slice(-12)}
      desc="Strategy detail · 策略详情"
    >
      <>
        {/* ── Back ── */}
        <div style={{ marginBottom: 16 }}>
          <Link to="/weather/strategies" style={{ color: "var(--muted)", textDecoration: "none", fontSize: 13 }}>
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
            </div>

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
                          {["Date", "City", "Bracket", "Side", "Fill Price", "Fair Value", "Edge", "Shares", "Cost", "Time", "Venue"].map(h => (
                            <th key={h} style={thStyle}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {openPositions.map(row => {
                          const fv = fairValue(row);
                          const edge = unrealizedEdge(row);
                          const edgeColor = edge == null ? "var(--muted)" : edge >= 0 ? "var(--ok)" : "var(--bad)";
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
                                {row.filled_price.toFixed(3)}
                              </td>
                              <td style={{ ...tdStyle, textAlign: "right", fontFamily: "monospace", color: "var(--muted)" }}>
                                {fv != null ? fv.toFixed(3) : "—"}
                              </td>
                              <td style={{ ...tdStyle, textAlign: "right", color: edgeColor, fontWeight: 600 }}>
                                {edge != null ? `${edge >= 0 ? "+" : ""}${(edge * 100).toFixed(1)}¢` : "—"}
                              </td>
                              <td style={{ ...tdStyle, textAlign: "right" }}>
                                {row.filled_shares.toFixed(2)}
                              </td>
                              <td style={{ ...tdStyle, textAlign: "right" }}>
                                ${(row.filled_shares * row.filled_price).toFixed(2)}
                              </td>
                              <td style={{ ...tdStyle, fontSize: 11, color: "var(--muted)" }}>
                                {row.filled_at_utc ? row.filled_at_utc.slice(0, 16).replace("T", " ") : "—"}
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
                                {row.filled_price.toFixed(3)}
                              </td>
                              <td style={{ ...tdStyle, textAlign: "right", fontFamily: "monospace", color: "var(--muted)" }}>
                                {row.final_price != null ? row.final_price.toFixed(2) : "—"}
                              </td>
                              <td style={{ ...tdStyle, textAlign: "right", color, fontWeight: 600 }}>
                                {usd(row.pnl_usd)}
                              </td>
                              <td style={{ ...tdStyle, textAlign: "right" }}>
                                {row.filled_shares.toFixed(2)}
                              </td>
                              <td style={{ ...tdStyle, textAlign: "right" }}>
                                ${(row.filled_shares * row.filled_price).toFixed(2)}
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
                            <td style={{ ...tdStyle, textAlign: "right" }}>${row.capital.toFixed(2)}</td>
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
    <div style={{ display: "flex", alignItems: "baseline", gap: 10, marginBottom: 12 }}>
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
