import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { PageFrame } from "../components/PageFrame";
import { useDashboardProvider } from "../data/provider-context";
import type { AccountAggregate, InstanceItem, StrategyItem } from "../data/types";
import { fmtCurrency, fmtInt } from "../utils/format";

export function DashboardPage(): JSX.Element {
  const provider = useDashboardProvider();
  const [strategies, setStrategies] = useState<StrategyItem[]>([]);
  const [instances, setInstances] = useState<InstanceItem[]>([]);
  const [accounts, setAccounts] = useState<AccountAggregate[]>([]);
  const [error, setError] = useState<string>("");
  const [selectedStrategy, setSelectedStrategy] = useState<string>("all");

  useEffect(() => {
    let dead = false;
    (async () => {
      try {
        const [ss, ii, aa] = await Promise.all([
          provider.listStrategies(300),
          provider.listInstances({ limit: 200 }),
          provider.listAccounts(100, 0),
        ]);
        if (dead) return;
        setStrategies(ss);
        setInstances(ii.items);
        setAccounts(aa.items);
        setError("");
      } catch (e) {
        if (!dead) setError((e as Error).message ?? "load failed");
      }
    })();
    return () => {
      dead = true;
    };
  }, [provider]);

  const filteredInstances = useMemo(() => {
    if (selectedStrategy === "all") return instances;
    return instances.filter(x => x.strategy_key === selectedStrategy || x.strategy_group === selectedStrategy);
  }, [instances, selectedStrategy]);

  const running = filteredInstances.filter((x) => (x.runtime_status ?? x.status) === "running").length;
  const stale = filteredInstances.filter((x) => (x.runtime_status ?? x.status) === "stale").length;

  const groupSummary = useMemo(() => {
    const m = new Map<string, { total: number; running: number }>();
    strategies.forEach((s) => {
      const key = s.strategy_group || "ungrouped";
      const cur = m.get(key) ?? { total: 0, running: 0 };
      cur.total += 1;
      cur.running += Number(s.running_instances ?? 0);
      m.set(key, cur);
    });
    return Array.from(m.entries()).map(([group, v]) => ({ group, ...v }));
  }, [strategies]);

  const equity = accounts.reduce((acc, x) => acc + Number(x.equity_total ?? 0), 0);

  return (
    <PageFrame title="策略总览" desc="全局策略分类、策略实例运行态、账户总览">
      <div className="grid">
        {error && <div className="card error">{error}</div>}
        <section className="grid cols-4">
          <div className="card">
            <div className="kpi-value">{fmtInt(strategies.length)}</div>
            <div className="kpi-label">策略总数</div>
          </div>
          <div className="card">
            <div className="kpi-value">{fmtInt(filteredInstances.length)}</div>
            <div className="kpi-label">当前视图实例</div>
          </div>
          <div className="card">
            <div className="kpi-value">{fmtInt(running)}</div>
            <div className="kpi-label">运行中实例</div>
          </div>
          <div className="card">
            <div className="kpi-value">{fmtCurrency(equity)}</div>
            <div className="kpi-label">账户权益合计</div>
          </div>
        </section>

        <section className="grid cols-2">
          <div className="card">
            <h3>策略分组</h3>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Group</th>
                    <th>Total</th>
                    <th>Running Instances</th>
                  </tr>
                </thead>
                <tbody>
                  {groupSummary.map((x) => (
                    <tr key={x.group}>
                      <td>{x.group}</td>
                      <td>{fmtInt(x.total)}</td>
                      <td>{fmtInt(x.running)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="card">
            <div className="row wrap" style={{ justifyContent: "space-between", marginBottom: 10 }}>
              <h3>实例热点</h3>
              <select
                value={selectedStrategy}
                onChange={(e) => setSelectedStrategy(e.target.value)}
                style={{ padding: "4px 8px", borderRadius: "4px", border: "1px solid var(--border)", background: "var(--surface)", color: "var(--fg)" }}
              >
                <option value="all">所有策略 (All)</option>
                {strategies.map((s) => (
                  <option key={s.strategy_key} value={s.strategy_key}>
                    {s.strategy_name || s.strategy_key}
                  </option>
                ))}
              </select>
            </div>
            <div className="row wrap" style={{ marginBottom: 10 }}>
              <span className="badge running">running: {running}</span>
              <span className="badge stale">stale: {stale}</span>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Instance</th>
                    <th>Strategy</th>
                    <th>Status</th>
                    <th>PnL</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredInstances.slice(0, 10).map((item) => {
                    const status = item.runtime_status ?? (item.status as "running" | "stopped" | "error" | "stale");
                    return (
                      <tr key={item.instance_id}>
                        <td>
                          <Link to={`/instances/${item.instance_id}`}>{item.label || item.instance_id}</Link>
                        </td>
                        <td>
                          <Link to={`/strategies/${item.strategy_key}`}>{item.strategy_key}</Link>
                        </td>
                        <td>
                          <span className={`badge ${status}`}>{status}</span>
                        </td>
                        <td>{fmtCurrency(item.last_pnl)}</td>
                      </tr>
                    );
                  })}
                  {filteredInstances.length === 0 && (
                    <tr>
                      <td colSpan={4} style={{ textAlign: "center" }}>无匹配实例</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </section>
      </div>
    </PageFrame>
  );
}
