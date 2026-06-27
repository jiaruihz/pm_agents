import { useEffect, useMemo, useState } from "react";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import type { StrategyRuntimeDetail, StrategyRuntimeOverview, StrategyRuntimeRow, StrategyShadowQueueRow } from "../../data/weather-types";

const REGIME_ROUTED_NO_LIVE = "regime_routed_no_soft_balanced_tiny_live_v1";

function tomorrowLocal(): string {
  const d = new Date();
  d.setDate(d.getDate() + 1);
  const year = d.getFullYear();
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function fmtInt(value: number | null | undefined): string {
  if (value == null) return "-";
  return value.toLocaleString();
}

function fmtAge(minutes: number | null): string {
  if (minutes == null) return "-";
  if (minutes < 90) return `${minutes.toFixed(1)}m`;
  if (minutes < 48 * 60) return `${(minutes / 60).toFixed(1)}h`;
  return `${(minutes / 1440).toFixed(1)}d`;
}

function fmtTime(value: string | null | undefined): string {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  return d.toLocaleString();
}

function fmtMoney(value: number | null): string {
  if (value == null) return "-";
  return `$${value.toFixed(2)}`;
}

function shortPath(value: string | null | undefined): string {
  if (!value) return "-";
  const parts = value.split("/");
  return parts.slice(Math.max(0, parts.length - 3)).join("/");
}

function listText(items: unknown[] | undefined, limit = 2): string {
  if (!items || items.length === 0) return "-";
  return items.slice(0, limit).map((item) => String(item)).join("; ");
}

function valueOf(obj: Record<string, unknown> | undefined, key: string): unknown {
  return obj ? obj[key] : undefined;
}

function fmtUnknown(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(3);
  if (typeof value === "boolean") return value ? "yes" : "no";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function fmtPrice(value: unknown): string {
  const n = Number(value);
  return Number.isFinite(n) ? `${(n * 100).toFixed(1)}c` : "-";
}

function fmtUsd(value: unknown): string {
  const n = Number(value);
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : "-";
}

function fmtShares(value: unknown): string {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(2) : "-";
}

function fmtWeight(value: unknown): string {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(3) : "-";
}

function compactJson(value: unknown): string {
  if (!value || typeof value !== "object") return "-";
  return Object.entries(value as Record<string, unknown>)
    .map(([k, v]) => `${k}: ${fmtUnknown(v)}`)
    .join(" · ") || "-";
}

function statusColor(status: string): string {
  if (["healthy", "target_seen", "live"].includes(status)) return "var(--ok)";
  if (["blocked", "stale", "shelved"].includes(status)) return "var(--bad)";
  if (["collecting", "live_waiting", "shadow_waiting", "monitoring", "telemetry", "shadow"].includes(status)) return "var(--accent)";
  return "var(--muted)";
}

function targetLabel(status: string): string {
  const labels: Record<string, string> = {
    target_seen: "target seen",
    live_waiting: "live waiting",
    shadow_waiting: "shadow waiting",
    collecting: "collecting",
    monitoring: "monitoring",
    blocked: "blocked",
    stale: "stale",
    shelved: "shelved",
    unknown: "unknown",
  };
  return labels[status] ?? status;
}

function policyLabel(value: string): string {
  const labels: Record<string, string> = {
    soft_balanced_live_policy: "live soft",
    soft_wind_only_shadow: "wind only",
    soft_wind_context_shadow: "wind ctx",
    soft_temp_context_light_shadow: "temp light",
    soft_temp_context_medium_shadow: "temp medium",
  };
  return labels[value] ?? value.replace(/_/g, " ");
}

export function WeatherStrategyRuntimePage(): JSX.Element {
  const [targetDate, setTargetDate] = useState(tomorrowLocal);
  const [data, setData] = useState<StrategyRuntimeOverview | null>(null);
  const [detail, setDetail] = useState<StrategyRuntimeDetail | null>(null);
  const [selectedStrategy, setSelectedStrategy] = useState(REGIME_ROUTED_NO_LIVE);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    weatherApi
      .getStrategyRuntimeOverview({ target_date: targetDate })
      .then((res) => {
        setData(res);
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [targetDate, refreshNonce]);

  useEffect(() => {
    if (!autoRefresh) return undefined;
    const id = window.setInterval(() => setRefreshNonce((x) => x + 1), 30000);
    return () => window.clearInterval(id);
  }, [autoRefresh]);

  const activeRows = useMemo(() => data?.strategies ?? [], [data]);
  const queueRows = data?.shadow_queue ?? [];
  const shadowRows = useMemo(
    () => activeRows.filter((row) => row.lifecycle_status === "shadow" || row.execution_mode === "zero_notional_shadow"),
    [activeRows],
  );

  useEffect(() => {
    if (!data || activeRows.length === 0) return;
    if (activeRows.some((row) => row.strategy_instance === selectedStrategy)) return;
    const preferred = activeRows.find((row) => row.strategy_instance === REGIME_ROUTED_NO_LIVE)
      ?? activeRows.find((row) => row.lifecycle_status === "live")
      ?? activeRows[0];
    setSelectedStrategy(preferred.strategy_instance);
  }, [activeRows, data, selectedStrategy]);

  useEffect(() => {
    if (!selectedStrategy) return;
    weatherApi
      .getStrategyRuntimeDetail(selectedStrategy, { limit: 12 })
      .then((res) => {
        setDetail(res);
        setDetailError(null);
      })
      .catch((e: Error) => {
        setDetail(null);
        setDetailError(e.message);
      });
  }, [selectedStrategy, refreshNonce, data?.refreshed_at_utc]);

  return (
    <PageFrame title="Strategy Runtime" desc="live · shadow · telemetry · target-date status">
      <>
        <section style={toolbarStyle}>
          <div>
            <div style={{ fontSize: 12, color: "var(--muted)" }}>Target date</div>
            <input
              type="date"
              value={targetDate}
              onChange={(e) => setTargetDate(e.target.value)}
              style={dateInputStyle}
            />
          </div>
          <div style={{ marginLeft: "auto", color: "var(--muted)", fontSize: 12 }}>
            refreshed {fmtTime(data?.refreshed_at_utc)}
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--muted)" }}>
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
            />
            auto 30s
          </label>
          <button onClick={() => setRefreshNonce((x) => x + 1)}>Refresh</button>
        </section>

        {loading && <div style={mutedBlockStyle}>Loading strategy runtime registry...</div>}
        {error && <div style={errorStyle}>{error}</div>}

        {data && !loading && !error && (
          <div style={stack16Style}>
            <SummaryGrid data={data} />
            <section style={panelStyle}>
              <div style={sectionHeadStyle}>
                <div>
                  <h2 style={sectionTitleStyle}>Live Monitor</h2>
                  <div style={subtleStyle}>selected runtime · recent heartbeat/orders</div>
                </div>
                <select value={selectedStrategy} onChange={(e) => setSelectedStrategy(e.target.value)}>
                  {activeRows.map((row) => (
                    <option key={row.strategy_instance} value={row.strategy_instance}>
                      {row.display_name}
                    </option>
                  ))}
                </select>
              </div>
              {detailError && <div style={errorStyle}>{detailError}</div>}
              {detail && <RuntimeDetailPanel detail={detail} />}
            </section>

            <section style={panelStyle}>
              <div style={sectionHeadStyle}>
                <div>
                  <h2 style={sectionTitleStyle}>Strategy Status</h2>
                  <div style={subtleStyle}>{activeRows.length} registered instances · target {data.target_date}</div>
                </div>
              </div>
              <StrategyTable rows={activeRows} />
            </section>

            <section style={panelStyle}>
              <div style={sectionHeadStyle}>
                <div>
                  <h2 style={sectionTitleStyle}>Shadow Instances</h2>
                  <div style={subtleStyle}>{shadowRows.length} active or registered zero-notional tracks</div>
                </div>
              </div>
              <ShadowInstanceTable rows={shadowRows} />
            </section>

            <section style={panelStyle}>
              <div style={sectionHeadStyle}>
                <div>
                  <h2 style={sectionTitleStyle}>Shadow Queue</h2>
                  <div style={subtleStyle}>{queueRows.length} proposed or blocked shadow tracks</div>
                </div>
              </div>
              <ShadowQueueTable rows={queueRows} />
            </section>
          </div>
        )}
      </>
    </PageFrame>
  );
}

function RuntimeDetailPanel({ detail }: { detail: StrategyRuntimeDetail }) {
  const strategy = detail.strategy;
  const summary = strategy.summary ?? {};
  const recent = detail.recent_records ?? {};
  const liveOrders = recent.live_orders ?? [];
  const paperOrders = recent.paper_orders ?? [];
  const blockedCandidates = recent.blocked_candidates ?? [];
  const latestCandidates = recent.latest_candidates ?? [];
  const heartbeats = recent.summary_history ?? recent.primary_journal ?? [];
  const shadowCandidates = recent.shadow_candidates ?? [];
  const latestHeartbeat = heartbeats.length ? heartbeats[heartbeats.length - 1] : summary;
  const executor = valueOf(latestHeartbeat, "executor_result") as Record<string, unknown> | undefined;
  const executorParsed = valueOf(executor, "parsed") as Record<string, unknown> | undefined;

  return (
    <div style={stack12Style}>
      <div style={metricGridStyle}>
        <Metric label="Live enabled" value={fmtUnknown(valueOf(latestHeartbeat, "live_enabled") ?? strategy.live_enabled)} color={valueOf(latestHeartbeat, "live_enabled") || strategy.live_enabled ? "var(--ok)" : "var(--muted)"} />
        <Metric label="Routed" value={fmtUnknown(valueOf(latestHeartbeat, "routed_candidates") ?? strategy.candidate_rows)} />
        <Metric label="Eligible" value={fmtUnknown(valueOf(latestHeartbeat, "execution_eligible") ?? strategy.plan_rows)} color={Number(valueOf(latestHeartbeat, "execution_eligible") ?? strategy.plan_rows) > 0 ? "var(--ok)" : undefined} />
        <Metric label="Live orders" value={fmtUnknown(valueOf(executorParsed, "live_orders") ?? strategy.live_order_rows)} color={Number(valueOf(executorParsed, "live_orders") ?? strategy.live_order_rows) > 0 ? "var(--ok)" : undefined} />
        <Metric label="Base N" value={fmtUsd(valueOf(latestHeartbeat, "base_notional") ?? strategy.cap_order_notional)} />
        <Metric label="Day cap" value={fmtUsd(valueOf(latestHeartbeat, "daily_gross_cap") ?? strategy.cap_total_day_notional)} />
      </div>

      <div style={detailGridStyle}>
        <InfoBox title="Latest heartbeat" rows={[
          ["generated", fmtTime(String(valueOf(latestHeartbeat, "generated_at_utc") ?? strategy.latest_summary_ts_utc ?? ""))],
          ["snapshot", fmtTime(String(valueOf(valueOf(latestHeartbeat, "meta") as Record<string, unknown> | undefined, "snapshot_ts_utc") ?? ""))],
          ["obs cache", fmtUnknown(valueOf(valueOf(latestHeartbeat, "meta") as Record<string, unknown> | undefined, "observation_cache_status"))],
          ["by regime", compactJson(valueOf(latestHeartbeat, "candidate_by_regime"))],
          ["skips", compactJson(valueOf(latestHeartbeat, "skip_reasons"))],
          ["no order", fmtUnknown(valueOf(latestHeartbeat, "no_order_placed"))],
        ]} />
        <InfoBox title="Execution result" rows={[
          ["plans read", fmtUnknown(valueOf(executorParsed, "plans_read"))],
          ["paper written", fmtUnknown(valueOf(executorParsed, "paper_written"))],
          ["live written", fmtUnknown(valueOf(executorParsed, "live_written"))],
          ["live errors", fmtUnknown(valueOf(executorParsed, "live_errors"))],
          ["cancel checked", fmtUnknown(valueOf(executorParsed, "cancel_expired_checked"))],
        ]} />
      </div>

      <ShadowSizingPanel counts={valueOf(latestHeartbeat, "shadow_policy_counts") as Record<string, unknown> | undefined} />
      <LatestCandidateCards rows={latestCandidates} />
      <BlockedCandidateCards rows={blockedCandidates} />
      <RecentRecordTable
        title="Recent live orders"
        rows={liveOrders}
        empty="No live order records yet."
        columns={[
          ["time", (row) => fmtTime(String(row.placed_at_utc ?? row.created_at_utc ?? row.live_attempt_ts_utc ?? ""))],
          ["city", (row) => fmtUnknown(row.city)],
          ["side", (row) => fmtUnknown(row.order_side ?? row.side)],
          ["bracket", (row) => fmtUnknown(row.bracket)],
          ["price", (row) => fmtPrice(row.entry_price ?? row.limit_price ?? row.no_ask)],
          ["shares", (row) => fmtShares(row.shares ?? row.desired_shares ?? row.size)],
          ["temp L", (row) => fmtShares(row.soft_temp_context_light_shadow_shares)],
          ["temp regime", (row) => fmtUnknown(row.temperature_context_regime)],
          ["status", (row) => fmtUnknown(row.status ?? row.order_status)],
        ]}
      />
      <RecentRecordTable
        title="Recent shadow candidates"
        rows={shadowCandidates}
        empty="No shadow candidate records in this artifact."
        columns={[
          ["time", (row) => fmtTime(String(row.created_at_utc ?? row.decision_snapshot_ts_utc ?? ""))],
          ["city", (row) => fmtUnknown(row.city)],
          ["regime", (row) => fmtUnknown(row.day_regime)],
          ["expression", (row) => fmtUnknown(row.expression)],
          ["ask", (row) => fmtPrice(row.no_ask ?? row.ask)],
          ["soft $", (row) => fmtUsd(row.hypothetical_weighted_notional_usd ?? row.weighted_notional_usd)],
          ["shares", (row) => fmtUnknown(row.hypothetical_weighted_shares ?? row.weighted_shares)],
        ]}
      />
      <RecentRecordTable
        title="Recent paper orders"
        rows={paperOrders}
        empty="No paper order records yet."
        columns={[
          ["time", (row) => fmtTime(String(row.placed_at_utc ?? row.created_at_utc ?? ""))],
          ["city", (row) => fmtUnknown(row.city)],
          ["side", (row) => fmtUnknown(row.order_side ?? row.side)],
          ["bracket", (row) => fmtUnknown(row.bracket)],
          ["price", (row) => fmtPrice(row.entry_price ?? row.limit_price ?? row.no_ask)],
          ["shares", (row) => fmtShares(row.shares ?? row.desired_shares ?? row.size)],
          ["temp L", (row) => fmtShares(row.soft_temp_context_light_shadow_shares)],
          ["temp regime", (row) => fmtUnknown(row.temperature_context_regime)],
          ["status", (row) => fmtUnknown(row.status ?? row.order_status)],
        ]}
      />
    </div>
  );
}

function ShadowSizingPanel({ counts }: { counts: Record<string, unknown> | undefined }) {
  const rows = counts && typeof counts === "object" ? Object.entries(counts) : [];
  return (
    <div>
      <div style={{ fontWeight: 700, marginBottom: 6 }}>Shadow sizing</div>
      {rows.length === 0 ? (
        <div style={mutedInlineStyle}>No shadow sizing records in the latest heartbeat.</div>
      ) : (
        <div style={tableWrapStyle}>
          <table style={{ ...tableStyle, minWidth: 620 }}>
            <thead>
              <tr>
                <th style={thStyle}>Policy</th>
                <th style={numThStyle}>Avg weight</th>
                <th style={numThStyle}>Shadow $</th>
                <th style={numThStyle}>Exec rows</th>
                <th style={numThStyle}>Exec $</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(([name, raw]) => {
                const row = raw as Record<string, unknown>;
                return (
                  <tr key={name}>
                    <td style={tdStyle}>{policyLabel(name)}</td>
                    <td style={numTdStyle}>{fmtWeight(row.avg_weight)}</td>
                    <td style={numTdStyle}>{fmtUsd(row.shadow_notional_usd)}</td>
                    <td style={numTdStyle}>{fmtUnknown(row.shadow_executable_rows)}</td>
                    <td style={numTdStyle}>{fmtUsd(row.shadow_executable_notional_usd)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function LatestCandidateCards({ rows }: { rows: Array<Record<string, unknown>> }) {
  const ordered = rows.slice().reverse();
  return (
    <CandidateCards
      title="Latest candidates"
      empty="No latest candidate records in this artifact."
      rows={ordered}
      showStatus
    />
  );
}

function BlockedCandidateCards({ rows }: { rows: Array<Record<string, unknown>> }) {
  const ordered = rows.slice().reverse();
  return <CandidateCards title="Blocked candidates" empty="No blocked candidate records yet." rows={ordered} />;
}

function CandidateCards({
  title,
  rows,
  empty,
  showStatus = false,
}: {
  title: string;
  rows: Array<Record<string, unknown>>;
  empty: string;
  showStatus?: boolean;
}) {
  return (
    <div>
      <div style={{ fontWeight: 700, marginBottom: 6 }}>{title}</div>
      {rows.length === 0 ? (
        <div style={mutedInlineStyle}>{empty}</div>
      ) : (
        <div className="runtime-candidate-grid" style={candidateGridStyle}>
          {rows.map((row, idx) => (
            <div key={String(row.candidate_id ?? idx)} style={candidateCardStyle}>
              <div style={candidateCardHeadStyle}>
                <div>
                  <div style={{ fontWeight: 800 }}>{fmtUnknown(row.city)} · {fmtUnknown(row.bracket)}</div>
                  <div style={subtleStyle}>{fmtUnknown(row.target_date)} · {fmtUnknown(row.route_leg)}</div>
                </div>
                <Badge text={showStatus ? fmtUnknown(row.candidate_status) : fmtUnknown(row.day_regime)} color={showStatus && row.candidate_status === "accepted" ? "var(--ok)" : "var(--accent)"} />
              </div>
              <div style={candidateMetricGridStyle}>
                <MiniStat label="ask" value={fmtPrice(row.ask)} />
                <MiniStat label="shares" value={fmtShares(row.soft_shares)} />
                <MiniStat label="soft $" value={fmtUsd(row.soft_notional_usd)} />
                <MiniStat label="temp L" value={fmtShares(row.soft_temp_context_light_shadow_shares)} />
                <MiniStat label="temp M" value={fmtShares(row.soft_temp_context_medium_shadow_shares)} />
                <MiniStat label="wind ctx" value={fmtShares(row.soft_wind_context_shadow_shares)} />
                <MiniStat label="parity" value={fmtUnknown(row.live_feature_parity_ok)} />
              </div>
              <div style={blockedReasonStyle}>{fmtUnknown(row.execution_skip_reason)}</div>
              <div style={contextLineStyle}>{fmtUnknown(row.temperature_context_regime)}</div>
              <div style={candidateFooterStyle}>
                <span>{fmtTime(String(row.created_at_utc ?? ""))}</span>
                <span>{fmtUnknown(row.live_feature_source)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ color: "var(--muted)", fontSize: 10 }}>{label}</div>
      <div style={{ fontFamily: "monospace", fontSize: 12, fontWeight: 700 }}>{value}</div>
    </div>
  );
}

function InfoBox({ title, rows }: { title: string; rows: [string, string][] }) {
  return (
    <div style={metricStyle}>
      <div style={{ fontWeight: 700, marginBottom: 8 }}>{title}</div>
      <div style={{ display: "grid", gap: 6 }}>
        {rows.map(([label, value]) => (
          <div key={label} style={{ display: "grid", gridTemplateColumns: "110px 1fr", gap: 8 }}>
            <span style={{ color: "var(--muted)", fontSize: 12 }}>{label}</span>
            <span style={{ fontFamily: "monospace", fontSize: 12, wordBreak: "break-word" }}>{value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function RecentRecordTable({
  title,
  rows,
  columns,
  empty,
}: {
  title: string;
  rows: Array<Record<string, unknown>>;
  columns: [string, (row: Record<string, unknown>) => string][];
  empty: string;
}) {
  return (
    <div>
      <div style={{ fontWeight: 700, marginBottom: 6 }}>{title}</div>
      {rows.length === 0 ? (
        <div style={mutedInlineStyle}>{empty}</div>
      ) : (
        <div style={tableWrapStyle}>
          <table style={tableStyle}>
            <thead>
              <tr>
                {columns.map(([label]) => <th key={label} style={thStyle}>{label}</th>)}
              </tr>
            </thead>
            <tbody>
              {rows.slice().reverse().map((row, idx) => (
                <tr key={String(row.shadow_decision_id ?? row.signal_id ?? row.order_id ?? idx)}>
                  {columns.map(([label, render]) => <td key={label} style={tdStyle}>{render(row)}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SummaryGrid({ data }: { data: StrategyRuntimeOverview }) {
  const s = data.summary;
  return (
    <div style={metricGridStyle}>
      <Metric label="Registered" value={String(s.total_strategies)} />
      <Metric label="Live" value={String(s.live_strategies)} color="var(--ok)" />
      <Metric label="Shadow" value={String(s.shadow_strategies)} color="var(--accent)" />
      <Metric label="Telemetry" value={String(s.telemetry_strategies)} />
      <Metric label="Healthy" value={String(s.healthy_strategies)} color="var(--ok)" />
      <Metric label="Blocked / stale" value={`${s.blocked_strategies} / ${s.stale_strategies}`} color={s.blocked_strategies + s.stale_strategies > 0 ? "var(--bad)" : undefined} />
      <Metric label="Target seen" value={String(s.target_seen_strategies)} color={s.target_seen_strategies > 0 ? "var(--ok)" : undefined} />
      <Metric label="Queue high" value={`${s.shadow_queue_high} / ${s.shadow_queue_items}`} />
    </div>
  );
}

function Metric({ label, value, color = "inherit" }: { label: string; value: string; color?: string }) {
  return (
    <div style={metricStyle}>
      <div style={{ color: "var(--muted)", fontSize: 11 }}>{label}</div>
      <div style={{ marginTop: 4, fontSize: 22, fontWeight: 700, color, fontVariantNumeric: "tabular-nums" }}>{value}</div>
    </div>
  );
}

function StrategyTable({ rows }: { rows: StrategyRuntimeRow[] }) {
  return (
    <div style={tableWrapStyle}>
      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={thStyle}>Strategy</th>
            <th style={thStyle}>Mode</th>
            <th style={thStyle}>Health</th>
            <th style={thStyle}>Target</th>
            <th style={numThStyle}>Rows</th>
            <th style={thStyle}>Freshness</th>
            <th style={thStyle}>Cap / live</th>
            <th style={thStyle}>Notes</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <StrategyRowView key={row.strategy_instance} row={row} />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function StrategyRowView({ row }: { row: StrategyRuntimeRow }) {
  const targetArtifacts = row.target_artifacts.map((a) => a.artifact_kind).join(", ");
  const counts = [
    `live ${fmtInt(row.live_order_rows)}`,
    `paper ${fmtInt(row.paper_order_rows)}`,
    `shadow ${fmtInt(row.shadow_rows)}`,
    `telemetry ${fmtInt(row.telemetry_rows)}`,
  ].join(" · ");
  const cap = [
    row.cap_order_notional != null ? `order ${fmtMoney(row.cap_order_notional)}` : null,
    row.cap_city_day_notional != null ? `city ${fmtMoney(row.cap_city_day_notional)}` : null,
    row.live_enabled != null ? `live ${row.live_enabled ? "on" : "off"}` : null,
  ].filter(Boolean).join(" · ");
  const blockers = listText(row.blockers);

  return (
    <tr>
      <td style={tdStyle}>
        <div style={{ fontWeight: 700 }}>{row.display_name}</div>
        <div style={subtleStyle}>{row.family}</div>
        <div style={monoSmallStyle}>{row.strategy_instance}</div>
      </td>
      <td style={tdStyle}>
        <Badge text={row.lifecycle_status} color={statusColor(row.lifecycle_status)} />
        <div style={{ marginTop: 6, color: "var(--muted)" }}>{row.execution_mode}</div>
      </td>
      <td style={tdStyle}>
        <Badge text={row.health_status} color={statusColor(row.health_status)} />
        <div style={{ marginTop: 6, color: "var(--muted)" }}>age {fmtAge(row.heartbeat_age_min)}</div>
      </td>
      <td style={tdStyle}>
        <Badge text={targetLabel(row.target_status)} color={statusColor(row.target_status)} />
        <div style={{ marginTop: 6, color: "var(--muted)" }}>
          {targetArtifacts || `latest ${row.latest_sample_target_date ?? "-"}`}
        </div>
      </td>
      <td style={numTdStyle}>
        <div>{counts}</div>
        <div style={{ marginTop: 6, color: "var(--muted)" }}>fact {fmtInt(row.fact_trade_rows)} · real {fmtInt(row.fact_live_real_rows)}</div>
      </td>
      <td style={tdStyle}>
        <div>{fmtTime(row.latest_data_ts_utc || row.latest_artifact_mtime_utc)}</div>
        <div style={monoSmallStyle}>{shortPath(row.primary_journal_path)}</div>
      </td>
      <td style={tdStyle}>{cap || "-"}</td>
      <td style={tdStyle}>
        <div>{blockers !== "-" ? blockers : row.notes ?? "-"}</div>
      </td>
    </tr>
  );
}

function ShadowQueueTable({ rows }: { rows: StrategyShadowQueueRow[] }) {
  if (rows.length === 0) {
    return <div style={mutedBlockStyle}>No queued shadow strategies.</div>;
  }
  return (
    <div style={tableWrapStyle}>
      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={thStyle}>Shadow</th>
            <th style={thStyle}>Priority</th>
            <th style={thStyle}>Status</th>
            <th style={thStyle}>Family</th>
            <th style={thStyle}>Target runtime</th>
            <th style={thStyle}>Required fields</th>
            <th style={thStyle}>Notes</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.shadow_id}>
              <td style={tdStyle}>
                <div style={{ fontWeight: 700 }}>{row.display_name}</div>
                <div style={monoSmallStyle}>{row.shadow_id}</div>
              </td>
              <td style={tdStyle}><Badge text={row.priority} color={row.priority === "high" ? "var(--bad)" : "var(--accent)"} /></td>
              <td style={tdStyle}><Badge text={row.status} color={statusColor(row.status)} /></td>
              <td style={tdStyle}>{row.family}</td>
              <td style={monoTdStyle}>{shortPath(row.target_runtime_dir)}</td>
              <td style={tdStyle}>{listText(row.required_fields, 4)}</td>
              <td style={tdStyle}>{listText(row.blockers) !== "-" ? listText(row.blockers) : row.notes ?? "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ShadowInstanceTable({ rows }: { rows: StrategyRuntimeRow[] }) {
  if (rows.length === 0) {
    return <div style={mutedBlockStyle}>No active shadow runtimes.</div>;
  }
  return (
    <div style={tableWrapStyle}>
      <table style={tableStyle}>
        <thead>
          <tr>
            <th style={thStyle}>Shadow</th>
            <th style={thStyle}>Health</th>
            <th style={numThStyle}>Rows</th>
            <th style={thStyle}>Latest artifact</th>
            <th style={thStyle}>Journal</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.strategy_instance}>
              <td style={tdStyle}>
                <div style={{ fontWeight: 700 }}>{row.display_name}</div>
                <div style={monoSmallStyle}>{row.strategy_instance}</div>
              </td>
              <td style={tdStyle}>
                <Badge text={row.health_status} color={statusColor(row.health_status)} />
                <div style={subtleStyle}>{row.execution_mode}</div>
              </td>
              <td style={numTdStyle}>
                shadow {fmtInt(row.shadow_rows)}
                <div style={subtleStyle}>paper {fmtInt(row.paper_order_rows)} · telemetry {fmtInt(row.telemetry_rows)}</div>
              </td>
              <td style={tdStyle}>{fmtTime(row.latest_data_ts_utc || row.latest_artifact_mtime_utc)}</td>
              <td style={monoTdStyle}>{shortPath(row.primary_journal_path)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Badge({ text, color }: { text: string; color: string }) {
  return (
    <span style={{
      display: "inline-block",
      padding: "2px 7px",
      borderRadius: 6,
      fontSize: 10,
      fontWeight: 700,
      color,
      background: `${color}20`,
      border: `1px solid ${color}66`,
      whiteSpace: "nowrap",
    }}>
      {text}
    </span>
  );
}

const toolbarStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  flexWrap: "wrap",
  gap: 14,
  marginBottom: 16,
  border: "1px solid var(--stroke)",
  background: "var(--card)",
  borderRadius: 8,
  padding: 14,
};
const stack16Style: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr)",
  gap: 16,
  minWidth: 0,
};
const stack12Style: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr)",
  gap: 12,
  marginTop: 12,
  minWidth: 0,
};
const dateInputStyle: React.CSSProperties = {
  marginTop: 6,
  height: 30,
  borderRadius: 6,
  border: "1px solid var(--stroke)",
  background: "transparent",
  color: "var(--fg)",
  padding: "0 8px",
};
const panelStyle: React.CSSProperties = {
  border: "1px solid var(--stroke)",
  background: "var(--card)",
  borderRadius: 8,
  padding: 16,
  minWidth: 0,
};
const sectionHeadStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
  flexWrap: "wrap",
  gap: 16,
};
const sectionTitleStyle: React.CSSProperties = {
  margin: 0,
  fontSize: 18,
};
const subtleStyle: React.CSSProperties = {
  marginTop: 4,
  color: "var(--muted)",
  fontSize: 12,
};
const monoSmallStyle: React.CSSProperties = {
  marginTop: 5,
  color: "var(--muted)",
  fontSize: 10,
  fontFamily: "monospace",
  wordBreak: "break-word",
};
const metricGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(min(138px, 100%), 1fr))",
  gap: 10,
  minWidth: 0,
};
const metricStyle: React.CSSProperties = {
  border: "1px solid var(--stroke)",
  borderRadius: 8,
  background: "var(--card)",
  padding: 12,
  minWidth: 0,
};
const detailGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(min(280px, 100%), 1fr))",
  gap: 10,
  minWidth: 0,
};
const tableWrapStyle: React.CSSProperties = {
  overflowX: "auto",
  marginTop: 12,
  width: "100%",
  maxWidth: "100%",
  minWidth: 0,
};
const tableStyle: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: 12,
  minWidth: 720,
};
const thStyle: React.CSSProperties = {
  textAlign: "left",
  color: "var(--muted)",
  borderBottom: "1px solid var(--stroke)",
  padding: "8px 6px",
  whiteSpace: "nowrap",
};
const numThStyle: React.CSSProperties = {
  ...thStyle,
  textAlign: "right",
};
const tdStyle: React.CSSProperties = {
  borderBottom: "1px solid var(--stroke)",
  padding: "9px 6px",
  verticalAlign: "top",
  lineHeight: 1.35,
};
const monoTdStyle: React.CSSProperties = {
  ...tdStyle,
  fontFamily: "monospace",
  whiteSpace: "nowrap",
};
const numTdStyle: React.CSSProperties = {
  ...tdStyle,
  textAlign: "right",
  fontVariantNumeric: "tabular-nums",
  whiteSpace: "nowrap",
};
const mutedBlockStyle: React.CSSProperties = {
  color: "var(--muted)",
  textAlign: "center",
  padding: 34,
};
const mutedInlineStyle: React.CSSProperties = {
  color: "var(--muted)",
  padding: "12px 0",
};
const errorStyle: React.CSSProperties = {
  color: "var(--bad)",
  border: "1px solid var(--bad)",
  borderRadius: 8,
  padding: 12,
  background: "var(--bad)18",
};
const candidateGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(min(240px, 100%), 1fr))",
  gap: 10,
  minWidth: 0,
};
const candidateCardStyle: React.CSSProperties = {
  border: "1px solid var(--stroke)",
  borderRadius: 8,
  background: "rgba(255,255,255,0.56)",
  padding: 12,
  minWidth: 0,
};
const candidateCardHeadStyle: React.CSSProperties = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "flex-start",
  gap: 10,
};
const candidateMetricGridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(4, minmax(0, 1fr))",
  gap: 8,
  marginTop: 10,
};
const blockedReasonStyle: React.CSSProperties = {
  marginTop: 10,
  color: "var(--bad)",
  fontFamily: "monospace",
  fontSize: 11,
  overflowWrap: "anywhere",
};
const contextLineStyle: React.CSSProperties = {
  marginTop: 8,
  color: "var(--muted)",
  fontFamily: "monospace",
  fontSize: 11,
  overflowWrap: "anywhere",
};
const candidateFooterStyle: React.CSSProperties = {
  marginTop: 10,
  display: "flex",
  justifyContent: "space-between",
  gap: 10,
  color: "var(--muted)",
  fontSize: 11,
  overflowWrap: "anywhere",
};
