import { useEffect, useState } from "react";
import { weatherApi } from "../../data/weather-http";
import type { DataSourcesResponse } from "../../data/v2-types";
import { EmptyState } from "../../components/v2/EmptyState";
import { GlossaryTerm } from "../../components/v2/GlossaryTerm";
import { HealthDot } from "../../components/v2/HealthDot";
import type { DataSourceDynamicHealth, DataSourceMonitorInstance, DataSourceProfile, Freshness } from "../../data/v2-types";

function ageMin(iso: string | null): number | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return (Date.now() - t) / 60000;
}
function fresh(iso: string | null, warn: number, bad: number): Freshness {
  const a = ageMin(iso);
  if (a == null) return "unknown";
  return a <= warn ? "fresh" : a <= bad ? "aging" : "stale";
}
function ageText(iso: string | null): string {
  const a = ageMin(iso);
  if (a == null) return "—";
  if (a < 90) return `${Math.round(a)} 分钟前`;
  return `${(a / 60).toFixed(1)} 小时前`;
}

function secText(sec: number | null): string {
  if (sec == null) return "—";
  if (sec < 90) return `${Math.round(sec)}s`;
  if (sec < 5400) return `${Math.round(sec / 60)}m`;
  return `${(sec / 3600).toFixed(1)}h`;
}

function parseStringArray(raw: string | null): string[] {
  if (!raw) return [];
  try {
    const value = JSON.parse(raw);
    return Array.isArray(value) ? value.map((item) => String(item)).filter(Boolean) : [];
  } catch {
    return [];
  }
}

function healthFreshness(status: string): Freshness {
  switch (status) {
    case "fresh":
      return "fresh";
    case "stale":
      return "stale";
    case "auth_required":
    case "fetch_failed":
      return "stale";
    case "missing":
    case "unknown":
    default:
      return "unknown";
  }
}

function statusTone(status: string): Freshness {
  return healthFreshness(status);
}

function badge(status: string) {
  return <span className="badge" data-tone={statusTone(status)}>{status}</span>;
}

function compactList(items: string[], limit = 5): string {
  if (items.length === 0) return "—";
  const head = items.slice(0, limit).join(", ");
  return items.length > limit ? `${head} +${items.length - limit}` : head;
}

function byMonitorId(rows: DataSourceDynamicHealth[]): Map<string, DataSourceDynamicHealth> {
  return new Map(rows.map((row) => [row.monitor_instance_id, row]));
}

function countBy<T extends string>(values: T[]): Record<T, number> {
  return values.reduce((acc, value) => {
    acc[value] = (acc[value] ?? 0) + 1;
    return acc;
  }, {} as Record<T, number>);
}

function feedKindLabel(feedKind: string): string {
  switch (feedKind) {
    case "official_observation": return "官方观测";
    case "high_frequency_observation": return "高频观测";
    case "runway_observation": return "跑道观测";
    case "forecast": return "预报";
    case "orderbook": return "盘口";
    default: return feedKind;
  }
}

function FeedBadge({ feedKind }: { feedKind: string }) {
  return <span className="badge" data-tone="unknown">{feedKindLabel(feedKind)}</span>;
}

