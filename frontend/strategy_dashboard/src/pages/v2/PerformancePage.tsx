import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { weatherApi } from "../../data/weather-http";
import type { LiveSummary, LivePosition } from "../../data/weather-types";
import { EmptyState } from "../../components/v2/EmptyState";
import { GlossaryTerm } from "../../components/v2/GlossaryTerm";
import { usd } from "./format";

export function PerformancePage() {
  const [summary, setSummary] = useState<LiveSummary | null>(null);
  const [positions, setPositions] = useState<LivePosition[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    weatherApi.getLiveSummary().then(setSummary).catch((e) => setError(String(e)));
    weatherApi.getLivePositions({ limit: 200 }).then(setPositions).catch(() => setPositions([]));
  }, []);

  return (
    <div className="page">
      <header className="page-head">
        <h1>绩效对账</h1>
        <p className="page-sub">硬口径：开仓成本不是亏损；只有已结算才报已实现盈亏；未结算只报 MTM。</p>
      </header>

      {/* gate banner — placeholder until /api wires the coverage gate; show caution by default */}
      <div className="gate-banner gate-warn">
        <GlossaryTerm field="gate_pass">成交覆盖门</GlossaryTerm> 未在看板内校验 —— 发布 PnL 前请先跑
        <code> scripts/analysis/execution_quality/weather_clob_fill_coverage_gate.py</code>。
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="kpi-grid">
        <div className="card kpi">
          <div className="kpi-label"><GlossaryTerm field="open_cost">开仓成本</GlossaryTerm><span className="kpi-note">非亏损</span></div>
          <div className="kpi-value">{usd(summary?.clob.capital_deployed_usd ?? null)}</div>
        </div>
        <div className="card kpi">
          <div className="kpi-label"><GlossaryTerm field="realized_pnl">已实现盈亏</GlossaryTerm><span className="kpi-note">仅已结算</span></div>
          <div className="kpi-value" style={{ color: (summary?.clob.realized_pnl_usd ?? 0) >= 0 ? "var(--ok)" : "var(--bad)" }}>
            {usd(summary?.clob.realized_pnl_usd ?? null, true)}
          </div>
        </div>
        <div className="card kpi">
          <div className="kpi-label">未结算 / 已结算</div>
          <div className="kpi-value">{summary?.clob.open_count ?? 0} / {summary?.clob.settled_count ?? 0}</div>
        </div>
        <div className="card kpi">
          <div className="kpi-label">挂单预留</div>
          <div className="kpi-value">{usd(summary?.pending_orders.reserved_usd ?? null)} <span className="kpi-note">{summary?.pending_orders.count ?? 0} 单</span></div>
        </div>
      </div>

      <section className="card">
        <h2>持仓（real fills）</h2>
        {positions == null && <EmptyState message="加载中…" />}
        {positions != null && positions.length === 0 && <EmptyState message="无 real fill 持仓" />}
        {positions != null && positions.length > 0 && (
          <table className="data-table">
            <thead>
              <tr><th>城市</th><th>日期</th><th>档位</th><th>方向</th><th>成交成本</th><th>结算</th><th>已实现</th></tr>
            </thead>
            <tbody>
              {positions.map((p) => (
                <tr key={p.fill_id}>
                  <td>{p.city ?? "—"}</td>
                  <td>{p.target_date ? <Link to={`/lineage/${p.target_date}`}>{p.target_date}</Link> : "—"}</td>
                  <td>{p.bracket ?? "—"}</td>
                  <td>{p.order_side}</td>
                  <td>{usd(p.cost_usd)}</td>
                  <td>{p.settlement_status ?? "—"}</td>
                  <td style={{ color: (p.pnl_usd ?? 0) >= 0 ? "var(--ok)" : "var(--bad)" }}>
                    {p.settlement_status === "settled" ? usd(p.pnl_usd, true) : <span className="muted">未结算</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
