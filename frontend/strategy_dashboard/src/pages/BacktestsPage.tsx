import { useEffect, useMemo, useState } from "react";
import { PageFrame } from "../components/PageFrame";
import { useDashboardProvider } from "../data/provider-context";
import type { BacktestRunItem, BacktestTableRow } from "../data/types";
import { fmtCurrency, fmtNum, fmtPct } from "../utils/format";

export function BacktestsPage(): JSX.Element {
  const provider = useDashboardProvider();
  const [runs, setRuns] = useState<BacktestRunItem[]>([]);
  const [run, setRun] = useState("");
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<BacktestTableRow[]>([]);
  const [summary, setSummary] = useState<Record<string, unknown>>({});
  const [selectedScenario, setSelectedScenario] = useState("");
  const [scenario, setScenario] = useState<Record<string, unknown> | null>(null);
  const [ops, setOps] = useState<Record<string, unknown> | null>(null);
  const [supervisor, setSupervisor] = useState<Record<string, unknown> | null>(null);
  const [logs, setLogs] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");

  async function reloadStatic(): Promise<void> {
    try {
      const [r, op, sp] = await Promise.all([
        provider.listBacktestRuns(),
        provider.getOpsStatus(),
        provider.getSupervisorSessions(20),
      ]);
      setRuns(r);
      if (!run && r[0]?.name) setRun(r[0].name);
      setOps(op);
      setSupervisor(sp);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  useEffect(() => {
    reloadStatic();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!run) return;
    provider
      .getBacktestTable(run, q)
      .then((res) => {
        setRows(res.rows ?? []);
        setSummary(res.summary ?? {});
        setError("");
      })
      .catch((e) => setError((e as Error).message));
  }, [provider, run, q]);

  useEffect(() => {
    if (!run || !selectedScenario) return;
    provider
      .getBacktestScenario(run, selectedScenario)
      .then((res) => setScenario(res))
      .catch((e) => setError((e as Error).message));
  }, [provider, run, selectedScenario]);

  async function loadLogs(): Promise<void> {
    try {
      const name = String(ops?.latest_log ?? "");
      const res = await provider.getOpsLogs(name, 120);
      setLogs(res);
    } catch (e) {
      setError((e as Error).message);
    }
  }

  const topRows = useMemo(() => rows.slice(0, 40), [rows]);

  return (
    <PageFrame title="Backtests + Ops" desc="回测结果、运维状态、监督会话">
      <div className="grid">
        {error && <div className="card error">{error}</div>}

        <section className="card">
          <div className="row wrap" style={{ marginBottom: 10 }}>
            <label className="mono">run</label>
            <select value={run} onChange={(e) => setRun(e.target.value)}>
              <option value="">请选择</option>
              {runs.map((r) => (
                <option key={r.name} value={r.name}>
                  {r.name} ({r.type}/{r.rows})
                </option>
              ))}
            </select>
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="关键词过滤" />
            <button onClick={reloadStatic}>刷新运行信息</button>
            <button onClick={loadLogs}>加载日志</button>
          </div>
          <div className="row wrap">
            <span className="badge running">rows: {rows.length}</span>
            <span className="badge stopped">avg pnl: {fmtNum(summary.avg_pnl_end, 4)}</span>
            <span className="badge stopped">avg mdd: {fmtNum(summary.avg_max_drawdown, 4)}</span>
          </div>
        </section>

        <section className="card">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Scenario</th>
                  <th>Profile</th>
                  <th>Strategy</th>
                  <th>PnL</th>
                  <th>MDD</th>
                  <th>Fill Rate</th>
                </tr>
              </thead>
              <tbody>
                {topRows.map((row) => (
                  <tr key={row.scenario_run_id} onClick={() => setSelectedScenario(row.scenario_run_id)}>
                    <td>{row.scenario_id}</td>
                    <td>{row.profile_name || "-"}</td>
                    <td>{row.strategy_key}</td>
                    <td>{fmtCurrency(row.pnl_end)}</td>
                    <td>{fmtNum(row.max_drawdown, 5)}</td>
                    <td>{fmtPct(row.fill_rate_per_order, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="grid cols-2">
          <div className="card">
            <h3>场景详情</h3>
            <pre>{JSON.stringify(scenario, null, 2)}</pre>
          </div>
          <div className="card">
            <h3>Ops 状态</h3>
            <pre>{JSON.stringify(ops, null, 2)}</pre>
          </div>
        </section>

        <section className="grid cols-2">
          <div className="card">
            <h3>Supervisor</h3>
            <pre>{JSON.stringify(supervisor, null, 2)}</pre>
          </div>
          <div className="card">
            <h3>日志</h3>
            <pre>{JSON.stringify(logs, null, 2)}</pre>
          </div>
        </section>
      </div>
    </PageFrame>
  );
}
