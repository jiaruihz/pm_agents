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

function verdict(p: ProbeHealthRow): { tone: "good" | "warn" | "bad" | "neutral"; text: string } {
  if (p.status === "no_pulse_file")
    return { tone: "bad", text: "本机镜像没有脉搏文件 —— 可能未同步，按 N100 doctor 判断生产，不要据此判定策略已死。" };
  if (p.freshness === "stale")
    return { tone: "warn", text: "行情快照偏旧，本轮决策可信度低；先确认采集是否断流再看候选。" };
  if ((p.candidate_rows ?? 0) === 0)
    return { tone: "neutral", text: `${lifecycleZh(p.lifecycle_status)}在跑，本轮未产候选（在等盘口/新鲜观测）。按执行质量评估，不看早期 PnL。` };
  return { tone: "good", text: `${lifecycleZh(p.lifecycle_status)}在跑，本轮 ${p.candidate_rows} 个候选、${num(p.execution_eligible)} 个可执行。` };
}

function ProbeCard({ p }: { p: ProbeHealthRow }) {
  const [raw, setRaw] = useState(false);
  const v = verdict(p);
  return (
    <div className="card probe-card">
      <div className="probe-card-head">
        <Link to={`/probes/${encodeURIComponent(p.strategy_instance)}`} className="probe-name">
          {p.strategy_instance}
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
          <span className="stat-label">主要拦截</span>
          <span className="stat-value">{auditZh(p.top_audit)}</span>
        </div>
      </div>

      <button className="raw-toggle" onClick={() => setRaw((x) => !x)}>
        {raw ? "收起原始字段 ▲" : "看原始字段 ▼"}
      </button>
      {raw && (
        <table className="raw-table">
          <tbody>
            {Object.entries({
              status: p.status,
              snapshot_age_min: p.snapshot_age_min,
              heartbeat_age_min: p.heartbeat_age_min,
              health_status: p.health_status,
              caps: JSON.stringify(p.caps),
            }).map(([k, val]) => (
              <tr key={k}>
                <td><GlossaryTerm field={k}>{k}</GlossaryTerm></td>
                <td>{String(val ?? "—")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

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

  return (
    <div className="page">
      <header className="page-head">
        <h1>探针在跑</h1>
        <p className="page-sub">
          live / shadow 前向取证探针的健康与执行质量。<strong>按执行质量评估，不按早期 PnL。</strong>
          每 30 秒刷新。
        </p>
      </header>

      {error && <div className="error-banner">加载失败：{error}</div>}
      {probes == null && !error && <EmptyState message="加载中…" />}
      {probes != null && probes.length === 0 && (
        <EmptyState message="注册表里没有探针" hint="weather_strategy_runtime_registry 为空，或数据库未同步。" />
      )}

      <div className="probe-grid">
        {probes?.map((p) => (
          <ProbeCard key={p.strategy_instance} p={p} />
        ))}
      </div>
    </div>
  );
}
