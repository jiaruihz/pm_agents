/**
 * WeatherStrategiesPage — one card per strategy config.
 * Strategy = config_id. All dimensions live in strategy_config.params JSON.
 */
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { PageFrame } from "../../components/PageFrame";
import { weatherApi } from "../../data/weather-http";
import type { StrategyRow } from "../../data/weather-types";

// ── helpers ────────────────────────────────────────────────────────────────────

function pct(v: number | null, decimals = 1) {
  return v == null ? "—" : `${(v * 100).toFixed(decimals)}%`;
}
function usd(v: number | null | undefined, decimals = 2) {
  if (v == null) return "—";
  const sign = v >= 0 ? "+" : "";
  return `${sign}$${Math.abs(v).toFixed(decimals)}`;
}
function shortName(name: string): string {
  // Strip algorithm prefix, humanize the rest
  const stripped = name
    .replace(/^.*?_mid_price_core_v\d+_/, "")
    .replace(/^.*?_maker_queue_v\d+_/, "");
  return (stripped || name)
    .replace(/_/g, " ")
    .replace(/\$None/g, "—")   // "$None" → "—"
    .replace(/shares —/g, "")  // "shares —" → ""
    .trim();
}

// ── component ──────────────────────────────────────────────────────────────────

export function WeatherStrategiesPage() {
  const [strategies, setStrategies] = useState<StrategyRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    weatherApi
      .listStrategies()
      .then((data) => setStrategies([...data].sort((a, b) => {
        const score = (s: StrategyRow) =>
          (s.params.live_enabled ? 2 : s.params.paper_enabled ? 1 : 0);
        const sd = score(b) - score(a);
        if (sd !== 0) return sd;
        return (b.latest_run_at ?? "").localeCompare(a.latest_run_at ?? "");
      })))
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <PageFrame
      title="Strategies"
      desc="Per-config strategy registry · 策略配置注册表"
    >
      <>
        <div style={topBarStyle}>
          {!loading && (
            <div style={{ color: "var(--muted)", fontSize: 13 }}>
              {strategies.length} strategies · {strategies.filter(s => s.settled_trades > 0).length} with settled trades
            </div>
          )}
        </div>

        {error && <div style={errorStyle}>{error}</div>}

        {loading && !error && (
          <div style={{ color: "var(--muted)", textAlign: "center", padding: 40 }}>
            Loading strategies…
          </div>
        )}

        <div style={gridStyle}>
          {strategies.map((s) => (
            <StrategyCard key={s.config_id} s={s} />
          ))}
        </div>
      </>
    </PageFrame>
  );
}

// ── StrategyCard ───────────────────────────────────────────────────────────────

function StrategyCard({ s }: { s: StrategyRow }) {
  const p = s.params;
  const liveEnabled = p.live_enabled as boolean | undefined;
  const paperEnabled = p.paper_enabled as boolean | undefined;
  const sizingMode = (p.sizing_mode as string | undefined) ?? "—";
  const notional = p.max_order_notional as number | null | undefined;
  const shares = p.fixed_order_shares as number | null | undefined;
  const entryWindow = (p.entry_price_window as string | undefined) ?? "—";
  const minEdge = p.min_edge as number | null | undefined;
  const algVersion = (p.algorithm_version as string | undefined) ?? "—";

  const pnl = s.total_pnl_usd;
  const pnlColor =
    s.total_trades === 0 ? "var(--muted)" : pnl >= 0 ? "var(--ok)" : "var(--bad)";
  const hasActivity = s.total_trades > 0;

  const accentColor = liveEnabled ? "var(--ok)" : paperEnabled ? "var(--accent-2)" : "var(--muted)";

  const hasSettled = s.settled_trades > 0;

  return (
    <Link to={`/weather/strategies/${encodeURIComponent(s.config_id)}`} style={{ textDecoration: "none", color: "inherit" }}>
      <div style={{ ...cardStyle, borderLeft: `3px solid ${accentColor}`, cursor: "pointer", transition: "box-shadow 0.15s" }}
        onMouseEnter={e => (e.currentTarget.style.boxShadow = "0 4px 20px rgba(0,0,0,0.15)")}
        onMouseLeave={e => (e.currentTarget.style.boxShadow = "none")}
      >
        {/* Header */}
        <div style={cardHeaderStyle}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={cardTitleStyle} title={s.name}>
              {shortName(s.name)}
            </div>
            <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2, fontFamily: "monospace" }}>
              {s.config_id.slice(-12)}
            </div>
          </div>
          <div style={{ display: "flex", gap: 5, flexShrink: 0 }}>
            {liveEnabled && <Badge label="LIVE" color="var(--ok)" />}
            {paperEnabled && <Badge label="PAPER" color="var(--accent-2)" />}
            {!liveEnabled && !paperEnabled && <Badge label="INACTIVE" color="var(--muted)" />}
          </div>
        </div>

        {/* Key params grid */}
        <div style={paramsGridStyle}>
          <ParamCell label="Sizing" value={sizingMode} />
          <ParamCell
            label="Notional / Shares"
            value={notional != null ? `$${notional}` : shares != null ? `${shares} sh` : "—"}
          />
          <ParamCell label="Entry Window" value={entryWindow} />
          <ParamCell label="Min Edge" value={minEdge != null ? pct(minEdge, 0) : "—"} />
          <ParamCell label="Algorithm" value={algVersion.replace("_v1", " v1")} />
          <ParamCell label="Exec Policy" value={s.execution_policy ?? "mid_price_core_v1"} />
        </div>

        {/* Divider */}
        <div style={dividerStyle} />

        {/* Stats row */}
        <div style={statsRowStyle}>
          <StatCell label="Runs" value={String(s.num_runs)} />
          <StatCell label="Trades" value={hasActivity ? String(s.total_trades) : "—"} />
          <StatCell label="Settled" value={hasActivity ? String(s.settled_trades) : "—"} />
          <StatCell label="Win%" value={hasSettled ? pct(s.win_rate) : "—"} />
          <StatCell
            label="PnL"
            value={hasSettled ? usd(pnl) : "—"}
            valueColor={hasSettled ? pnlColor : "var(--muted)"}
          />
          <StatCell
            label="ROI"
            value={hasSettled ? pct(s.roi, 2) : "—"}
            valueColor={hasSettled && s.roi != null ? (s.roi >= 0 ? "var(--ok)" : "var(--bad)") : "var(--muted)"}
          />
        </div>

        {/* Footer */}
        <div style={cardFooterStyle}>
          <span style={{ color: "var(--muted)", fontSize: 11 }}>
            Latest: {s.latest_run_at ? s.latest_run_at.slice(0, 10) : "never"}
            {" · "}
            {s.live_run_count} live · {s.paper_run_count} paper
          </span>
          <span style={{ ...linkStyle, fontSize: 12 }}>Details →</span>
        </div>
      </div>
    </Link>
  );
}

