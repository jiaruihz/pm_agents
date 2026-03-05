import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { PageFrame } from "../components/PageFrame";
import { useDashboardProvider } from "../data/provider-context";
import type { StrategyItem } from "../data/types";
import { fmtInt } from "../utils/format";

export function StrategiesPage(): JSX.Element {
  const provider = useDashboardProvider();
  const [items, setItems] = useState<StrategyItem[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let dead = false;
    provider
      .listStrategies(500)
      .then((rows) => {
        if (!dead) {
          setItems(rows);
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

  const groups = useMemo(() => {
    const map = new Map<string, StrategyItem[]>();
    items.forEach((item) => {
      const group = item.strategy_group || "ungrouped";
      const list = map.get(group) ?? [];
      list.push(item);
      map.set(group, list);
    });
    return Array.from(map.entries());
  }, [items]);

  return (
    <PageFrame title="策略目录" desc="按 strategy_group 分类浏览策略">
      <div className="grid">
        {error && <div className="card error">{error}</div>}
        {groups.map(([group, rows]) => (
          <section className="card" key={group}>
            <h3>{group}</h3>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Key</th>
                    <th>Name</th>
                    <th>Domain</th>
                    <th>Family</th>
                    <th>Running</th>
                    <th>Total</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((item) => (
                    <tr key={item.strategy_key}>
                      <td>
                        <Link to={`/strategies/${item.strategy_key}`}>{item.strategy_key}</Link>
                      </td>
                      <td>{item.strategy_name}</td>
                      <td>{item.domain || "-"}</td>
                      <td>{item.strategy_family || "-"}</td>
                      <td>{fmtInt(item.running_instances)}</td>
                      <td>{fmtInt(item.total_instances)}</td>
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
