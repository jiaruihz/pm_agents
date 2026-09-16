import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { PageFrame } from "../components/PageFrame";
import { Sparkline } from "../components/Sparkline";
import { useDashboardProvider } from "../data/provider-context";
import type { InstanceHistoryPoint, InstanceItem, OrderRecord } from "../data/types";
import { fmtAge, fmtCurrency, fmtDate, fmtInt } from "../utils/format";

export function InstanceDetailPage(): JSX.Element {
  const provider = useDashboardProvider();
  const { instanceId = "" } = useParams();
  const [item, setItem] = useState<InstanceItem | null>(null);
  const [history, setHistory] = useState<InstanceHistoryPoint[]>([]);
  const [orders, setOrders] = useState<OrderRecord[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const [detail, hist, ords] = await Promise.all([
          provider.getInstance(instanceId),
          provider.getInstanceHistory(instanceId, 300),
          provider.getTradeOrders(instanceId, 100),
        ]);
        if (dead) return;
        setItem(detail);
        setHistory(hist.slice().reverse());
        setOrders(ords);
        setError("");
      } catch (e) {
        if (!dead) setError((e as Error).message);
      }
    })();
    return () => {
      dead = true;
    };
  }, [provider, instanceId]);

  const pnlSeries = useMemo(() => history.map((x) => Number(x.pnl ?? 0)), [history]);
  const equitySeries = useMemo(() => history.map((x) => Number(x.equity ?? 0)), [history]);
  const usdcSeries = useMemo(() => history.map((x) => Number(x.usdc ?? 0)), [history]);

  return (
    <PageFrame title={`实例详情 · ${instanceId}`} desc="状态、时序快照、运行参数">
      <div className="grid">
        {error && <div className="card error">{error}</div>}
        {item && (
          <>
            <section className="grid cols-4">
              <div className="card">
                <div className="kpi-value">{item.label || item.instance_id}</div>
                <div className="kpi-label">实例标识</div>
              </div>
              <div className="card">
                <div className="kpi-value">{fmtCurrency(item.last_pnl)}</div>
                <div className="kpi-label">最新 PnL</div>
              </div>
              <div className="card">
                <div className="kpi-value">{fmtCurrency(item.last_equity)}</div>
                <div className="kpi-label">最新 Equity</div>
              </div>
              <div className="card">
                <div className="kpi-value">{fmtAge(item.heartbeat_age_sec)}</div>
                <div className="kpi-label">Heartbeat Age</div>
              </div>
            </section>

            <section className="grid cols-2">
              <div className="card">
                <h3>PnL 轨迹</h3>
                <Sparkline points={pnlSeries} color="var(--accent)" />
              </div>
              <div className="card">
                <h3>Equity 轨迹</h3>
                <Sparkline points={equitySeries} color="var(--accent-2)" />
              </div>
            </section>

            <section className="grid cols-2">
              <div className="card">
                <h3>USDC 轨迹</h3>
                <Sparkline points={usdcSeries} color="var(--ok)" />
              </div>
              <div className="card">
                <h3>实例属性</h3>
                <div className="row wrap" style={{ marginBottom: 10 }}>
                  <span className={`badge ${item.runtime_status ?? item.status}`}>{item.runtime_status ?? item.status}</span>
                  <span className="badge stopped">{item.execution_mode}</span>
                  <Link to={`/strategies/${item.strategy_key}`}>{item.strategy_key}</Link>
                </div>
                <pre>{JSON.stringify(item, null, 2)}</pre>
              </div>
            </section>

            <section className="card">
              <h3>最近快照</h3>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Tick</th>
                      <th>PnL</th>
                      <th>Equity</th>
                      <th>USDC</th>
                      <th>Open Orders</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.slice(-30).reverse().map((row) => (
                      <tr key={`${row.ts_utc}-${row.tick}`}>
                        <td>{fmtDate(row.ts_utc)}</td>
                        <td>{fmtInt(row.tick)}</td>
                        <td>{fmtCurrency(row.pnl)}</td>
                        <td>{fmtCurrency(row.equity)}</td>
                        <td>{fmtCurrency(row.usdc)}</td>
                        <td>{fmtInt(row.open_orders)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>

            <section className="card">
              <h3>订单明细 (Trade Orders)</h3>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Order ID</th>
                      <th>Strategy</th>
                      <th>Token</th>
                      <th>Side</th>
                      <th>Type</th>
                      <th>Size</th>
                      <th>Price</th>
                      <th>Status</th>
                      <th>Filled</th>
                      <th>Avg Price</th>
                      <th>Fee Paid</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders.map((row) => (
                      <tr key={row.order_id}>
                        <td>{fmtDate(row.updated_at_utc)}</td>
                        <td title={row.order_id}>{row.order_id.length > 20 ? row.order_id.slice(0, 15) + "..." : row.order_id}</td>
                        <td><Link to={`/strategies/${row.strategy_key}`}>{row.strategy_key}</Link></td>
                        <td>{row.token_id}</td>
                        <td className={row.side.toLowerCase()}>{row.side}</td>
                        <td>{row.order_type}</td>
                        <td>{row.size}</td>
                        <td>{fmtCurrency(row.price)}</td>
                        <td><span className={`badge ${row.status.toLowerCase()}`}>{row.status}</span></td>
                        <td>{row.filled_size}</td>
                        <td>{fmtCurrency(row.average_price)}</td>
                        <td>{fmtCurrency(row.fee_paid)}</td>
                      </tr>
                    ))}
                    {orders.length === 0 && (
                      <tr>
                        <td colSpan={12} style={{ textAlign: "center" }}>无近期订单 (No recent orders)</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </div>
    </PageFrame>
  );
}
