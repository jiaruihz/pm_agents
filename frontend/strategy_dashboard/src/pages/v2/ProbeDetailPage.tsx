import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { weatherApi } from "../../data/weather-http";
import type { ProbeDetail } from "../../data/v2-types";
import { FreshnessBadge } from "../../components/v2/FreshnessBadge";
import { EmptyState } from "../../components/v2/EmptyState";
import { lifecycleZh, num } from "./format";

export function ProbeDetailPage() {
  const { instance = "" } = useParams();
  const [data, setData] = useState<ProbeDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    weatherApi.getProbe(instance).then(setData).catch((e) => setError(String(e)));
  }, [instance]);

  if (error) return <div className="page"><div className="error-banner">{error}</div></div>;
  if (!data) return <div className="page"><EmptyState message="加载中…" /></div>;

  const { row, history, candidates, live_orders } = data;

  return (
    <div className="page">
      <header className="page-head">
        <Link to="/probes" className="back-link">← 探针在跑</Link>
        <h1>{row.strategy_instance}</h1>
        <p className="page-sub">{lifecycleZh(row.lifecycle_status)} · 状态 {row.status}</p>
      </header>

      <section className="card">
        <h2>新鲜度</h2>
        <FreshnessBadge heartbeatAgeMin={row.heartbeat_age_min} snapshotAgeMin={row.snapshot_age_min} snapshotTsUtc={row.snapshot_ts_utc} />
      </section>

      <section className="card">
        <h2>今日候选 <span className="muted">({candidates.length})</span></h2>
        {candidates.length === 0 ? (
          <EmptyState message="本轮没有候选" hint="探针在等盘口或新鲜观测。" />
        ) : (
          <pre className="json-block">{JSON.stringify(candidates.slice(0, 20), null, 2)}</pre>
        )}
      </section>

      <section className="card">
        <h2>今日实单 <span className="muted">({live_orders.length})</span></h2>
        {live_orders.length === 0 ? <div className="muted">无</div> : <pre className="json-block">{JSON.stringify(live_orders, null, 2)}</pre>}
      </section>

      <section className="card">
        <h2>脉搏历史 <span className="muted">({history.length})</span></h2>
        {history.length === 0 ? (
          <div className="muted">无历史</div>
        ) : (
          <table className="data-table">
            <thead><tr><th>时间</th><th>候选</th><th>快照年龄(min)</th><th>状态</th></tr></thead>
            <tbody>
              {history.slice(-30).reverse().map((h, i) => (
                <tr key={i}>
                  <td>{String(h.generated_at_utc ?? "—")}</td>
                  <td>{num((h.candidate_rows as number) ?? null)}</td>
                  <td>{String((h.snapshot_age_min as number) ?? (h.meta as any)?.snapshot_age_min ?? "—")}</td>
                  <td>{String(h.status ?? "—")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
}
