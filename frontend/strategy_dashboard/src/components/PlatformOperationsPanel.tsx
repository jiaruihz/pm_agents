import { useEffect, useState } from "react";
import {
  weatherApi,
  type PlatformControllerResult,
  type PlatformControllerResultDetail,
  type PlatformIncident,
  type PlatformIncidentDetail,
  type PlatformOperation,
  type PlatformOperationDetail,
  type PlatformHealth,
} from "../data/weather-http";

type Tab = "operations" | "incidents" | "controller";
type Detail = PlatformOperationDetail | PlatformIncidentDetail | PlatformControllerResultDetail;

function RawJson({ value }: { value: unknown }) {
  return <details><summary>原始 JSON</summary><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(value, null, 2)}</pre></details>;
}

function StageTimings({ items }: { items: unknown[] }) {
  if (!items.length) return <p>没有阶段耗时。</p>;
  return <table style={{ minWidth: 0, width: "100%" }}><thead><tr><th>阶段</th><th>耗时</th></tr></thead><tbody>{items.map((item, index) => {
    const value = item as Record<string, unknown>;
    const duration = value.duration_sec !== undefined ? `${String(value.duration_sec)} 秒` : value.elapsed_ms !== undefined ? `${String(value.elapsed_ms)} 毫秒` : "—";
    return <tr key={index}><td>{String(value.stage ?? "—")}</td><td>{duration}</td></tr>;
  })}</tbody></table>;
}

function TimelineTable({ items }: { items: unknown[] }) {
  return <table style={{ minWidth: 0, width: "100%" }}><thead><tr><th>时间</th><th>事件</th><th>状态</th></tr></thead><tbody>{items.map((item, index) => {
    const value = item as Record<string, unknown>;
    return <tr key={index}><td>{String(value.occurred_at_utc ?? "—")}</td><td>{String(value.event_kind ?? value.kind ?? "—")}</td><td>{String(value.to_state ?? value.step_state ?? value.outcome ?? "—")}</td></tr>;
  })}</tbody></table>;
}

function ControllerMode({ value }: { value: PlatformControllerResult["apply"] }) {
  return <>{value === true ? "apply" : value === false ? "仅计划 / 非 apply" : "执行标记未知"}</>;
}

