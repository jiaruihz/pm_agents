import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { weatherApi } from "../../data/weather-http";
import type { ProbeHealthRow } from "../../data/v2-types";
import { statusToFreshness } from "../../components/v2/HealthDot";
import { FreshnessBadge } from "../../components/v2/FreshnessBadge";
import { VerdictLine } from "../../components/v2/VerdictLine";
import { EmptyState } from "../../components/v2/EmptyState";
import { GlossaryTerm } from "../../components/v2/GlossaryTerm";
import { auditZh, lifecycleZh, num } from "./format";

const POLL_MS = 30_000;

function isCurrentRunner(p: ProbeHealthRow): boolean {
  return p.process_status === "running" && p.heartbeat_age_min != null && p.heartbeat_age_min <= 20;
}

function verdict(p: ProbeHealthRow): { tone: "good" | "warn" | "bad" | "neutral"; text: string } {
  if (!isCurrentRunner(p))
    return { tone: "bad", text: `Supervisor 未在 20 分钟内确认心跳（当前 ${p.process_status}）；不计为在跑。` };
  if (p.freshness === "stale")
    return { tone: "warn", text: "行情快照偏旧，本轮决策可信度低；先确认采集是否断流再看候选。" };
  if ((p.candidate_rows ?? 0) === 0)
    return { tone: "neutral", text: `${lifecycleZh(p.lifecycle_status)}在跑，本轮未产候选（在等盘口/新鲜观测）。按执行质量评估，不看早期 PnL。` };
  return { tone: "good", text: `${lifecycleZh(p.lifecycle_status)}在跑，本轮 ${p.candidate_rows} 个候选、${num(p.execution_eligible)} 个计划。` };
}

function fmtParam(v: unknown): string {
  if (v === true) return "是";
  if (v === false) return "否";
  if (v == null || v === "") return "—";
  if (typeof v === "string" && v.includes("/") && v.length > 28) return "…/" + v.split("/").slice(-1)[0];
  return String(v);
}

