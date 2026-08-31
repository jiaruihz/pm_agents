import { FormEvent, useEffect, useMemo, useState } from "react";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import type { CapitalEfficiencyPosition, CapitalEfficiencyReport } from "../../data/copy-trade-types";

type SortKey = "value" | "date" | "annualized_desc" | "annualized_asc";

const WALLET_RE = /^0x[a-fA-F0-9]{40}$/;

function fmtUsd(value: unknown, digits = 2): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: Math.abs(n) < 100 ? digits : 0,
  }).format(n);
}

function pct(value: unknown, digits = 1): string {
  const n = Number(value);
  return Number.isFinite(n) ? `${(n * 100).toFixed(digits)}%` : "—";
}

function optionalNumber(value: string, label: string): number | undefined {
  if (!value.trim()) return undefined;
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 0) throw new Error(`${label}必须是大于等于 0 的数字。`);
  return parsed;
}

function statusLabel(status: string): string {
  if (status === "active") return "持有中";
  if (status === "pending_resolution") return "待结算";
  if (status === "unknown_end") return "期限未知";
  if (status === "redeemable") return "可赎回";
  if (status === "dust") return "dust";
  return status;
}

export function CapitalEfficiencyPage(): JSX.Element {
  const initialWallet = new URLSearchParams(window.location.search).get("wallet") || localStorage.getItem("capital-efficiency-wallet") || "";
  const [wallet, setWallet] = useState(initialWallet);
  const [cash, setCash] = useState("");
  const [reserved, setReserved] = useState("");
  const [hurdlePct, setHurdlePct] = useState("10");
  const [report, setReport] = useState<CapitalEfficiencyReport | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [sortKey, setSortKey] = useState<SortKey>("value");
  const [showInactive, setShowInactive] = useState(false);

  const analyze = async (event?: FormEvent) => {
    event?.preventDefault();
    const normalized = wallet.trim().toLowerCase();
    if (!WALLET_RE.test(normalized)) {
      setError("请输入 0x 开头的 40 位 Polymarket profile/proxy wallet 地址。");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const next = await weatherApi.getCapitalEfficiency({
        wallet_address: normalized,
        annual_hurdle_rate: Math.max(Number(hurdlePct) || 0, 0) / 100,
        available_cash_usd: optionalNumber(cash, "可用现金"),
        reserved_cash_usd: optionalNumber(reserved, "开放挂单预留"),
      });
      setReport(next);
      localStorage.setItem("capital-efficiency-wallet", normalized);
      const url = new URL(window.location.href);
      url.searchParams.set("wallet", normalized);
      window.history.replaceState({}, "", url);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (initialWallet && WALLET_RE.test(initialWallet)) void analyze();
    // Initial deep links should run exactly once; later runs are explicit form submissions.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sortedPositions = useMemo(() => {
    const rows = [...(report?.positions ?? [])];
    rows.sort((a, b) => {
      if (sortKey === "date") return (a.end_date || "9999-12-31").localeCompare(b.end_date || "9999-12-31");
      if (sortKey === "annualized_desc") return (b.annualized_simple_return_if_win ?? -Infinity) - (a.annualized_simple_return_if_win ?? -Infinity);
      if (sortKey === "annualized_asc") return (a.annualized_simple_return_if_win ?? Infinity) - (b.annualized_simple_return_if_win ?? Infinity);
      return b.mark_value_usd - a.mark_value_usd;
    });
    return rows;
  }, [report, sortKey]);

  return (
    <PageFrame title="PM 资金效率" desc="按期限统一比较仓位占用、可退出价值与持有回报">
      <div className="grid">
        <form className="card" onSubmit={analyze}>
          <div className="capital-form-grid">
            <label className="capital-form-wallet" style={fieldStyle}>
              <span>Polymarket 钱包地址</span>
              <input value={wallet} onChange={(e) => setWallet(e.target.value)} placeholder="0x…" className="mono" />
            </label>
            <label style={fieldStyle}>
              <span>目标年化</span>
              <div className="row"><input value={hurdlePct} onChange={(e) => setHurdlePct(e.target.value)} inputMode="decimal" /><span>%</span></div>
            </label>
            <label style={fieldStyle}>
              <span>可用现金（可选）</span>
              <input value={cash} onChange={(e) => setCash(e.target.value)} placeholder="公开地址无法读取" inputMode="decimal" />
            </label>
            <label style={fieldStyle}>
              <span>开放挂单预留（可选）</span>
              <input value={reserved} onChange={(e) => setReserved(e.target.value)} placeholder="无挂单请填 0" inputMode="decimal" />
            </label>
            <button className="primary" disabled={loading} style={{ alignSelf: "end", minHeight: 40 }}>
              {loading ? "分析中…" : "分析资金效率"}
            </button>
          </div>
          <div style={{ color: "var(--muted)", fontSize: 12, marginTop: 10 }}>
            资金利用率只有在“可用现金”和“挂单预留”都填写后才计算；仓位分析本身不需要登录或 API key。
          </div>
        </form>

        {error && <section className="card" style={{ color: "var(--bad)" }}>{error}</section>}

        {report && (
          <>
            <section className="card">
              <div className="row wrap" style={{ justifyContent: "space-between" }}>
                <div>
                  <h2 style={{ margin: 0 }}>{report.display_name || "Polymarket portfolio"}</h2>
                  <div className="mono" style={{ color: "var(--muted)", fontSize: 12, marginTop: 4 }}>{report.wallet_address}</div>
                </div>
                <div style={{ color: "var(--muted)", fontSize: 12 }}>快照 {new Date(report.observed_at_utc).toLocaleString("zh-CN")}</div>
              </div>
            </section>

            <section className="grid cols-4">
              <Kpi label={`在险仓位 · ${report.summary.active_positions_count} 个`} value={fmtUsd(report.summary.active_mark_value_usd)} />
              <Kpi label="资金利用率" value={report.summary.capital_utilization == null ? "待补现金" : pct(report.summary.capital_utilization)} />
              <Kpi label="加权剩余期限" value={report.summary.weighted_avg_days_to_end == null ? "—" : `${report.summary.weighted_avg_days_to_end.toFixed(1)} 天`} />
              <Kpi
                label={`低于目标的占用 · ${report.summary.below_hurdle_positions_count} 个 (${pct(report.summary.below_hurdle_portfolio_share)})`}
                value={fmtUsd(report.summary.below_hurdle_mark_value_usd)}
              />
            </section>

            <section className="grid cols-2">
              <section className="card">
                <h3 style={{ marginTop: 0 }}>账户资金桥</h3>
                <BridgeRow label="活跃仓位 mark value" value={fmtUsd(report.summary.active_mark_value_usd)} />
                <BridgeRow label="开放挂单预留" value={report.inputs.reserved_cash_usd == null ? "未提供" : fmtUsd(report.inputs.reserved_cash_usd)} />
                <BridgeRow label="可赎回仓位" value={fmtUsd(report.summary.redeemable_value_usd)} />
                <BridgeRow label="逐仓若赢剩余收益合计" value={fmtUsd(report.summary.remaining_upside_if_all_win_usd)} />
                <BridgeRow label="可用现金" value={report.inputs.available_cash_usd == null ? "未提供" : fmtUsd(report.inputs.available_cash_usd)} />
                <div style={{ borderTop: "1px solid var(--stroke)", marginTop: 8, paddingTop: 8 }}>
                  <BridgeRow label="可见总资金" value={report.summary.accounted_capital_usd == null ? "未闭合" : fmtUsd(report.summary.accounted_capital_usd)} strong />
                </div>
              </section>

              <section className="card">
                <h3 style={{ marginTop: 0 }}>退出流动性</h3>
                <BridgeRow label="有 order book 覆盖的 mark value" value={pct(report.summary.book_value_coverage_ratio)} />
                <BridgeRow label="可见 bids 预计回收" value={fmtUsd(report.summary.visible_exit_value_usd)} />
                <BridgeRow label="可完整退出仓位的 mark value" value={fmtUsd(report.summary.fully_quoted_mark_value_usd)} />
                <BridgeRow label="这些仓位相对 mark 的滑点" value={fmtUsd(report.summary.full_exit_slippage_usd)} />
                <div style={{ color: "var(--muted)", fontSize: 11, marginTop: 10 }}>
                  退出价值按当前可见 bids 逐档吃单估算，不含之后的盘口变化与交易 fee。
                </div>
              </section>
            </section>

            <section className="card">
              <h3 style={{ marginTop: 0 }}>资金释放时间轴</h3>
              <div className="grid" style={{ gap: 10 }}>
                {report.maturity_buckets.map((row) => (
                  <div key={row.bucket} style={timelineRowStyle}>
                    <div className="mono" style={{ width: 76 }}>{row.bucket}</div>
                    <div style={barTrackStyle}>
                      <div style={{ ...barFillStyle, width: `${Math.max(row.portfolio_share * 100, 1)}%` }} />
                    </div>
                    <div className="mono" style={{ width: 110, textAlign: "right" }}>{fmtUsd(row.mark_value_usd)}</div>
                    <div style={{ color: "var(--muted)", width: 88, textAlign: "right" }}>{pct(row.portfolio_share)}</div>
                  </div>
                ))}
              </div>
              <div style={{ color: "var(--muted)", fontSize: 11, marginTop: 12 }}>
                endDate 只是预计事件截止日，不保证当天完成 resolution / redemption。
              </div>
            </section>

            <section className="card">
              <div className="row wrap" style={{ justifyContent: "space-between", marginBottom: 12 }}>
                <div>
                  <h3 style={{ margin: 0 }}>逐仓资金效率</h3>
                  <div style={{ color: "var(--muted)", fontSize: 12, marginTop: 4 }}>
                    “要求胜率”表示该仓位跑赢 {pct(report.annual_hurdle_rate, 0)} 年化现金基准所需的最低主观胜率。
                  </div>
                </div>
                <select value={sortKey} onChange={(e) => setSortKey(e.target.value as SortKey)}>
                  <option value="value">占用资金从高到低</option>
                  <option value="date">到期从近到远</option>
                  <option value="annualized_asc">若赢年化从低到高</option>
                  <option value="annualized_desc">若赢年化从高到低</option>
                </select>
              </div>
              <PositionTable rows={sortedPositions} />
            </section>

            {report.non_active_positions.length > 0 && (
              <section className="card">
                <button onClick={() => setShowInactive((value) => !value)}>
                  {showInactive ? "收起" : "展开"}可赎回 / dust（{report.non_active_positions.length}）
                </button>
                {showInactive && <div style={{ marginTop: 12 }}><PositionTable rows={report.non_active_positions} /></div>}
              </section>
            )}

            <section className="card" style={{ color: "var(--muted)", fontSize: 12, lineHeight: 1.6 }}>
              <strong style={{ color: "var(--ink)" }}>口径说明：</strong> 年化值是“该 outcome 最终赢”的简单年化上限，不是期望收益或投资建议；多个仓位可能互斥，不能把“逐仓若赢”合计当作可实现 PnL。公开 API 无法看到 authenticated open orders、完整现金和外部转账，因此未填写现金输入时账户桥明确标为未闭合。
            </section>
            {(report.data_quality.positions_truncated || !report.data_quality.order_book_fetch_complete) && (
              <section className="card" style={{ color: "var(--bad)", fontSize: 12 }}>
                数据不完整：{report.data_quality.positions_truncated ? "持仓超过官方分页上限；" : ""}
                {!report.data_quality.order_book_fetch_complete ? "部分 order book 批次未返回；退出价值只能按已覆盖盘口解释。" : ""}
              </section>
            )}
          </>
        )}
      </div>
    </PageFrame>
  );
}

