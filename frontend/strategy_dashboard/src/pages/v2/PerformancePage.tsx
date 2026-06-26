import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { weatherApi } from "../../data/weather-http";
import type { LiveSummary } from "../../data/weather-types";
import type { LiveBookRow, LiveBookStrategy } from "../../data/v2-types";
import { EmptyState } from "../../components/v2/EmptyState";
import { GlossaryTerm } from "../../components/v2/GlossaryTerm";
import { usd } from "./format";

function sideTag(side: string | null) {
  if (!side) return "—";
  const no = side.includes("NO");
  return <span style={{ color: no ? "var(--accent)" : "var(--ok)", fontWeight: 700 }}>{no ? "NO" : "YES"}</span>;
}

function BookTable({ rows, showPnl }: { rows: LiveBookRow[]; showPnl: "realized" | "mtm" }) {
  return (
    <div className="table-scroll">
      <table className="data-table">
        <thead>
          <tr>
            <th>城市</th><th><GlossaryTerm field="target_date">日期</GlossaryTerm></th>
            <th><GlossaryTerm field="bracket">档</GlossaryTerm></th>
            <th><GlossaryTerm field="side">方向</GlossaryTerm></th>
            <th><GlossaryTerm field="fill_price">成交价</GlossaryTerm></th>
            <th><GlossaryTerm field="cost_usd">成本</GlossaryTerm></th>
            <th><GlossaryTerm field="forecast_source">数据源</GlossaryTerm></th>
            <th>{showPnl === "realized" ? <GlossaryTerm field="pnl_usd_at_fill">已实现</GlossaryTerm> : <GlossaryTerm field="unrealized_pnl_mid">浮动(MTM)</GlossaryTerm>}</th>
            <th>Poly</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => {
            const pnl = showPnl === "realized" ? r.pnl_usd_at_fill : r.unrealized_pnl_mid;
            return (
              <tr key={r.fill_id}>
                <td>{r.city ?? "—"}</td>
                <td>{r.target_date ? <Link to={`/lineage/${r.target_date}`}>{r.target_date}</Link> : "—"}</td>
                <td>{r.bracket ?? "—"}</td>
                <td>{sideTag(r.side)}</td>
                <td>{r.fill_price ?? "—"}</td>
                <td>{usd(r.cost_usd)}</td>
                <td className="muted" style={{ fontSize: 11 }}>{(r.forecast_source ?? "").replace("open_meteo_live_", "") || "—"}</td>
                <td style={{ color: (pnl ?? 0) >= 0 ? "var(--ok)" : "var(--bad)" }}>{usd(pnl, true)}</td>
                <td>{r.poly_url ? <a href={r.poly_url} target="_blank" rel="noreferrer" className="poly-link">↗</a> : <span className="muted">—</span>}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export function PerformancePage() {
  const [summary, setSummary] = useState<LiveSummary | null>(null);
  const [open, setOpen] = useState<LiveBookRow[] | null>(null);
  const [settled, setSettled] = useState<LiveBookRow[] | null>(null);
  const [strategies, setStrategies] = useState<LiveBookStrategy[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    weatherApi.getLiveSummary().then(setSummary).catch((e) => setError(String(e)));
    weatherApi.getLiveBook({ status: "open", limit: 300 }).then((r) => setOpen(r.rows)).catch(() => setOpen([]));
    weatherApi.getLiveBook({ status: "settled", limit: 60 }).then((r) => setSettled(r.rows)).catch(() => setSettled([]));
    weatherApi.getLiveBookStrategies().then((r) => setStrategies(r.strategies)).catch(() => setStrategies([]));
  }, []);

  const c = summary?.clob;
  const recentOpen = (open ?? []).filter((r) => !r.stale_unsettled);
  const staleOpen = (open ?? []).filter((r) => r.stale_unsettled);

  return (
    <div className="page">
      <header className="page-head">
        <h1>绩效对账</h1>
        <p className="page-sub">只统计 <strong>live 实盘（trade_class=live_real）</strong>。开仓成本不是亏损；只有已结算才报已实现盈亏，未结算只报 MTM 浮动。</p>
      </header>

      <div className="gate-banner gate-warn">
        <GlossaryTerm field="gate_pass">成交覆盖门</GlossaryTerm> 未在看板内校验 —— 发布 PnL 前请先跑
        <code> scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py</code>。
      </div>
      {error && <div className="error-banner">{error}</div>}

      <div className="kpi-grid">
        <div className="card kpi">
          <div className="kpi-label"><GlossaryTerm field="open_cost_usd">在险资金</GlossaryTerm><span className="kpi-note">近期·非亏损</span></div>
          <div className="kpi-value">{usd(c?.open_recent_cost_usd ?? null)}</div>
        </div>
        <div className="card kpi">
          <div className="kpi-label">未结算浮动 <span className="kpi-note">MTM</span></div>
          <div className="kpi-value" style={{ color: (c?.open_unrealized_pnl_usd ?? 0) >= 0 ? "var(--ok)" : "var(--bad)" }}>{usd(c?.open_unrealized_pnl_usd ?? null, true)}</div>
        </div>
        <div className="card kpi">
          <div className="kpi-label"><GlossaryTerm field="realized_pnl">已实现盈亏</GlossaryTerm><span className="kpi-note">仅已结算</span></div>
          <div className="kpi-value" style={{ color: (c?.realized_pnl_usd ?? 0) >= 0 ? "var(--ok)" : "var(--bad)" }}>{usd(c?.realized_pnl_usd ?? null, true)}</div>
        </div>
        <div className="card kpi">
          <div className="kpi-label">未结算 / 已结算</div>
          <div className="kpi-value">{c?.open_count ?? 0} / {c?.settled_count ?? 0}</div>
        </div>
      </div>

      <section className="card">
        <h2>当前未结算持仓 <span className="muted">({recentOpen.length})</span></h2>
        {open == null && <EmptyState message="加载中…" />}
        {open != null && recentOpen.length === 0 && <EmptyState message="当前没有近期未结算的 live 持仓" />}
        {recentOpen.length > 0 && <BookTable rows={recentOpen} showPnl="mtm" />}
      </section>

      {staleOpen.length > 0 && (
        <section className="card">
          <h2>陈旧未结算 <span className="muted">({staleOpen.length}) · 疑似漏结算</span></h2>
          <div className="gate-banner gate-warn">
            这些是 target_date 超过 7 天却仍未结算的旧持仓（多为已停用的 <code>mid_price_core</code> 策略，
            <code>settlement_join_method=none</code>）。它们现实中早已结算，只是<strong>结算未回填进 fact_trades</strong>，
            不算真正在险。修复请走结算回填（<code>weather-fact-rebuild</code> / settlement backfill）。
          </div>
          <BookTable rows={staleOpen} showPnl="mtm" />
        </section>
      )}

      <section className="card">
        <h2>按策略汇总（live_real）</h2>
        {strategies == null && <EmptyState message="加载中…" />}
        {strategies != null && (
          <div className="table-scroll">
            <table className="data-table">
              <thead><tr><th>策略</th><th>笔数</th><th>未结算</th><th>开仓成本</th><th>已实现盈亏</th><th>最近成交</th></tr></thead>
              <tbody>
                {strategies.map((s) => (
                  <tr key={s.strategy_name}>
                    <td style={{ fontFamily: "ui-monospace, monospace", fontSize: 12 }}>{s.strategy_name}</td>
                    <td>{s.n}</td>
                    <td>{s.open_count}</td>
                    <td>{usd(s.open_cost_usd)}</td>
                    <td style={{ color: s.realized_pnl_usd >= 0 ? "var(--ok)" : "var(--bad)" }}>{usd(s.realized_pnl_usd, true)}</td>
                    <td className="muted" style={{ fontSize: 11 }}>{s.last_fill_ts_utc?.slice(0, 16) ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card">
        <h2>最近已结算 <span className="muted">({settled?.length ?? 0})</span></h2>
        {settled != null && settled.length > 0 ? <BookTable rows={settled} showPnl="realized" /> : <EmptyState message="无已结算记录" />}
      </section>
    </div>
  );
}
