import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { weatherApi } from "../../data/weather-http";
import type { ProbeHealthRow, ResearchLineRow } from "../../data/v2-types";
import type { LiveSummary } from "../../data/weather-types";
import { HealthDot } from "../../components/v2/HealthDot";
import { GlossaryTerm } from "../../components/v2/GlossaryTerm";
import { usd, pct } from "./format";

export function TodayOverviewPage() {
  const [probes, setProbes] = useState<ProbeHealthRow[] | null>(null);
  const [live, setLive] = useState<LiveSummary | null>(null);
  const [lines, setLines] = useState<ResearchLineRow[] | null>(null);

  useEffect(() => {
    weatherApi.getProbeHealth().then((r) => setProbes(r.probes)).catch(() => setProbes([]));
    weatherApi.getLiveSummary().then(setLive).catch(() => setLive(null));
    weatherApi.getResearchLines().then((r) => setLines(r.lines)).catch(() => setLines([]));
  }, []);

  const running = probes?.filter((p) => ["live", "shadow", "telemetry", "monitor"].includes(p.lifecycle_status ?? "")).length ?? 0;
  const aging = probes?.filter((p) => p.freshness === "aging").length ?? 0;
  const needAttn = probes?.filter((p) => p.freshness === "stale" || p.status === "no_pulse_file").length ?? 0;

  const recentLines = (lines ?? [])
    .slice()
    .sort((a, b) => (b.generated_at_utc ?? "").localeCompare(a.generated_at_utc ?? ""))
    .slice(0, 5);

  return (
    <div className="page">
      <header className="page-head">
        <h1>今日总览</h1>
        <p className="page-sub">当前没有已确认的稳定 live alpha；在跑的都是 tiny-live 前向取证（$5–$10 微仓）。</p>
      </header>

      <div className="pulse-grid">
        {/* Probe health */}
        <Link to="/probes" className="card pulse-card">
          <div className="pulse-title">探针健康</div>
          <div className="pulse-big">{running}<span className="pulse-unit"> 在跑</span></div>
          <div className="pulse-detail">
            <span><HealthDot level="aging" /> {aging} 偏旧</span>
            <span><HealthDot level="stale" /> {needAttn} 待查</span>
          </div>
        </Link>

        {/* Capital at risk */}
        <Link to="/performance" className="card pulse-card">
          <div className="pulse-title">在险资金</div>
          <div className="pulse-big">{usd(live?.clob.capital_deployed_usd ?? null)}</div>
          <div className="pulse-detail">
            <span><GlossaryTerm field="open_cost">开仓成本（非亏损）</GlossaryTerm></span>
            <span>{live?.clob.open_count ?? 0} 未结算 · {live?.clob.settled_count ?? 0} 已结算</span>
          </div>
        </Link>

        {/* Settled */}
        <Link to="/performance" className="card pulse-card">
          <div className="pulse-title">已结算盈亏</div>
          <div className="pulse-big" style={{ color: (live?.clob.realized_pnl_usd ?? 0) >= 0 ? "var(--ok)" : "var(--bad)" }}>
            {usd(live?.clob.realized_pnl_usd ?? null, true)}
          </div>
          <div className="pulse-detail">
            <span><GlossaryTerm field="realized_pnl">已实现</GlossaryTerm> · 仅已结算</span>
          </div>
        </Link>
      </div>

      <section className="card">
        <div className="section-head">
          <h2>最近变更的研究线</h2>
          <Link to="/research" className="section-link">全部研究证据 →</Link>
        </div>
        {recentLines.length === 0 && <div className="muted">暂无</div>}
        <table className="data-table">
          <thead>
            <tr><th>研究线</th><th>状态</th><th>holdout ROI</th><th>forward ROI</th><th>够 gate?</th></tr>
          </thead>
          <tbody>
            {recentLines.map((l) => (
              <tr key={l.line_id}>
                <td><Link to={`/research/${encodeURIComponent(l.line_id)}`}>{l.title}</Link></td>
                <td>{l.status}</td>
                <td>{pct(l.holdout_roi)}</td>
                <td>{pct(l.forward_roi)}</td>
                <td>{l.gate_ready ? <span className="badge" data-tone="fresh">够</span> : <span className="badge" data-tone="aging">不够</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
