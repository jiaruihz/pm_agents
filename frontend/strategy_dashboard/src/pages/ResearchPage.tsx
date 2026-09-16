import { useEffect, useState } from "react";
import { PageFrame } from "../components/PageFrame";
import { useDashboardProvider } from "../data/provider-context";
import type { ResearchMarketItem } from "../data/types";

const ACTIONS = ["filter", "parse", "prompt", "run_all"] as const;

export function ResearchPage(): JSX.Element {
  const provider = useDashboardProvider();
  const [items, setItems] = useState<ResearchMarketItem[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [actionResult, setActionResult] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let dead = false;
    provider
      .listResearchMarkets(1, 40)
      .then((res) => {
        if (dead) return;
        setItems(res.items);
        if (res.items[0]?.market_id) setSelected(res.items[0].market_id);
        setError("");
      })
      .catch((e) => {
        if (!dead) setError((e as Error).message);
      });
    return () => {
      dead = true;
    };
  }, [provider]);

  useEffect(() => {
    if (!selected) return;
    let dead = false;
    provider
      .getResearchMarket(selected)
      .then((d) => {
        if (!dead) setDetail(d);
      })
      .catch((e) => {
        if (!dead) setError((e as Error).message);
      });
    return () => {
      dead = true;
    };
  }, [provider, selected]);

  async function runAction(action: (typeof ACTIONS)[number]): Promise<void> {
    try {
      const res = await provider.runResearchAction(action, { market_id: selected });
      setActionResult(res);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <PageFrame title="Research" desc="规则律师信息面板（同步 action，非任务化）">
      <div className="grid">
        {error && <div className="card error">{error}</div>}
        <section className="card">
          <div className="row wrap" style={{ marginBottom: 10 }}>
            <label className="mono">market</label>
            <select value={selected} onChange={(e) => setSelected(e.target.value)}>
              {items.map((item) => (
                <option key={item.market_id} value={item.market_id}>
                  {item.slug || item.market_id}
                </option>
              ))}
            </select>
            {ACTIONS.map((a) => (
              <button className={a === "run_all" ? "primary" : ""} key={a} onClick={() => runAction(a)}>
                {a}
              </button>
            ))}
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Market ID</th>
                  <th>Slug</th>
                  <th>Category</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item) => (
                  <tr key={item.market_id} onClick={() => setSelected(item.market_id)}>
                    <td>{item.market_id}</td>
                    <td>{item.slug}</td>
                    <td>{item.category}</td>
                    <td>{item.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="grid cols-2">
          <div className="card">
            <h3>市场详情</h3>
            <pre>{JSON.stringify(detail, null, 2)}</pre>
          </div>
          <div className="card">
            <h3>动作结果</h3>
            <pre>{JSON.stringify(actionResult, null, 2)}</pre>
          </div>
        </section>
      </div>
    </PageFrame>
  );
}