// ── sub-components ─────────────────────────────────────────────────────────────

function Badge({ label, color }: { label: string; color: string }) {
  return (
    <span style={{
      fontSize: 10, fontWeight: 700, padding: "2px 7px",
      borderRadius: 4, background: color + "22",
      color, border: `1px solid ${color}55`,
      letterSpacing: "0.04em",
    }}>
      {label}
    </span>
  );
}

function ParamCell({ label, value }: { label: string; value: string }) {
  return (
    <div style={paramCellStyle}>
      <div style={paramLabelStyle}>{label}</div>
      <div style={paramValueStyle}>{value}</div>
    </div>
  );
}

function StatCell({
  label, value, valueColor = "inherit",
}: { label: string; value: string; valueColor?: string }) {
  return (
    <div style={{ textAlign: "center", flex: 1 }}>
      <div style={{ fontSize: 16, fontWeight: 700, color: valueColor, fontVariantNumeric: "tabular-nums" }}>
        {value}
      </div>
      <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 2 }}>{label}</div>
    </div>
  );
}

// ── styles ─────────────────────────────────────────────────────────────────────

const topBarStyle: React.CSSProperties = {
  display: "flex", justifyContent: "space-between", alignItems: "center",
  marginBottom: 20,
};
const errorStyle: React.CSSProperties = {
  color: "var(--bad)", marginBottom: 12, padding: "10px 14px",
  background: "rgba(255,50,50,0.08)", borderRadius: 8,
};
const gridStyle: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fill, minmax(400px, 1fr))",
  gap: 16,
};
const cardStyle: React.CSSProperties = {
  background: "var(--card)",
  border: "1px solid var(--stroke)",
  borderRadius: 12,
  padding: "18px 20px",
  display: "flex",
  flexDirection: "column",
  gap: 14,
};
const cardHeaderStyle: React.CSSProperties = {
  display: "flex", alignItems: "flex-start", gap: 10,
};
const cardTitleStyle: React.CSSProperties = {
  fontSize: 13, fontWeight: 600,
  overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
};
const paramsGridStyle: React.CSSProperties = {
  display: "grid", gridTemplateColumns: "1fr 1fr 1fr",
  gap: "8px 12px",
};
const paramCellStyle: React.CSSProperties = {
  display: "flex", flexDirection: "column", gap: 2,
};
const paramLabelStyle: React.CSSProperties = {
  fontSize: 10, color: "var(--muted)", textTransform: "uppercase",
  letterSpacing: "0.06em",
};
const paramValueStyle: React.CSSProperties = {
  fontSize: 13, fontWeight: 500,
};
const dividerStyle: React.CSSProperties = {
  height: 1, background: "var(--stroke)", margin: "0 -20px",
};
const statsRowStyle: React.CSSProperties = {
  display: "flex", gap: 4,
};
const cardFooterStyle: React.CSSProperties = {
  display: "flex", justifyContent: "space-between", alignItems: "center",
};
const linkStyle: React.CSSProperties = {
  color: "var(--accent-2)", textDecoration: "none", fontSize: 12,
};