function PositionTable({ rows }: { rows: CapitalEfficiencyPosition[] }) {
  return (
    <div className="table-wrap">
      <table style={{ minWidth: 1180 }}>
        <thead>
          <tr>
            <th>Market / outcome</th><th>预计释放</th><th>占用 mark</th><th>退出 VWAP</th><th>若赢收益</th><th>若赢简单年化</th><th>要求胜率</th><th>成本 PnL</th><th>状态</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.token_id || row.condition_id || index}`}>
              <td style={{ maxWidth: 390 }}>
                {row.event_slug ? (
                  <a href={`https://polymarket.com/event/${row.event_slug}`} target="_blank" rel="noreferrer" style={{ fontWeight: 700 }}>{row.title || "—"}</a>
                ) : <div style={{ fontWeight: 700 }}>{row.title || "—"}</div>}
                <div style={{ color: "var(--muted)", fontSize: 11 }}>{row.outcome || "—"} · {row.size.toFixed(2)} shares · mark {pct(row.mark_price, 1)}</div>
              </td>
              <td className="mono">{row.end_date || "—"}<div style={{ color: "var(--muted)", fontSize: 11 }}>{row.days_to_end == null ? "" : `${row.days_to_end}d`}</div></td>
              <td className="mono">{fmtUsd(row.mark_value_usd)}</td>
              <td className="mono">{row.visible_exit_vwap == null ? "—" : pct(row.visible_exit_vwap, 2)}<div style={{ color: "var(--muted)", fontSize: 11 }}>depth {pct(row.exit_coverage_ratio, 0)}</div></td>
              <td className="mono">{pct(row.gross_return_if_win)}<div style={{ color: "var(--muted)", fontSize: 11 }}>{fmtUsd(row.remaining_upside_if_win_usd)}</div></td>
              <td className="mono">{pct(row.annualized_simple_return_if_win)}</td>
              <td className="mono" style={{ color: row.hurdle_feasible === false ? "var(--bad)" : undefined }}>{pct(row.required_confidence_for_hurdle)}</td>
              <td className="mono" style={{ color: row.unrealized_pnl_usd < 0 ? "var(--bad)" : "var(--ok)" }}>{fmtUsd(row.unrealized_pnl_usd)}</td>
              <td><span className={`badge ${row.status === "pending_resolution" ? "error" : "stale"}`}>{statusLabel(row.status)}</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Kpi({ label, value }: { label: string; value: string }) {
  return <div className="card"><div className="kpi-value">{value}</div><div className="kpi-label">{label}</div></div>;
}

function BridgeRow({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return <div className="row" style={{ justifyContent: "space-between", padding: "6px 0", fontWeight: strong ? 700 : 400 }}><span style={{ color: strong ? "var(--ink)" : "var(--muted)" }}>{label}</span><span className="mono">{value}</span></div>;
}

const fieldStyle: React.CSSProperties = { display: "grid", gap: 6, color: "var(--muted)", fontSize: 12 };
const timelineRowStyle: React.CSSProperties = { display: "flex", alignItems: "center", gap: 10 };
const barTrackStyle: React.CSSProperties = { flex: 1, height: 12, borderRadius: 999, background: "rgba(42, 95, 255, 0.10)", overflow: "hidden" };
const barFillStyle: React.CSSProperties = { height: "100%", borderRadius: 999, background: "linear-gradient(90deg, var(--accent-2), var(--accent))" };
