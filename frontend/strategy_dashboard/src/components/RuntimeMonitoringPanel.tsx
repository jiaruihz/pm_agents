import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import type { RuntimeMonitorMetric, RuntimeMonitorResponse, RuntimeMonitorRuntime } from "../data/runtime-monitor-types";

const API_BASE = `${import.meta.env.VITE_WEATHER_API ?? ""}/api`;
const POLL_MS = 30_000;

function formatAge(seconds: number | null): string {
  if (seconds == null) return "未知";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(1)} 分钟`;
  return `${(seconds / 3600).toFixed(1)} 小时`;
}

function formatTime(value: string | null): string {
  if (!value) return "未知";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatMetric(metric: RuntimeMonitorMetric): string {
  if (metric.value == null) return "未知";
  const number = typeof metric.value === "number" ? metric.value.toLocaleString(undefined, { maximumFractionDigits: 3 }) : metric.value;
  return `${number}${metric.unit ? ` ${metric.unit}` : ""}`;
}

function tone(status: string, sourceStatus: RuntimeMonitorResponse["source_status"]): string {
  if (sourceStatus === "stale" || sourceStatus === "unavailable") return "var(--bad)";
  if (["critical", "error", "failed", "unhealthy", "down", "stale"].includes(status.toLowerCase())) return "var(--bad)";
  if (["unknown", "paused", "degraded", "warning"].includes(status.toLowerCase())) return "var(--warn)";
  if (["running", "healthy", "fresh", "observed", "ok"].includes(status.toLowerCase())) return "var(--ok)";
  return "var(--muted)";
}

function needsAttention(runtime: RuntimeMonitorRuntime, sourceStatus: RuntimeMonitorResponse["source_status"]): boolean {
  if (sourceStatus !== "fresh") return true;
  if (runtime.status === "paused" && runtime.present === false) return false;
  return !["running", "healthy"].includes(runtime.status) || runtime.present !== true || runtime.business.status === "stale" || runtime.business.status === "unknown" || runtime.issues.length > 0 || ["business_error", "business_blocked"].includes(runtime.diagnosis.code);
}

function MetricList({ metrics }: { metrics: RuntimeMonitorMetric[] }) {
  if (!metrics.length) return <span style={{ color: "var(--muted)" }}>未提供业务指标（未知不等于 0）</span>;
  return <>{[...new Set(metrics.map((m) => m.scope))].map((scope) => <div key={scope}><small style={{ color: "var(--muted)" }}>{scope}</small><div style={metricListStyle}>{metrics.filter((m) => m.scope === scope).map((metric) => <div key={`${metric.key}:${metric.scope}`} style={metricStyle} title={metric.scope}>
    <span style={{ color: "var(--muted)" }}>{metric.label}</span><strong>{formatMetric(metric)}</strong>
  </div>)}</div></div>)}</>;
}

export function RuntimeMonitoringPanel(): JSX.Element {
  const [data, setData] = useState<RuntimeMonitorResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [role, setRole] = useState("all");
  const [status, setStatus] = useState("all");
  const [search, setSearch] = useState("");
  const [onlyAttention, setOnlyAttention] = useState(false);
  const requestRef = useRef<AbortController | null>(null);
  const requestVersion = useRef(0);
  const mounted = useRef(true);
  const receivedAt = useRef(Date.now());
  const [clock, setClock] = useState(Date.now());
  const sourceStatus: RuntimeMonitorResponse["source_status"] = data?.source_status === "fresh" &&
    (data.source_age_sec == null || data.source_age_sec + (clock - receivedAt.current) / 1000 > data.refresh_budget_sec)
    ? "stale" : data?.source_status ?? "unavailable";

  const load = useCallback(async () => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    const version = ++requestVersion.current;
    let timedOut = false;
    const timeout = window.setTimeout(() => { timedOut = true; controller.abort(); }, 15_000);
    setLoading(true);
    try {
      const response = await fetch(`${API_BASE}/platform/monitor`, { headers: { Accept: "application/json" }, signal: controller.signal });
      if (!response.ok) throw new Error(`${response.status} /platform/monitor: ${await response.text().catch(() => response.statusText)}`);
      const value = await response.json() as RuntimeMonitorResponse;
      if (!mounted.current || version !== requestVersion.current) return;
      receivedAt.current = Date.now();
      setClock(receivedAt.current);
      setData(value);
      setError(null);
    } catch (cause) {
      if ((controller.signal.aborted && !timedOut) || !mounted.current || version !== requestVersion.current) return;
      setData(null);
      setError(timedOut ? "监控请求超过 15 秒" : cause instanceof Error ? cause.message : "业务监控接口不可用");
    } finally {
      window.clearTimeout(timeout);
      if (mounted.current && version === requestVersion.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 5_000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    mounted.current = true;
    void load();
    return () => { mounted.current = false; requestRef.current?.abort(); };
  }, [load]);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const timer = window.setInterval(() => { void load(); }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [autoRefresh, load]);

  const roles = useMemo(() => [...new Set(data?.runtimes.map((item) => item.role) ?? [])].sort(), [data]);
  const statuses = useMemo(() => [...new Set(data?.runtimes.map((item) => item.status) ?? [])].sort(), [data]);
  const businessCounts = useMemo(() => {
    const counts: Record<string, number> = { observed: 0, unknown: 0, stale: 0, not_applicable: 0 };
    data?.runtimes.forEach((runtime) => { counts[runtime.business.status] = (counts[runtime.business.status] ?? 0) + 1; });
    return counts;
  }, [data]);
  const runtimes = useMemo(() => {
    if (!data) return [];
    const query = search.trim().toLowerCase();
    return data.runtimes.filter((item) => {
      const matchesText = !query || [item.instance_id, item.role, item.execution_mode, item.status, item.diagnosis.label, item.diagnosis.detail, ...item.issues, ...item.business.blockers].join(" ").toLowerCase().includes(query);
      return (role === "all" || item.role === role) && (status === "all" || item.status === status) && matchesText && (!onlyAttention || needsAttention(item, sourceStatus));
    }).sort((a, b) => Number(needsAttention(b, sourceStatus)) - Number(needsAttention(a, sourceStatus)) || a.instance_id.localeCompare(b.instance_id));
  }, [data, onlyAttention, role, search, status, sourceStatus]);

  return <section style={panelStyle} aria-label="业务监控总览">
    <div style={headingStyle}>
      <div><h2 style={{ margin: 0 }}>业务监控总览</h2><div style={{ color: "var(--muted)", fontSize: 12, marginTop: 4 }}>进程在跑不等于业务正常；未成交不等于故障；未知不按 0 处理。</div></div>
      <div style={controlsStyle}><label><input type="checkbox" checked={autoRefresh} onChange={(event) => setAutoRefresh(event.target.checked)} /> 自动 30 秒刷新</label><button onClick={() => void load()} disabled={loading}>{loading ? "读取中…" : "刷新"}</button></div>
    </div>
    {error && <div role="alert" style={errorStyle}>监控接口不可用：{error}。已清除旧数据，不能据此判断运行状态。</div>}
    {data && <>
      {sourceStatus !== "fresh" && <div role="alert" style={errorStyle}>来源{sourceStatus === "stale" ? "陈旧" : "不可用"}：所有旧的绿色状态已失效。{data.source_error ? ` ${data.source_error}` : ""}</div>}
      <div style={summaryGridStyle}>
        <Summary label="实例" value={data.summary.total} /><Summary label="运行中" value={data.summary.running} tone={tone("running", sourceStatus)} /><Summary label="暂停" value={data.summary.paused} /><Summary label="严重" value={data.summary.critical} tone={tone("critical", sourceStatus)} /><Summary label="未知" value={data.summary.unknown} tone={tone("unknown", sourceStatus)} /><Summary label="业务已观测" value={data.summary.business_observed} />
      </div>
      <div style={sourceStyle}>业务阶段：已观测 {businessCounts.observed} · 未知 {businessCounts.unknown} · 陈旧 {businessCounts.stale} · 不适用 {businessCounts.not_applicable}</div>
      <div style={sourceStyle}>监控采样 {formatTime(data.observed_at_utc)} · 来源采样 {formatTime(data.source_observed_at_utc)} · 来源年龄 {formatAge(data.source_age_sec == null ? null : data.source_age_sec + Math.max(0, clock - receivedAt.current) / 1000)} / 预算 {formatAge(data.refresh_budget_sec)}</div>
      <div style={moduleGridStyle}>{data.modules.map((module) => <article key={module.id} style={moduleStyle}><div style={labelRowStyle}><strong>{module.label}</strong><StatusBadge value={module.status} sourceStatus={sourceStatus} /></div><div style={detailStyle}>{module.detail || "未提供详情"}</div><small style={{ color: "var(--muted)", display: "block" }}>观测：{formatTime(module.observed_at_utc)}</small>{module.metrics.length > 0 && <MetricList metrics={module.metrics} />}</article>)}</div>
      <div style={filterStyle}><label>角色 <select value={role} onChange={(event) => setRole(event.target.value)}><option value="all">全部</option>{roles.map((value) => <option key={value} value={value}>{value}</option>)}</select></label><label>状态 <select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">全部</option>{statuses.map((value) => <option key={value} value={value}>{value}</option>)}</select></label><label>搜索 <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="实例、诊断、阻塞原因" /></label><label><input type="checkbox" checked={onlyAttention} onChange={(event) => setOnlyAttention(event.target.checked)} /> 只看需关注</label></div>
      <div style={tableWrapStyle}><table style={tableStyle}><thead><tr><th>实例 / 执行</th><th>运行状态</th><th>心跳 / 依赖</th><th>业务阶段</th><th>诊断与阻塞</th></tr></thead><tbody>{runtimes.map((runtime) => <RuntimeRow key={runtime.instance_id} runtime={runtime} sourceStatus={sourceStatus} />)}</tbody></table></div>
      {runtimes.length === 0 && <div style={{ color: "var(--muted)", padding: 12 }}>没有匹配实例；这不是实例数为 0 的证明。</div>}
    </>}
  </section>;
}

function Summary({ label, value, tone: color }: { label: string; value: number; tone?: string }) { return <div style={summaryStyle}><span>{label}</span><strong style={{ color }}>{value}</strong></div>; }
function StatusBadge({ value, sourceStatus }: { value: string; sourceStatus: RuntimeMonitorResponse["source_status"] }) { return <span style={{ color: tone(value, sourceStatus), fontWeight: 700, whiteSpace: "nowrap" }}>{sourceStatus === "fresh" ? value : `${value} · ${sourceStatus}`}</span>; }
function RuntimeRow({ runtime, sourceStatus }: { runtime: RuntimeMonitorRuntime; sourceStatus: RuntimeMonitorResponse["source_status"] }) {
  return <tr><td><strong>{runtime.instance_id}</strong><br /><small>{runtime.role} · {runtime.execution_mode} · desired: {runtime.desired_state}</small></td><td><StatusBadge value={runtime.status} sourceStatus={sourceStatus} /><br /><small>present: {runtime.present === true ? "是" : runtime.present === false ? "否" : "未知"}</small></td><td>采样时心跳年龄: {formatAge(runtime.health_age_sec)} / {formatAge(runtime.health_budget_sec)}<br /><small>依赖：{runtime.dependencies.length ? runtime.dependencies.join("、") : "未声明"}</small></td><td><StatusBadge value={runtime.business.status} sourceStatus={sourceStatus} /><br /><small>{runtime.business.scope || "范围未知"} · {formatTime(runtime.business.observed_at_utc)}</small><small>来源：{runtime.business.source_ref ?? "未提供"}</small><MetricList metrics={runtime.business.metrics} /></td><td><strong>{runtime.diagnosis.label || runtime.diagnosis.code}</strong><br /><small>{runtime.diagnosis.detail || "未提供诊断详情"}</small>{[...runtime.issues, ...runtime.business.blockers].length > 0 && <div style={blockerStyle}>原因 / 原生状态：{[...runtime.issues, ...runtime.business.blockers].join("；")}</div>}</td></tr>;
}

const panelStyle: CSSProperties = { padding: 16, border: "1px solid var(--stroke)", borderRadius: 12, background: "var(--card)", marginBottom: 16, overflow: "hidden" };
const headingStyle: CSSProperties = { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, flexWrap: "wrap" };
const controlsStyle: CSSProperties = { display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", fontSize: 12 };
const errorStyle: CSSProperties = { marginTop: 12, padding: 10, borderRadius: 8, background: "rgba(191, 45, 63, .13)", color: "var(--bad)", overflowWrap: "anywhere" };
const summaryGridStyle: CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(105px, 1fr))", gap: 8, marginTop: 14 };
const summaryStyle: CSSProperties = { padding: 10, border: "1px solid var(--stroke)", borderRadius: 8, display: "grid", gap: 3, fontSize: 12 };
const sourceStyle: CSSProperties = { color: "var(--muted)", fontSize: 12, marginTop: 10, overflowWrap: "anywhere" };
const moduleGridStyle: CSSProperties = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10, marginTop: 14 };
const moduleStyle: CSSProperties = { padding: 11, border: "1px solid var(--stroke)", borderRadius: 8, minWidth: 0 };
const labelRowStyle: CSSProperties = { display: "flex", gap: 8, justifyContent: "space-between", alignItems: "baseline" };
const detailStyle: CSSProperties = { fontSize: 12, margin: "7px 0", overflowWrap: "anywhere", maxHeight: 76, overflowY: "auto" };
const metricListStyle: CSSProperties = { display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 };
const metricStyle: CSSProperties = { display: "grid", gap: 2, padding: "4px 6px", borderRadius: 5, background: "rgba(31, 39, 53, .05)", fontSize: 11, minWidth: 76 };
const filterStyle: CSSProperties = { display: "flex", gap: 10, alignItems: "end", flexWrap: "wrap", marginTop: 16, paddingTop: 12, borderTop: "1px solid var(--stroke)", fontSize: 12 };
const tableWrapStyle: CSSProperties = { overflowX: "auto", marginTop: 12, WebkitOverflowScrolling: "touch" };
const tableStyle: CSSProperties = { minWidth: 1050, width: "100%", fontSize: 12 };
const blockerStyle: CSSProperties = { marginTop: 7, color: "var(--bad)", fontSize: 12, overflowWrap: "anywhere" };
