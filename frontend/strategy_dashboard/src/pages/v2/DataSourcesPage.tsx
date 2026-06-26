import { useEffect, useState } from "react";
import { weatherApi } from "../../data/weather-http";
import type { DataSourcesResponse } from "../../data/v2-types";
import { EmptyState } from "../../components/v2/EmptyState";
import { GlossaryTerm } from "../../components/v2/GlossaryTerm";
import { HealthDot } from "../../components/v2/HealthDot";
import type { Freshness } from "../../data/v2-types";

function ageMin(iso: string | null): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return (Date.now() - t) / 60000;
}
function fresh(iso: string | null, warn: number, bad: number): Freshness {
  const a = ageMin(iso);
  if (a == null) return "unknown";
  return a <= warn ? "fresh" : a <= bad ? "aging" : "stale";
}
function ageText(iso: string | null): string {
  const a = ageMin(iso);
  if (a == null) return "—";
  if (a < 90) return `${Math.round(a)} 分钟前`;
  return `${(a / 60).toFixed(1)} 小时前`;
}

export function DataSourcesPage() {
  const [data, setData] = useState<DataSourcesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    weatherApi.getDataSources().then(setData).catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="page">
      <header className="page-head">
        <h1>数据源</h1>
        <p className="page-sub">我们抓取了哪些数据、来自哪、什么时候、覆盖哪些城市。上层是驱动决策的预测源，下层是实时盘口快照流。</p>
      </header>

      {error && <div className="error-banner">{error}</div>}
      {data == null && !error && <EmptyState message="加载中…" />}

      {data && (
        <>
          <section className="card">
            <h2>预测数据源 <span className="muted">（驱动决策，来自 fact_trades）</span></h2>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr><th><GlossaryTerm field="forecast_source">数据源</GlossaryTerm></th><th>最近快照</th><th>城市数</th><th>模型数</th><th>用到的笔数</th><th>覆盖日期</th></tr>
                </thead>
                <tbody>
                  {data.forecast_sources.map((s) => (
                    <tr key={s.forecast_source}>
                      <td style={{ fontFamily: "ui-monospace, monospace" }}>{s.forecast_source}</td>
                      <td><HealthDot level={fresh(s.latest_snapshot_ts_utc, 90, 360)} /> {ageText(s.latest_snapshot_ts_utc)}<div className="muted" style={{ fontSize: 11 }}>{s.latest_snapshot_ts_utc}</div></td>
                      <td>{s.cities}</td>
                      <td>{s.models}</td>
                      <td>{s.rows}</td>
                      <td className="muted" style={{ fontSize: 12 }}>{s.first_target_date} → {s.last_target_date}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="card">
            <h2>实时盘口快照 <span className="muted">（最近 {data.market_snapshots.length} 个）</span></h2>
            {data.market_snapshots.length === 0 ? (
              <EmptyState message="没有盘口快照文件" hint="检查 runtime/.../paper_snapshots 是否已同步。" />
            ) : (
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr><th>快照</th><th>北京时间</th><th>新鲜度</th><th><GlossaryTerm field="total_records">记录数</GlossaryTerm></th><th>交易城市</th><th>研究城市</th></tr>
                  </thead>
                  <tbody>
                    {data.market_snapshots.map((m) => (
                      <tr key={m.file}>
                        <td style={{ fontFamily: "ui-monospace, monospace", fontSize: 12 }}>{m.file}</td>
                        <td>{m.ts_beijing ?? "—"}</td>
                        <td><HealthDot level={fresh(m.ts_utc, 45, 180)} /> {ageText(m.ts_utc)}</td>
                        <td>{m.total_records ?? "—"}</td>
                        <td>{m.trading_cities ?? "—"}</td>
                        <td>{m.research_cities ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