/** Readable "参数与口径": runtime status + freshness + the probe's caps/thresholds. */
function ProbeParams({ p }: { p: ProbeHealthRow }) {
  const caps = (p.caps ?? {}) as Record<string, unknown>;
  // Show notional/window/threshold caps first; hide noisy artifact paths.
  const capEntries = Object.entries(caps).filter(([k]) => !k.endsWith("_artifact") && !k.endsWith("_model_artifact"));
  const runtime: [string, unknown][] = [
    ["status", p.status],
    ["process_status", p.process_status],
    ["snapshot_age_min", p.snapshot_age_min],
    ["heartbeat_age_min", p.heartbeat_age_min],
    ["health_status", p.health_status],
  ];
  return (
    <div className="param-block">
      <div className="param-caption">运行态</div>
      <table className="raw-table">
        <tbody>
          {runtime.map(([k, v]) => (
            <tr key={k}><td><GlossaryTerm field={k} /></td><td>{fmtParam(v)}</td></tr>
          ))}
        </tbody>
      </table>
      {capEntries.length > 0 && (
        <>
          <div className="param-caption">下单门槛与仓位上限（hover 看英文原名 / 口径）</div>
          <table className="raw-table">
            <tbody>
              {capEntries.map(([k, v]) => (
                <tr key={k}><td><GlossaryTerm field={k} /></td><td>{fmtParam(v)}</td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

function ProbeCard({ p }: { p: ProbeHealthRow }) {
  const [raw, setRaw] = useState(false);
  const v = verdict(p);
  return (
    <div className="card probe-card">
      <div className="probe-card-head">
        <Link to={`/probes/${encodeURIComponent(p.strategy_instance)}`} className="probe-name">
          {p.display_name || p.strategy_instance}
        </Link>
        <span className="badge" data-tone={statusToFreshness(p.lifecycle_status)}>
          {lifecycleZh(p.lifecycle_status)}
        </span>
      </div>

      <VerdictLine tone={v.tone}>{v.text}</VerdictLine>

      <FreshnessBadge
        heartbeatAgeMin={p.heartbeat_age_min}
        snapshotAgeMin={p.snapshot_age_min}
        snapshotTsUtc={p.snapshot_ts_utc}
      />

      <div className="probe-stats">
        <div className="stat">
          <span className="stat-label"><GlossaryTerm field="candidate_rows">候选数</GlossaryTerm></span>
          <span className="stat-value">{num(p.candidate_rows)}</span>
        </div>
        <div className="stat">
          <span className="stat-label"><GlossaryTerm field="execution_eligible">可执行</GlossaryTerm></span>
          <span className="stat-value">{num(p.execution_eligible)}</span>
        </div>
        <div className="stat">
          <span className="stat-label"><GlossaryTerm field="top_audit">主要拦截</GlossaryTerm></span>
          <span className="stat-value">{auditZh(p.top_audit)}</span>
        </div>
      </div>
      <p className="stat-caption">
        候选数 = 从盘口筛出的机会；可执行 = 再过新鲜度/仓位/穿价门槛后真正会下单的；主要拦截 = 本轮最多候选被挡下的原因。
      </p>

      <button className="raw-toggle" onClick={() => setRaw((x) => !x)}>
        {raw ? "收起参数与口径 ▲" : "看参数与口径 ▼"}
      </button>
      {raw && <ProbeParams p={p} />}
    </div>
  );
}

const GROUPS: { key: string; title: string; sub: string; match: (s: string | null) => boolean }[] = [
  { key: "running-live", title: "正在执行", sub: "Supervisor 已确认 running 的 tiny-live / live 实例", match: (s) => s === "__running_live__" },
  { key: "running-shadow", title: "正在观察", sub: "Supervisor 已确认 running 的 zero-notional shadow / monitor", match: (s) => s === "__running_shadow__" },
  { key: "attention", title: "未在跑 / 待处理", sub: "仍启用但 supervisor 未确认运行；不计入今日在跑", match: (s) => s === "__attention__" },
];

export function ProbesPage() {
  const [probes, setProbes] = useState<ProbeHealthRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = () =>
      weatherApi
        .getProbeHealth()
        .then((r) => alive && setProbes(r.probes))
        .catch((e) => alive && setError(String(e)));
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const assigned = new Set<string>();
  const grouped = GROUPS.map((g) => {
    const items = (probes ?? []).filter((p) => {
      const isRunning = isCurrentRunner(p);
      const isLive = ["live", "tiny_live_probe"].includes(p.lifecycle_status ?? "");
      const group = isRunning ? (isLive ? "__running_live__" : "__running_shadow__") : "__attention__";
      return g.match(group);
    });
    items.forEach((p) => assigned.add(p.strategy_instance));
    return { ...g, items };
  });
  const leftover = (probes ?? []).filter((p) => !assigned.has(p.strategy_instance));
  if (leftover.length) grouped.push({ key: "misc", title: "其他", sub: "", match: () => false, items: leftover });

  return (
    <div className="page">
      <header className="page-head">
        <h1>探针在跑</h1>
        <p className="page-sub">
          当前部署实例的健康与执行质量。<strong>状态来自 supervisor，不从历史镜像推断。</strong> 每 30 秒刷新。
        </p>
      </header>

      {error && <div className="error-banner">加载失败：{error}</div>}
      {probes == null && !error && <EmptyState message="加载中…" />}
      {probes != null && probes.length === 0 && (
        <EmptyState message="没有当前启用的探针实例" hint="请检查 strategy_instance 的部署状态。" />
      )}

      {grouped.map((g) => g.items.length > 0 && (
        g.key === "attention" ? (
          <details key={g.key} className="probe-group">
            <summary className="probe-group-head">
              <h2>{g.title} <span className="probe-group-count">{g.items.length}</span></h2>
              {g.sub && <span className="probe-group-sub">{g.sub}</span>}
            </summary>
            <div className="probe-grid">
              {g.items.map((p) => <ProbeCard key={p.strategy_instance} p={p} />)}
            </div>
          </details>
        ) : (
          <section key={g.key} className="probe-group">
            <div className="probe-group-head">
              <h2>{g.title} <span className="probe-group-count">{g.items.length}</span></h2>
              {g.sub && <span className="probe-group-sub">{g.sub}</span>}
            </div>
            <div className="probe-grid">
              {g.items.map((p) => <ProbeCard key={p.strategy_instance} p={p} />)}
            </div>
          </section>
        )
      ))}
    </div>
  );
}