export function DataSourcesPage() {
  const [data, setData] = useState<DataSourcesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    weatherApi.getDataSources().then(setData).catch((e) => setError(String(e)));
  }, []);

  return (
    <div className="page">
      <header className="page-head">
        <h1>数据源</h1>
        <p className="page-sub">我们抓取了哪些数据、来自哪、什么时候、覆盖哪些城市。上层是驱动决策的预测源，下层是实时盘口快照流。</p>
      </header>

      {error && <div className="error-banner">{error}</div>}
      {data == null && !error && <EmptyState message="加载中…" />}

      {data && (
        <>
          <DataSourceManagementOverview
            profiles={data.source_profiles ?? []}
            monitors={data.monitor_instances ?? []}
            healthRows={data.dynamic_health ?? []}
          />

          <MonitorInstancesTable
            monitors={data.monitor_instances ?? []}
            healthRows={data.dynamic_health ?? []}
          />

          <SourceProfilesTable profiles={data.source_profiles ?? []} />

          <section className="card">
            <h2>预测数据源 <span className="muted">（驱动决策，来自 fact_trades）</span></h2>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr><th><GlossaryTerm field="forecast_source">数据源</GlossaryTerm></th><th>最近快照</th><th>城市数</th><th>模型数</th><th>用到的笔数</th><th>覆盖日期</th></tr>
                </thead>
                <tbody>
                  {data.forecast_sources.map((s) => (
                    <tr key={s.forecast_source}>
                      <td style={{ fontFamily: "ui-monospace, monospace" }}>{s.forecast_source}</td>
                      <td><HealthDot level={fresh(s.latest_snapshot_ts_utc, 90, 360)} /> {ageText(s.latest_snapshot_ts_utc)}<div className="muted" style={{ fontSize: 11 }}>{s.latest_snapshot_ts_utc}</div></td>
                      <td>{s.cities}</td>
                      <td>{s.models}</td>
                      <td>{s.rows}</td>
                      <td className="muted" style={{ fontSize: 12 }}>{s.first_target_date} → {s.last_target_date}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="card">
            <h2>观测源 / METAR <span className="muted">（注册表 weather_data_feed/observation_sources）</span></h2>
            <p className="stat-caption">维护了 {data.observation_sources.length} 个标准化观测源；每个有多个别名归一到 canonical 名。悬浮看别名。</p>
            <div className="table-scroll">
              <table className="data-table">
                <thead><tr><th>canonical 源</th><th>类型</th><th>说明</th><th>别名数</th></tr></thead>
                <tbody>
                  {data.observation_sources.map((s) => (
                    <tr key={s.canonical}>
                      <td style={{ fontFamily: "ui-monospace, monospace", fontSize: 12 }}
                          title={s.aliases.length ? `别名: ${s.aliases.join(", ")}` : "无别名"}>{s.canonical}</td>
                      <td>{s.kind === "metar" ? <span className="badge" data-tone="fresh">METAR</span> : <span className="badge" data-tone="unknown">{s.kind}</span>}</td>
                      <td>{s.description || "—"}</td>
                      <td>{s.aliases.length}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>

          <section className="card">
            <h2>实时盘口快照 <span className="muted">（最近 {data.market_snapshots.length} 个）</span></h2>
            <p className="stat-caption">
              更新周期约 <strong>{data.market_snapshot_cadence_min ?? "—"} 分钟/次</strong>。
              注意：这里读的是<strong>本机镜像</strong>，时间显示十几小时前 = 镜像未同步，<strong>不代表生产断流</strong>。
              刷新镜像跑 <code>scripts/ops/sync_weather_remote.sh</code>；确认生产是否在产快照看 N100 doctor。
            </p>
            {data.market_snapshots.length === 0 ? (
              <EmptyState message="没有盘口快照文件" hint="检查 runtime/.../paper_snapshots 是否已同步。" />
            ) : (
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr><th>快照</th><th>北京时间</th><th>新鲜度</th><th><GlossaryTerm field="total_records">记录数</GlossaryTerm></th><th>交易城市</th><th>研究城市</th></tr>
                  </thead>
                  <tbody>
                    {data.market_snapshots.map((m) => (
                      <tr key={m.file}>
                        <td style={{ fontFamily: "ui-monospace, monospace", fontSize: 12 }}>{m.file}</td>
                        <td>{m.ts_beijing ?? "—"}</td>
                        <td><HealthDot level={fresh(m.ts_utc, 45, 180)} /> {ageText(m.ts_utc)}</td>
                        <td>{m.total_records ?? "—"}</td>
                        <td>{m.trading_cities ?? "—"}</td>
                        <td>{m.research_cities ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}

function DataSourceManagementOverview({
  profiles,
  monitors,
  healthRows,
}: {
  profiles: DataSourceProfile[];
  monitors: DataSourceMonitorInstance[];
  healthRows: DataSourceDynamicHealth[];
}) {
  const healthCounts = countBy(healthRows.map((row) => row.status));
  const feedKinds = new Set(profiles.map((row) => row.feed_kind));
  const liveProfiles = profiles.filter((row) => row.live_eligible).length;
  const authProfiles = profiles.filter((row) => row.requires_auth).length;

  return (
    <section className="kpi-grid">
      <div className="card kpi">
        <div className="kpi-label">source profiles</div>
        <div className="kpi-value">{profiles.length}</div>
        <div className="kpi-note">{feedKinds.size} feed kinds · {liveProfiles} live eligible · {authProfiles} auth</div>
      </div>
      <div className="card kpi">
        <div className="kpi-label">monitor instances</div>
        <div className="kpi-value">{monitors.length}</div>
        <div className="kpi-note">DB 管理面实例，不含 JSONL 明细</div>
      </div>
      <div className="card kpi">
        <div className="kpi-label">fresh</div>
        <div className="kpi-value">{healthCounts.fresh ?? 0}</div>
        <div className="kpi-note">按 latest.json generated_at 动态计算</div>
      </div>
      <div className="card kpi">
        <div className="kpi-label">attention</div>
        <div className="kpi-value">{(healthCounts.stale ?? 0) + (healthCounts.fetch_failed ?? 0) + (healthCounts.auth_required ?? 0) + (healthCounts.missing ?? 0)}</div>
        <div className="kpi-note">stale / fetch_failed / auth_required / missing</div>
      </div>
    </section>
  );
}

function MonitorInstancesTable({
  monitors,
  healthRows,
}: {
  monitors: DataSourceMonitorInstance[];
  healthRows: DataSourceDynamicHealth[];
}) {
  const healthById = byMonitorId(healthRows);
  return (
    <section className="card">
      <h2>监控实例 <span className="muted">（weather_data_monitor_instance + latest health）</span></h2>
      <p className="stat-caption">健康年龄来自生产 latest.json 的 generated_at；mtime 单独展示，只代表本机文件更新时间。</p>
      {monitors.length === 0 ? (
        <EmptyState message="没有 monitor instance" hint="先运行 materializer 写入 weather_data_monitor_instance。" />
      ) : (
        <div className="table-scroll">
          <table className="data-table compact">
            <thead>
              <tr>
                <th>instance</th>
                <th>feed</th>
                <th>status</th>
                <th>generated age</th>
                <th>file mtime</th>
                <th>rows</th>
                <th>cities / sources</th>
                <th>paths</th>
              </tr>
            </thead>
            <tbody>
              {monitors.map((monitor) => {
                const health = healthById.get(monitor.monitor_instance_id);
                const cities = health?.cities.length ? health.cities : parseStringArray(monitor.cities_json);
                const sources = health?.sources.length ? health.sources : parseStringArray(monitor.sources_json);
                const journals = parseStringArray(monitor.journal_paths_json);
                return (
                  <tr key={monitor.monitor_instance_id}>
                    <td>
                      <div className="mono strong">{monitor.monitor_instance_id}</div>
                      <div className="muted tiny">{monitor.display_name}</div>
                    </td>
                    <td><FeedBadge feedKind={monitor.feed_kind} /></td>
                    <td>
                      {badge(health?.status ?? "unknown")}
                      <div className="muted tiny">{monitor.desired_status} · {monitor.host}</div>
                    </td>
                    <td>
                      <HealthDot level={healthFreshness(health?.status ?? "unknown")} /> {secText(health?.age_sec ?? null)}
                      <div className="muted tiny">{health?.latest_generated_at_utc ?? "—"}</div>
                    </td>
                    <td>
                      {ageText(health?.latest_file_mtime_utc ?? null)}
                      <div className="muted tiny">{health?.latest_file_mtime_utc ?? "—"}</div>
                    </td>
                    <td>{health?.rows ?? "—"}</td>
                    <td>
                      <div title={cities.join(", ")}>{cities.length} cities · {compactList(cities, 3)}</div>
                      <div className="muted tiny" title={sources.join(", ")}>{sources.length} sources · {compactList(sources, 3)}</div>
                    </td>
                    <td>
                      <div className="path-cell" title={monitor.latest_path ?? ""}>latest: {monitor.latest_path ? "yes" : "no"}</div>
                      <div className="muted tiny" title={monitor.output_dir ?? ""}>journals: {journals.length} · state: {monitor.state_path ? "yes" : "no"}</div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function SourceProfilesTable({ profiles }: { profiles: DataSourceProfile[] }) {
  const feedCounts = Object.entries(countBy(profiles.map((row) => row.feed_kind))).sort((a, b) => a[0].localeCompare(b[0]));
  return (
    <section className="card">
      <h2>源配置矩阵 <span className="muted">（weather_data_source_profile）</span></h2>
      <p className="stat-caption">
        {feedCounts.map(([kind, count]) => `${feedKindLabel(kind)} ${count}`).join(" · ") || "无配置"}
      </p>
      {profiles.length === 0 ? (
        <EmptyState message="没有 source profile" hint="先运行 materializer 写入 weather_data_source_profile。" />
      ) : (
        <div className="table-scroll">
          <table className="data-table compact">
            <thead>
              <tr>
                <th>city</th>
                <th>feed</th>
                <th>source</th>
                <th>station / runway</th>
                <th>cadence</th>
                <th>eligibility</th>
                <th>notes</th>
              </tr>
            </thead>
            <tbody>
              {profiles.map((profile) => (
                <tr key={profile.profile_id}>
                  <td>
                    <div className="strong">{profile.city}</div>
                    <div className="muted tiny">{profile.timezone_name ?? "—"}</div>
                  </td>
                  <td><FeedBadge feedKind={profile.feed_kind} /></td>
                  <td>
                    <div className="mono strong">{profile.source_key}</div>
                    <div className="muted tiny">{profile.source_role} · {profile.source_kind ?? "—"}</div>
                  </td>
                  <td>
                    <div className="mono">{profile.station_or_feed || profile.icao || "—"}</div>
                    <div className="muted tiny">runway {profile.runway ?? "—"}</div>
                  </td>
                  <td>
                    <div>{secText(profile.expected_cadence_sec)}</div>
                    <div className="muted tiny">stale {secText(profile.staleness_max_age_sec)}</div>
                  </td>
                  <td>
                    {profile.live_eligible ? <span className="badge" data-tone="fresh">live</span> : <span className="badge" data-tone="unknown">no live</span>}
                    {profile.requires_auth ? <span className="badge" data-tone="aging" style={{ marginLeft: 4 }}>auth</span> : null}
                    <div className="muted tiny">strategy {profile.strategy_eligible ? "yes" : "no"}</div>
                  </td>
                  <td>
                    <div className="row-note">{profile.notes || profile.auth_ref || "—"}</div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
