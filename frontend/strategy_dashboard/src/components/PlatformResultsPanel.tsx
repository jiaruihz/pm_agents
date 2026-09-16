import { useEffect, useState } from "react";
import { weatherApi } from "../data/weather-http";

export interface PlatformRun {
  run_id: string; strategy_version_id: string | null; config_version_id: string | null;
  dataset_snapshot_id: string; created_at_utc: string; metric_count: number;
}
export interface PlatformMetric {
  metric_key: string; value: string | null; unit: string; status: string;
  grain: string; formula_id: string; denominator_ref: string | null;
  metric_profile: string; source_refs: string[]; quality_flags: string[];
  as_of_utc: string; dimensions: [string, string][];
}
export interface PlatformDetail {
  run: PlatformRun; metrics: PlatformMetric[]; records: unknown[];
}

/** Independent result reader: a canonical/JRS outage must not hide this panel. */
export function PlatformResultsPanel() {
  const [runs, setRuns] = useState<PlatformRun[]>([]);
  const [evidence, setEvidence] = useState<unknown[]>([]);
  const [detail, setDetail] = useState<PlatformDetail | null>(null);
  const [selected, setSelected] = useState("");
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let active = true;
    setLoading(true); setError(null); setSelected(""); setDetail(null);
    Promise.all([weatherApi.listPlatformRuns(offset), weatherApi.listPlatformEvidence(offset)])
      .then(([rows, records]) => { if (active) { setRuns(rows); setEvidence(records); } })
      .catch((e: Error) => { if (active) { setError(e.message); setRuns([]); setEvidence([]); } })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [offset]);
  useEffect(() => {
    let active = true;
    setDetail(null); setDetailError(null);
    if (selected) weatherApi.getPlatformRun(selected)
      .then(value => { if (active) setDetail(value); })
      .catch((e: Error) => { if (active) setDetailError(e.message); });
    return () => { active = false; };
  }, [selected]);
  return <section className="panel" style={{ padding: 16, marginBottom: 24 }}>
    <h2>统一结果索引 · 只读</h2>
    <p>Weather 历史、PM Barrier shadow 与 Freqtrade backtest 保留各自指标口径；缺失值不是 0。</p>
    {loading && <p role="status">读取控制库…</p>}
    {error && <p role="alert">平台索引不可用（不代表策略停止）：{error}</p>}
    {!error && !loading && <>
      <div style={{ overflowX: "auto" }}><table><thead><tr>
        <th>Run / 查看指标</th><th>Strategy version</th><th>Config version</th><th>Dataset</th><th>指标数</th>
      </tr></thead><tbody>{runs.map(run => <tr key={run.run_id}>
        <td><button onClick={() => setSelected(run.run_id)}>{run.run_id}</button></td>
        <td>{run.strategy_version_id ?? "历史绑定缺失"}</td><td>{run.config_version_id ?? "历史绑定缺失"}</td>
        <td>{run.dataset_snapshot_id}</td><td>{run.metric_count}</td>
      </tr>)}</tbody></table></div>
      {runs.length === 0 && <p>这一页没有已入库 run；未映射来源见下方证据。</p>}
      <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>上一页</button>
      <button disabled={runs.length < 25 && evidence.length < 25} onClick={() => setOffset(offset + 25)}>下一页</button>
      {selected && !detail && !detailError && <p role="status">读取指标…</p>}
      {detailError && <p role="alert">{detailError}</p>}
      {detail && <div style={{ overflowX: "auto" }}><h3>{selected}</h3><table><thead><tr>
        <th>Metric / profile</th><th>值 / 单位</th><th>状态 / 缺口</th><th>口径 / 分母</th><th>来源 / as-of</th>
      </tr></thead><tbody>{detail.metrics.map((metric, index) => <tr key={index}>
        <td>{metric.metric_key}<br/>{metric.metric_profile}</td>
        <td>{metric.value ?? "—（无可计算值）"} {metric.unit}</td>
        <td>{metric.status}<br/>{metric.quality_flags.join(", ")}</td>
        <td>{metric.grain} / {metric.formula_id}<br/>{metric.denominator_ref ?? "未声明分母"}<br/>{JSON.stringify(metric.dimensions)}</td>
        <td>{metric.source_refs.join(", ")}<br/>{metric.as_of_utc}</td>
      </tr>)}</tbody></table><details><summary>原始血缘记录</summary><pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(detail.records, null, 2)}</pre></details></div>}
      <details><summary>导入验收与未映射来源（本页 {evidence.length} 条）</summary>
        <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(evidence, null, 2)}</pre>
      </details>
    </>}
  </section>;
}
