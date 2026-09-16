import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { PageFrame } from "../components/PageFrame";
import { useDashboardProvider } from "../data/provider-context";
import type { AccountAggregate } from "../data/types";
import { fmtCurrency, fmtInt } from "../utils/format";

export function AccountsPage(): JSX.Element {
  const provider = useDashboardProvider();
  const [items, setItems] = useState<AccountAggregate[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let dead = false;
    provider
      .listAccounts(400, 0)
      .then((data) => {
        if (!dead) {
          setItems(data.items);
          setError("");
        }
      })
      .catch((e) => {
        if (!dead) setError((e as Error).message);
      });
    return () => {
      dead = true;
    };
  }, [provider]);

  return (
    <PageFrame title="账户中心" desc="按 account_id / wallet 聚合展示策略实例资产">
      <div className="grid">
        {error && <div className="card error">{error}</div>}
        <section className="card">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Group Key</th>
                  <th>Account</th>
                  <th>Wallet</th>
                  <th>Equity</th>
                  <th>USDC</th>
                  <th>PnL</th>
                  <th>Open Orders</th>
                  <th>Running</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.group_key}>
                    <td>
                      {item.group_key === "unknown_account" ? <span className="warn">unknown_account</span> : item.group_key}
                    </td>
                    <td>{item.account_id || "-"}</td>
                    <td>{item.wallet_address || "-"}</td>
                    <td>{fmtCurrency(item.equity_total)}</td>
                    <td>{fmtCurrency(item.usdc_total)}</td>
                    <td>{fmtCurrency(item.pnl_total)}</td>
                    <td>{fmtInt(item.open_orders_total)}</td>
                    <td>{fmtInt(item.running_instances)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        {items.map((item) => (
          <section className="card" key={`${item.group_key}-instances`}>
            <h3>
              实例明细 · {item.group_key} ({item.instances.length})
            </h3>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Instance</th>
                    <th>Strategy</th>
                    <th>Status</th>
                    <th>PnL</th>
                    <th>Equity</th>
                  </tr>
                </thead>
                <tbody>
                  {item.instances.map((ins) => (
                    <tr key={ins.instance_id}>
                      <td>
                        <Link to={`/instances/${ins.instance_id}`}>{ins.label || ins.instance_id}</Link>
                      </td>
                      <td>
                        <Link to={`/strategies/${ins.strategy_key}`}>{ins.strategy_key}</Link>
                      </td>
                      <td>
                        <span className={`badge ${ins.runtime_status ?? ins.status}`}>{ins.runtime_status ?? ins.status}</span>
                      </td>
                      <td>{fmtCurrency(ins.last_pnl)}</td>
                      <td>{fmtCurrency(ins.last_equity)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ))}
      </div>
    </PageFrame>
  );
}