/** 只读 evidence 浏览：不执行 controller，也不更改 ledger。 */
export function PlatformOperationsPanel() {
  const [health, setHealth] = useState<PlatformHealth | null>(null);
  const [healthError, setHealthError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("operations");
  const [offset, setOffset] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [rows, setRows] = useState<(PlatformOperation | PlatformIncident | PlatformControllerResult)[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  const clearSelection = () => {
    setSelected(""); setDetail(null); setDetailError(null);
    setRows([]); setLoading(true); setError(null);
  };
  const selectTab = (next: Tab) => {
    clearSelection(); setTab(next); setOffset(0);
  };
  useEffect(() => {
    let active = true;
    setHealth(null); setHealthError(null);
    weatherApi.getPlatformHealth().then(value => { if (active) setHealth(value); })
      .catch((e: Error) => { if (active) setHealthError(e.message); });
    return () => { active = false; };
  }, [refresh]);
  useEffect(() => {
    let active = true;
    setError(null); setLoading(true);
    const load = tab === "operations" ? weatherApi.listPlatformOperations : tab === "incidents" ? weatherApi.listPlatformIncidents : weatherApi.listPlatformControllerResults;
    load(offset).then(value => { if (active) setRows(value); }).catch((e: Error) => { if (active) { setRows([]); setError(e.message); } }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [tab, offset, refresh]);
  useEffect(() => {
    let active = true;
    setDetail(null); setDetailError(null);
    if (!selected) return () => { active = false; };
    const load = tab === "operations" ? weatherApi.getPlatformOperation : tab === "incidents" ? weatherApi.getPlatformIncident : weatherApi.getPlatformControllerResult;
    load(selected).then(value => { if (active) setDetail(value); }).catch((e: Error) => { if (active) setDetailError(e.message); });
    return () => { active = false; };
  }, [selected, tab]);
  return <section className="panel" style={{ padding: 16, maxWidth: "100%", overflowX: "auto", overflowWrap: "anywhere" }}>
    <h2>Operations · 只读证据</h2><p>控制库与 Weather canonical 独立；无执行、无后台轮询。</p>
    {healthError && <p role="alert">原生自愈观测不可用：{healthError}</p>}
    {health && <div><h3>原生 supervisor · 最后入库观测</h3>{health.native_reliability ? <>
      <p>状态：{health.native_reliability.status}；观测时间：{health.native_reliability.observed_at_utc}。这不是实时健康保证。</p>
      <p>活动事故：{health.native_reliability.active_incidents?.length ?? 0}；待确认：{health.native_reliability.pending_incidents?.length ?? 0}。修复动作不等于恢复验收通过。</p>
      <RawJson value={health.native_reliability} />
    </> : <p>尚未接入原生 supervisor 观测；不能据此判断链路健康。</p>}</div>}
    <p style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>{([ ["operations", "操作"], ["incidents", "事故"], ["controller", "Controller 记录"] ] as [Tab, string][]).map(([key, label]) => <button key={key} aria-pressed={tab === key} style={tab === key ? { background: "var(--accent-2)", color: "white" } : undefined} onClick={() => { if (tab !== key) selectTab(key); }}>{label}</button>)}</p>
    <button onClick={() => { clearSelection(); setRefresh(value => value + 1); }}>手动刷新</button>
    {error && <p role="alert">控制库不可用（不代表策略停止）：{error}</p>}
    {loading ? <p>正在读取控制库…</p> : !error && <><div style={{ overflowX: "auto" }}><OperationTable tab={tab} rows={rows} onSelect={setSelected} /></div>
      {rows.length === 0 && <p>{tab === "incidents" ? "没有事故记录。" : tab === "controller" ? "没有 Controller 记录。" : "没有 operation ledger 记录。"}</p>}
      <button disabled={!offset} onClick={() => { clearSelection(); setOffset(Math.max(0, offset - 25)); }}>上一页</button>{" "}
      <button disabled={rows.length < 25} onClick={() => { clearSelection(); setOffset(offset + 25); }}>下一页</button></>}
    {detailError && <p role="alert">{detailError}</p>}{detail && <DetailView detail={detail} />}
  </section>;
}

function OperationTable({ tab, rows, onSelect }: { tab: Tab; rows: (PlatformOperation | PlatformIncident | PlatformControllerResult)[]; onSelect: (id: string) => void }) {
  if (tab === "operations") return <table><thead><tr><th>操作</th><th>目标 / 状态</th><th>时间</th><th>原因</th></tr></thead><tbody>{(rows as PlatformOperation[]).map(row => <tr key={row.operation_id}><td><button onClick={() => onSelect(row.operation_id)}>{row.operation_type}</button></td><td>{row.target_id ?? "未绑定"}<br/>{row.state}</td><td>{row.updated_at_utc}</td><td>{row.error_code ?? "—"}</td></tr>)}</tbody></table>;
  if (tab === "incidents") return <table><thead><tr><th>事故</th><th>领域 / 状态</th><th>时间</th><th>原因</th></tr></thead><tbody>{(rows as PlatformIncident[]).map(row => <tr key={row.incident_id}><td><button onClick={() => onSelect(row.incident_id)}>{row.incident_id.slice(0, 14)}</button></td><td>{row.failure_domain}<br/>{row.state}</td><td>{row.last_event_at_utc}</td><td>{row.root_cause_key}</td></tr>)}</tbody></table>;
  return <table><thead><tr><th>命令 / 计划</th><th>目标 / 模式</th><th>来源观测时间</th><th>结果</th></tr></thead><tbody>{(rows as PlatformControllerResult[]).map(row => <tr key={row.record_id}><td><button onClick={() => onSelect(row.record_id)}>{row.command ?? "未知"}</button><br/><small>plan: {row.controller_operation_id ?? "未绑定"}</small></td><td>{row.target_instance ?? "未绑定"}<br/><ControllerMode value={row.apply} /></td><td>{row.source_observed_at_utc ?? "未知"}</td><td>{row.status ?? "未声明"}</td></tr>)}</tbody></table>;
}

function DetailView({ detail }: { detail: Detail }) {
  if ("source_observed_at_utc" in detail) return <div><h3>Controller 记录</h3><p>来源：{detail.source_ref ?? "未知"}；SHA：{detail.source_sha256 ?? "未知"}</p><p>来源观测：{detail.source_observed_at_utc ?? "未知"}；总耗时：{detail.duration_sec ?? "未知"} 秒。action.started 不代表整体成功。</p><h4>阶段耗时</h4><StageTimings items={[...detail.operation_stage_timings, ...detail.controller_stage_timings]} /><h4>postcondition / scoped 验证</h4><RawJson value={{ postcondition: detail.operation_postcondition, scoped_manifest_delta: detail.scoped_manifest_delta, actions: detail.actions }} /><RawJson value={detail.source_output} /></div>;
  if ("impacts" in detail) return <div><h3>事故详情</h3><h4>时间线</h4><TimelineTable items={detail.timeline} /><RawJson value={detail.timeline} /><h4>影响</h4><RawJson value={detail.impacts} /><h4>原生修复证据</h4><RawJson value={detail.native_repairs ?? []} /></div>;
  return <div><h3>{detail.operation_id}</h3><p>状态：{detail.state}；失败：{detail.error_code ?? "—"}</p><p>实例：{detail.instance?.instance_key ?? "未投影"}；采样：{detail.instance?.observed_at_utc ?? "未知"}；desired SHA：{detail.instance?.desired_sha ?? "未知"}；loaded SHA：{detail.instance?.loaded_sha ?? "未知（不可用 desired SHA 替代）"}</p><h4>时间线</h4><TimelineTable items={detail.timeline} /><RawJson value={detail.timeline} /></div>;
}
