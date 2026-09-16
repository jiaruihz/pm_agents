import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { PageFrame } from "../components/PageFrame";
import { useDashboardProvider } from "../data/provider-context";
import type { InstanceItem, StrategyItem } from "../data/types";
import { fmtCurrency, fmtDate, fmtInt } from "../utils/format";

export function StrategyDetailPage(): JSX.Element {
  const provider = useDashboardProvider();
  const { strategyKey = "" } = useParams();
  const [strategy, setStrategy] = useState<StrategyItem | null>(null);
  const [instances, setInstances] = useState<InstanceItem[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const [strategies, list] = await Promise.all([
          provider.listStrategies(500),
          provider.listInstances({ strategy_key: strategyKey, limit: 200 }),
        ]);
        if (dead) return;
        setStrategy(strategies.find((x) => x.strategy_key === strategyKey) ?? null);
        setInstances(list.items);
        setError("");
      } catch (e) {
        if (!dead) setError((e as Error).message);
      }
    })();
    return () => {
      dead = true;
    };
  }, [provider, strategyKey]);

  const pnl = useMemo(() => instances.reduce((acc, x) => acc + Number(x.last_pnl ?? 0), 0), [instances]);

  return (
    <PageFrame title={`策略详情 · ${strategyKey}`} desc="实例明细与运行效果">
      <div className="grid">
        {error && <div className="card error">{error}</div>}
        <section className="grid cols-4">
          <div className="card">
            <div className="kpi-value">{strategy?.strategy_name ?? "-"}</div>
            <div className="kpi-label">策略名称</div>
          </div>
          <div className="card">
            <div className="kpi-value">{fmtInt(instances.length)}</div>
            <div className="kpi-label">实例数量</div>
          </div>
          <div className="card">
            <div className="kpi-value">{fmtCurrency(pnl)}</div>
            <div className="kpi-label">实例 PnL 合计</div>
          </div>
          <div className="card">
            <div className="kpi-value">{strategy?.domain ?? "-"}</div>
            <div className="kpi-label">Domain</div>
          </div>
        </section>

        <section className="card">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Instance</th>
                  <th>Status</th>
                  <th>Mode</th>
                  <th>PnL</th>
                  <th>Equity</th>
                  <th>Heartbeat</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {instances.map((item) => {
                  const status = item.runtime_status ?? item.status;
                  return (
                    <tr key={item.instance_id}>
                      <td>
                        <Link to={`/instances/${item.instance_id}`}>{item.label || item.instance_id}</Link>
                      </td>
                      <td>
                        <span className={`badge ${status}`}>{status}</span>
                      </td>
                      <td>{item.execution_mode}</td>
                      <td>{fmtCurrency(item.last_pnl)}</td>
                      <td>{fmtCurrency(item.last_equity)}</td>
                      <td>{fmtDate(item.heartbeat_at_utc)}</td>
                      <td>{fmtDate(item.updated_at_utc)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </PageFrame>
  );
}
